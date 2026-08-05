"""Build the static JSON dataset consumed by the site/ SPA.

Emits into an output directory (default site/public/data, gitignored):
  meta.json          — build info, totals, per-state coverage + health
  national.json      — monthly series, top employers, biggest recent notices
  states/{xx}.json   — per-state series, employers, recent notices, health
  index.json         — columnar arrays over all notices (explorer)
  notices/{pp}.json  — 256 detail shards keyed by dedupe_key prefix

Everything is deterministic (stable ordering, sorted keys) so repeated
builds over the same DB are byte-identical.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from warnlive.registry import Registry
from warnlive.verify.report import build_status

KEY_PREFIX_LEN = 8
TYPES = ["unknown", "mass_layoff", "closure"]

# flags bitmask for index.json
FLAG_TEMPORARY = 1
FLAG_AMENDMENT = 2
FLAG_AMENDED = 4
FLAG_HAS_LINKS = 8
FLAG_PUBLIC = 16  # matched to an SEC CIK


def build_site(conn: sqlite3.Connection, registry: Registry, out_dir: Path) -> dict[str, int]:
    out_dir = Path(out_dir)
    (out_dir / "states").mkdir(parents=True, exist_ok=True)
    (out_dir / "notices").mkdir(parents=True, exist_ok=True)

    # display_date drives all time bucketing/sorting on the site: notices
    # from sources that never published a notice date (CA pre-2014 archive,
    # GA, NJ/PA backfills) fall back to their effective date rather than
    # vanishing from charts and date ranges. The raw fields stay distinct.
    notices = [
        dict(r) | {"display_date": r["notice_date"] or r["effective_date"]}
        for r in conn.execute(
            "SELECT notices.*, "
            "(SELECT v.fields_json FROM notice_versions v "
            " WHERE v.notice_id = notices.id AND v.version = notices.current_version"
            ") AS fields_json "
            "FROM notices ORDER BY state, notice_date, employer_name, dedupe_key"
        )
    ]

    # Derived annotations (same logic as the CSV exports); they flow into
    # the detail shards via dict(n), and CIK presence into FLAG_PUBLIC.
    from warnlive.enrich.annotate import Annotator
    from warnlive.enrich.places import Resolver

    annotator = Annotator()
    annotator.prime(conn)
    resolver = Resolver()
    for n in notices:
        # Kept until the resolver has had it: a state that publishes its city
        # and county in their own columns has already answered the question
        # the location string is being parsed for.
        fields_json = n.pop("fields_json")
        n.update(
            annotator.annotate(n["employer_name"], n["display_date"], fields_json)
        )
        n.update(resolver.resolve(
            n["state"], n.get("location"), fields_json, n["employer_name"]
        ))
    linked_ids = {
        r["notice_id"] for r in conn.execute("SELECT DISTINCT notice_id FROM notice_links")
    } | {
        r["related_id"] for r in conn.execute("SELECT DISTINCT related_id FROM notice_links")
    }

    prefix_len = _choose_prefix_len(notices)
    status = build_status(conn, registry)
    counts: dict[str, int] = {}

    counts["meta.json"] = _write(out_dir / "meta.json", _build_meta(notices, status, prefix_len))
    counts["national.json"] = _write(out_dir / "national.json", _build_national(notices, prefix_len))

    by_state: dict[str, list] = {}
    for n in notices:
        by_state.setdefault(n["state"], []).append(n)
    for cfg in registry.all():
        postal = cfg.postal.upper()
        payload = _build_state(
            postal, by_state.get(postal, []), status.get(postal, {}), cfg, prefix_len
        )
        counts[f"states/{cfg.postal}.json"] = _write(
            out_dir / "states" / f"{cfg.postal}.json", payload
        )

    counts["index.json"] = _write(
        out_dir / "index.json", _build_index(notices, linked_ids, prefix_len)
    )

    counts["employers/* (256 shards)"] = _build_employer_shards(
        notices, out_dir / "employers", prefix_len
    )
    counts["employers/index.json"] = _write(
        out_dir / "employers" / "index.json", _build_employer_index(notices)
    )

    shard_counts = _build_detail_shards(conn, notices, out_dir / "notices", prefix_len)
    counts["notices/* (256 shards)"] = shard_counts
    return counts


def _choose_prefix_len(notices) -> int:
    """Shortest prefix (>= KEY_PREFIX_LEN) that uniquely identifies every notice."""
    length = KEY_PREFIX_LEN
    keys = [n["dedupe_key"] for n in notices]
    while length <= 40:
        prefixes = {k[:length] for k in keys}
        if len(prefixes) == len(keys):
            return length
        length += 2
    raise ValueError("dedupe_key prefixes cannot be made unique")


def _month(d: str | None) -> str | None:
    return d[:7] if d and len(d) >= 7 else None


def _monthly_series(rows) -> list[dict]:
    months: dict[str, dict] = {}
    for n in rows:
        m = _month(n["display_date"])
        if m is None:
            continue
        entry = months.setdefault(
            m, {"month": m, "notices": 0, "workers": 0,
                "by_type": {"closure": 0, "mass_layoff": 0, "unknown": 0}}
        )
        entry["notices"] += 1
        entry["workers"] += n["employees_affected"] or 0
        # NULL or an unforeseen value counts as unknown rather than crashing
        # the whole site build: the schema allows NULL, and rows written by
        # paths that bypass the normalizer have no closed-set guarantee.
        bucket = n["layoff_type"] if n["layoff_type"] in entry["by_type"] else "unknown"
        entry["by_type"][bucket] += 1
    return [months[m] for m in sorted(months)]


def _notice_summary(n, prefix_len: int) -> dict:
    return {
        "key": n["dedupe_key"][:prefix_len],
        "state": n["state"],
        "employer": n["employer_name"],
        "location": n["location"],
        "notice_date": n["notice_date"],
        "effective_date": n["effective_date"],
        "jobs": n["employees_affected"],
        "type": n["layoff_type"],
    }


def _county_series(rows, limit: int | None = None) -> list[dict]:
    """Notices and workers per county, for the maps and county tables.

    Notices whose location did not resolve are simply absent — a county map
    can only show what could be placed, and the share that could not is
    reported separately rather than folded in as a zero.
    """
    agg: dict[str, dict] = {}
    for n in rows:
        fips = n.get("county_fips")
        if not fips:
            continue
        entry = agg.setdefault(
            fips,
            {"fips": fips, "county": n["county_name"], "state": n["state"],
             "notices": 0, "workers": 0},
        )
        entry["notices"] += 1
        entry["workers"] += n["employees_affected"] or 0
    ranked = sorted(agg.values(), key=lambda e: -e["workers"])
    return ranked[:limit] if limit else ranked


def _top_employers(rows, since: str | None, limit: int) -> list[dict]:
    """Aggregate by identity key (not raw spelling), labeled with the
    group's most common raw name."""
    agg: dict[str, dict] = {}
    for n in rows:
        if since and (n["display_date"] or "") < since:
            continue
        e = agg.setdefault(
            n["employer_key"],
            {"key": n["employer_key"], "names": {}, "notices": 0, "workers": 0},
        )
        e["names"][n["employer_name"]] = e["names"].get(n["employer_name"], 0) + 1
        e["notices"] += 1
        e["workers"] += n["employees_affected"] or 0
    out = []
    for e in agg.values():
        label = max(sorted(e["names"]), key=lambda k: e["names"][k])
        out.append({"employer": label, "key": e["key"],
                    "notices": e["notices"], "workers": e["workers"]})
    out.sort(key=lambda e: (-e["workers"], -e["notices"], e["employer"]))
    return out[:limit]


