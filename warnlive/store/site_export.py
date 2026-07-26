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
    from warnlive.enrich.edgar import REFERENCE_PATH, Matcher, load_sic
    from warnlive.enrich.industry import industry_from_fields_json, load_sic_naics

    from warnlive.enrich.wikidata import load_orgs

    matcher = Matcher() if REFERENCE_PATH.exists() else None
    sic_by_cik = load_sic()
    naics_by_sic = load_sic_naics()
    wikidata_by_cik = load_orgs()
    for n in notices:
        n["cik"] = n["ticker"] = n["cik_match"] = None
        n["sic"] = n["sic_description"] = None
        n["wikidata_qid"] = n["parent_company"] = None
        n["industry"], n["naics"], n["naics_basis"] = industry_from_fields_json(
            n.pop("fields_json")
        )
        if matcher is not None:
            d = n["display_date"]
            hit = matcher.match(n["employer_name"], int(d[:4]) if d else None)
            if hit:
                n["cik"], n["ticker"], n["cik_match"] = hit[0], hit[1] or None, hit[2]
                sic = sic_by_cik.get(n["cik"])
                if sic:
                    n["sic"], n["sic_description"] = sic[0] or None, sic[1] or None
                wd = wikidata_by_cik.get(n["cik"])
                if wd:
                    n["wikidata_qid"] = wd["qid"]
                    n["parent_company"] = (
                        wd["parents"].split("||")[0] if wd["parents"] else None
                    )
        if n["naics"] is None and n["sic"] in naics_by_sic:
            n["naics"], n["naics_basis"] = naics_by_sic[n["sic"]], "sic-crosswalk"
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
        entry["by_type"][n["layoff_type"]] += 1
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


def _top_employers(rows, since: str | None, limit: int) -> list[dict]:
    agg: dict[str, dict] = {}
    for n in rows:
        if since and (n["display_date"] or "") < since:
            continue
        name = n["employer_name"]
        e = agg.setdefault(name, {"employer": name, "notices": 0, "workers": 0})
        e["notices"] += 1
        e["workers"] += n["employees_affected"] or 0
    ranked = sorted(agg.values(), key=lambda e: (-e["workers"], -e["notices"], e["employer"]))
    return ranked[:limit]


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
    return {
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "key_prefix_len": prefix_len,
        "totals": {
            "notices": len(notices),
            "workers": sum(n["employees_affected"] or 0 for n in notices),
            "states": len({n["state"] for n in notices}),
        },
        "date_range": {"min": min(dates), "max": max(dates)} if dates else None,
        "states": {
            postal: {
                "name": s["name"],
                "status": s["registry_status"],
                "notices": s["notices"],
                "latest_verdict": s["latest_verdict"],
                "last_success": s["last_success"],
            }
            for postal, s in sorted(status.items())
        },
    }


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

    return {
        "anchor_date": anchor,
        "monthly": _monthly_series(notices),
        "top_employers_12mo": _top_employers(dated, t12, 25),
        "biggest_recent": [_notice_summary(n, prefix_len) for n in biggest_recent],
        "states_12mo": sorted(state_agg.values(), key=lambda e: -e["workers"]),
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
        },
        "monthly": _monthly_series(rows),
        "top_employers": _top_employers(dated, None, 20),
        "top_employers_24mo": _top_employers(dated, _shift_months(anchor, 24), 20),
        "recent": [_notice_summary(n, prefix_len) for n in recent],
    }


def _build_index(notices, linked_ids: set, prefix_len: int) -> dict:
    states = sorted({n["state"] for n in notices})
    state_idx = {s: i for i, s in enumerate(states)}
    type_idx = {t: i for i, t in enumerate(TYPES)}

    cols: dict[str, list] = {
        "key": [], "state": [], "date": [], "employer": [],
        "location": [], "jobs": [], "type": [], "flags": [],
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
        cols["employer"].append(n["employer_name"])
        cols["location"].append(n["location"])
        cols["jobs"].append(n["employees_affected"])
        cols["type"].append(type_idx[n["layoff_type"]])
        cols["flags"].append(flags)
    return {"states": states, "types": TYPES, "count": len(notices), "columns": cols}


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