def _today(notices) -> str:
    """Anchor 'trailing N months' windows to the newest notice date, clamped
    to the build date — a handful of source typos carry far-future notice
    dates and would otherwise drag every trailing window into the future."""
    build_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dates = [n["display_date"] for n in notices if n["display_date"] and n["display_date"] <= build_date]
    return max(dates) if dates else "1970-01-01"


def _shift_months(iso_date: str, months_back: int) -> str:
    y, m = int(iso_date[:4]), int(iso_date[5:7])
    total = y * 12 + (m - 1) - months_back
    return f"{total // 12:04d}-{total % 12 + 1:02d}-01"


def _build_meta(notices, status: dict, prefix_len: int) -> dict:
    dates = [n["display_date"] for n in notices if n["display_date"]]

    # Per-state completeness, so the site can say what is missing rather
    # than only what is present: how far back a state's history reaches,
    # and how much of it arrived without a date, a headcount or a location.
    per_state: dict[str, dict] = {}
    for n in notices:
        e = per_state.setdefault(
            n["state"],
            {"first": None, "last": None, "undated": 0, "no_jobs": 0,
             "no_location": 0, "archived": 0, "identified": 0, "placed": 0},
        )
        d = n["display_date"]
        if d:
            e["first"] = min(e["first"] or d, d)
            e["last"] = max(e["last"] or d, d)
        if not n["notice_date"]:
            e["undated"] += 1
        if n["employees_affected"] is None:
            e["no_jobs"] += 1
        if not n["location"]:
            e["no_location"] += 1
        if "web.archive.org" in (n["source_url"] or ""):
            e["archived"] += 1
        if n["cik"] or n["ein"] or n["lei"] or n["wikidata_qid"]:
            e["identified"] += 1
        if n.get("county_fips"):
            e["placed"] += 1

    def quality(rows: dict) -> dict:
        return {k: rows[k] for k in
                ("first", "last", "undated", "no_jobs", "no_location",
                 "archived", "identified", "placed")}

    return {
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "key_prefix_len": prefix_len,
        "totals": {
            "notices": len(notices),
            "workers": sum(n["employees_affected"] or 0 for n in notices),
            "states": len({n["state"] for n in notices}),
            "undated": sum(1 for n in notices if not n["notice_date"]),
            "no_jobs": sum(1 for n in notices if n["employees_affected"] is None),
            "no_location": sum(1 for n in notices if not n["location"]),
            "archived": sum(
                1 for n in notices if "web.archive.org" in (n["source_url"] or "")
            ),
            "identified": sum(
                1 for n in notices
                if n["cik"] or n["ein"] or n["lei"] or n["wikidata_qid"]
            ),
            "with_industry": sum(1 for n in notices if n["naics"]),
            "placed": sum(1 for n in notices if n.get("county_fips")),
        },
        "date_range": {"min": min(dates), "max": max(dates)} if dates else None,
        "states": {
            postal: {
                "name": s["name"],
                "status": s["registry_status"],
                "notices": s["notices"],
                "latest_verdict": s["latest_verdict"],
                "last_success": s["last_success"],
                "source": s.get("source"),
                **quality(per_state.get(postal, {
                    "first": None, "last": None, "undated": 0, "no_jobs": 0,
                    "no_location": 0, "archived": 0, "identified": 0,
                    "placed": 0})),
            }
            for postal, s in sorted(status.items())
        },
    }


def _sector_series(rows) -> list[dict]:
    """Notices and workers per NAICS sector, biggest first. Notices whose
    industry is unknown are counted under a null sector rather than
    dropped — the site should never imply the mix is fully known."""
    from warnlive.enrich.industry import SECTOR_LABELS, sector_of

    agg: dict[str | None, dict] = {}
    for n in rows:
        code = sector_of(n["naics"])
        e = agg.setdefault(
            code,
            {"sector": code, "label": SECTOR_LABELS.get(code, "Industry not recorded"),
             "notices": 0, "workers": 0},
        )
        e["notices"] += 1
        e["workers"] += n["employees_affected"] or 0
    return sorted(
        agg.values(), key=lambda e: (e["sector"] is None, -e["workers"], -e["notices"])
    )


def _build_national(notices, prefix_len: int) -> dict:
    anchor = _today(notices)
    t12 = _shift_months(anchor, 12)
    recent_cut = _shift_months(anchor, 3)
    dated = [n for n in notices if n["display_date"]]

    biggest_recent = sorted(
        (n for n in dated if recent_cut <= n["display_date"] <= anchor),
        key=lambda n: -(n["employees_affected"] or 0),
    )[:50]

    state_agg: dict[str, dict] = {}
    for n in dated:
        if not (t12 <= n["display_date"] <= anchor):
            continue
        e = state_agg.setdefault(n["state"], {"state": n["state"], "notices": 0, "workers": 0})
        e["notices"] += 1
        e["workers"] += n["employees_affected"] or 0

    in_window = [n for n in dated if t12 <= n["display_date"] <= anchor]
    prior = [
        n for n in dated
        if _shift_months(anchor, 24) <= n["display_date"] < t12
    ]
    return {
        "anchor_date": anchor,
        "monthly": _monthly_series(notices),
        "top_employers_12mo": _top_employers(dated, t12, 25),
        "biggest_recent": [_notice_summary(n, prefix_len) for n in biggest_recent],
        "states_12mo": sorted(state_agg.values(), key=lambda e: -e["workers"]),
        "counties_12mo": _county_series(in_window),
        "placed_12mo": sum(1 for n in in_window if n.get("county_fips")),
        "sectors_12mo": _sector_series(in_window),
        # The comparable window a year earlier, so the site can say whether
        # the current one is unusual rather than only how big it is.
        "prior_12mo": {
            "notices": len(prior),
            "workers": sum(n["employees_affected"] or 0 for n in prior),
            "sectors": _sector_series(prior),
        },
    }


def _build_state(postal: str, rows, health: dict, cfg, prefix_len: int) -> dict:
    dated = [n for n in rows if n["display_date"]]
    anchor = _today(rows)
    recent = sorted(dated, key=lambda n: n["display_date"], reverse=True)[:50]
    return {
        "state": postal,
        "name": cfg.name,
        "source": {
            "kind": cfg.source,
            "status": cfg.status,
            "url": cfg.source_url,
            "cadence": cfg.cadence,
            "notes": cfg.notes,
        },
        "health": {
            "latest_verdict": health.get("latest_verdict"),
            "latest_run": health.get("latest_run"),
            "latest_error": health.get("latest_error"),
            "last_success": health.get("last_success"),
            "consecutive_failures": health.get("consecutive_failures", 0),
        },
        "coverage": {
            "notices": len(rows),
            "earliest": min((n["display_date"] for n in dated), default=None),
            "latest": max((n["display_date"] for n in dated), default=None),
            # How much of this state could be put on a map, so a county view
            # can say what it is leaving out. Kansas files against workforce
            # areas, so its answer is close to none.
            "placed": sum(1 for n in rows if n.get("county_fips")),
        },
        "counties": _county_series(rows),
        "monthly": _monthly_series(rows),
        "top_employers": _top_employers(dated, None, 20),
        "top_employers_24mo": _top_employers(dated, _shift_months(anchor, 24), 20),
        "recent": [_notice_summary(n, prefix_len) for n in recent],
    }


def _build_index(notices, linked_ids: set, prefix_len: int) -> dict:
    from warnlive.enrich.industry import SECTOR_LABELS, sector_of

    states = sorted({n["state"] for n in notices})
    state_idx = {s: i for i, s in enumerate(states)}
    type_idx = {t: i for i, t in enumerate(TYPES)}
    # Sectors ride as an index into a 20-entry table rather than as codes:
    # one small integer per notice instead of a repeated string.
    sectors = list(SECTOR_LABELS)
    sector_idx = {code: i for i, code in enumerate(sectors)}

    # Counties ride as an index into a table of their own, the way sectors
    # do: one small integer per notice rather than a repeated FIPS string
    # and name across ninety thousand rows.
    counties = sorted(
        {
            (n["county_fips"], n["county_name"], n["state"])
            for n in notices if n.get("county_fips")
        },
        key=lambda c: (c[2], c[1]),
    )
    county_idx = {fips: i for i, (fips, _, _) in enumerate(counties)}

    cols: dict[str, list] = {
        "key": [], "state": [], "date": [], "effective": [], "employer": [],
        "location": [], "jobs": [], "type": [], "flags": [], "sector": [],
        "county": [],
    }
    for n in notices:
        flags = (
            (FLAG_TEMPORARY if n["is_temporary"] else 0)
            | (FLAG_AMENDMENT if n["is_amendment"] else 0)
            | (FLAG_AMENDED if n["is_amended"] else 0)
            | (FLAG_HAS_LINKS if n["id"] in linked_ids else 0)
            | (FLAG_PUBLIC if n["cik"] else 0)
        )
        cols["key"].append(n["dedupe_key"][:prefix_len])
        cols["state"].append(state_idx[n["state"]])
        cols["date"].append(n["display_date"])
        cols["effective"].append(n["effective_date"])
        cols["employer"].append(n["employer_name"])
        cols["location"].append(n["location"])
        cols["jobs"].append(n["employees_affected"])
        cols["type"].append(type_idx.get(n["layoff_type"], type_idx["unknown"]))
        cols["flags"].append(flags)
        sector = sector_of(n["naics"])
        cols["sector"].append(sector_idx[sector] if sector else -1)
        cols["county"].append(county_idx.get(n.get("county_fips"), -1))
    return {
        "states": states,
        "types": TYPES,
        "sectors": [{"code": c, "label": SECTOR_LABELS[c]} for c in sectors],
        "counties": [
            {"fips": fips, "name": name, "state": state}
            for fips, name, state in counties
        ],
        "count": len(notices),
        "columns": cols,
    }


# An employer earns a directory entry by filing more than once or by
# affecting a meaningful number of workers. Single small filings are the
# long tail — 47,000 of them — and listing every one would make the
# directory unreadable and its payload enormous.
DIRECTORY_MIN_NOTICES = 2
DIRECTORY_MIN_WORKERS = 250


def _build_employer_index(notices) -> dict:
    """A browsable directory of employers, with their corporate parents."""
    from warnlive.enrich.industry import SECTOR_LABELS, sector_of

    agg: dict[str, dict] = {}
    for n in notices:
        e = agg.setdefault(
            n["employer_key"],
            {"key": n["employer_key"], "names": {}, "canonical": None,
             "notices": 0, "workers": 0, "states": set(), "sector": None,
             "parent": None, "identified": False, "first": None, "last": None},
        )
        e["names"][n["employer_name"]] = e["names"].get(n["employer_name"], 0) + 1
        e["canonical"] = e["canonical"] or n["canonical_name"]
        e["notices"] += 1
        e["workers"] += n["employees_affected"] or 0
        e["states"].add(n["state"])
        e["sector"] = e["sector"] or sector_of(n["naics"])
        e["parent"] = e["parent"] or n["parent_company"]
        e["identified"] = e["identified"] or bool(
            n["cik"] or n["ein"] or n["lei"] or n["wikidata_qid"]
        )
        d = n["display_date"]
        if d:
            e["first"] = min(e["first"] or d, d)
            e["last"] = max(e["last"] or d, d)

    rows = []
    for e in agg.values():
        if e["notices"] < DIRECTORY_MIN_NOTICES and e["workers"] < DIRECTORY_MIN_WORKERS:
            continue
        rows.append((
            e["key"],
            e["canonical"] or max(sorted(e["names"]), key=lambda k: e["names"][k]),
            e["notices"],
            e["workers"],
            sorted(e["states"]),
            e["sector"],
            e["parent"],
            1 if e["identified"] else 0,
            e["first"],
            e["last"],
        ))
    rows.sort(key=lambda r: (-r[3], -r[2], r[1]))
    # Columnar, like the notice index: 13,000 rows of repeated JSON field
    # names cost more than the values themselves.
    fields = ["key", "label", "notices", "workers", "states", "sector",
              "parent", "identified", "first_date", "last_date"]
    return {
        "sectors": [{"code": c, "label": SECTOR_LABELS[c]} for c in SECTOR_LABELS],
        "columns": {name: [r[i] for r in rows] for i, name in enumerate(fields)},
        "total_employers": len(agg),
        "listed": len(rows),
        "min_notices": DIRECTORY_MIN_NOTICES,
        "min_workers": DIRECTORY_MIN_WORKERS,
    }


def _fnv_shard(key: str) -> str:
    """FNV-1a low byte as 2-hex shard name; mirrored in the SPA's dataClient
    so pages can locate an employer's shard without an index fetch."""
    h = 2166136261
    for b in key.encode("utf-8"):
        h = ((h ^ b) * 16777619) & 0xFFFFFFFF
    return f"{h & 0xFF:02x}"


def _build_employer_shards(notices, out_dir: Path, prefix_len: int) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    groups: dict[str, list] = {}
    for n in notices:
        groups.setdefault(n["employer_key"], []).append(n)

    shards: dict[str, dict] = {}
    for key, rows in groups.items():
        names: dict[str, int] = {}
        for n in rows:
            names[n["employer_name"]] = names.get(n["employer_name"], 0) + 1
        first = rows[0]  # enrichment fields identical across the group's key
        # Prefer the name the identity carries ("United Airlines") over the
        # commonest thing states typed ("UNITED"); the raw spellings all
        # survive as aliases.
        label = first["canonical_name"] or max(sorted(names), key=lambda k: names[k])
        dated = sorted((n["display_date"] for n in rows if n["display_date"]))
        summaries = sorted(
            (_notice_summary(n, prefix_len) for n in rows),
            key=lambda s: (s["notice_date"] or s["effective_date"] or "", s["key"]),
            reverse=True,
        )
        shards.setdefault(_fnv_shard(key), {})[key] = {
            "key": key,
            "label": label,
            "aliases": sorted(k for k in names if k != label)[:12],
            "canonical_name": first["canonical_name"],
            "cik": first["cik"],
            "ticker": first["ticker"],
            "ein": first["ein"],
            "lei": first["lei"],
            "wikidata_qid": first["wikidata_qid"],
            "parent_company": first["parent_company"],
            "sic_description": first["sic_description"],
            "totals": {
                "notices": len(rows),
                "workers": sum(n["employees_affected"] or 0 for n in rows),
                "states": sorted({n["state"] for n in rows}),
            },
            "first_date": dated[0] if dated else None,
            "last_date": dated[-1] if dated else None,
            "notices": summaries,
        }

    total = 0
    for pp in [f"{i:02x}" for i in range(256)]:
        total += _write(out_dir / f"{pp}.json", shards.get(pp, {}))
    return total


def _build_detail_shards(conn, notices, out_dir: Path, prefix_len: int) -> int:
    key_by_id = {n["id"]: n["dedupe_key"] for n in notices}
    summary_by_id = {n["id"]: n for n in notices}

    versions: dict[int, list] = {}
    for v in conn.execute(
        "SELECT notice_id, version, fields_json, observed_at FROM notice_versions "
        "ORDER BY notice_id, version"
    ):
        versions.setdefault(v["notice_id"], []).append(
            {"version": v["version"], "observed_at": v["observed_at"],
             "fields": json.loads(v["fields_json"])}
        )

    links: dict[int, list] = {}
    for l in conn.execute(
        "SELECT notice_id, related_id, kind, score, method, detail FROM notice_links "
        "ORDER BY notice_id, related_id, kind"
    ):
        for src, other, direction in (
            (l["notice_id"], l["related_id"], "to"),
            (l["related_id"], l["notice_id"], "from"),
        ):
            o = summary_by_id.get(other)
            if o is None or src not in key_by_id:
                continue
            links.setdefault(src, []).append(
                {
                    "direction": direction,
                    "kind": l["kind"],
                    "score": l["score"],
                    "method": l["method"],
                    "detail": l["detail"],
                    "related": _notice_summary(o, prefix_len),
                }
            )

    shards: dict[str, dict] = {}
    for n in notices:
        rec = dict(n)
        rec.pop("id")
        rec["key"] = n["dedupe_key"][:prefix_len]
        rec["versions"] = versions.get(n["id"], [])
        rec["links"] = links.get(n["id"], [])
        shards.setdefault(n["dedupe_key"][:2], {})[n["dedupe_key"]] = rec

    total = 0
    for pp in [f"{i:02x}" for i in range(256)]:
        total += _write(out_dir / f"{pp}.json", shards.get(pp, {}))
    return total


def _write(path: Path, payload) -> int:
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False) + "\n")
    return path.stat().st_size
