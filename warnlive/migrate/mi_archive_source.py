"""Michigan WARN listings archived from the agency's former web pages.

Input: ``agency/mi_archive`` in a source bundle (pinned in
``data/source_snapshots/mi/wayback-2026-09-30``): Wayback Machine captures of
the Michigan Workforce Development WARN pages on the old michigan.gov site,
year pages 2014-2020 and three main-page captures (2020-07, 2020-11,
2022-01), plus a 2021 year page. The 2014-2020 pages list each notice under a
month heading with its day; the 2021 and 2022 captures embed the listing as
``window.contentPieces`` JSON, with a year category but no day. Each listing
links the notice PDF, whose michigan.gov document id is the identity.

Output: ``project`` returns admitted notice records, held rows (for the
exception ledger) and a report. The listed day is the agency's posting of the
notice on its page, not a legal notice date: it is kept as
``agency_posted_date`` and no canonical notice or action date is set. No
listing for 2022 (after January) through 2023 was archived; that gap stays
visible. No network access.

Dates outside the live transformer's window (``archive_dates``) are blanked
and reported under ``implausible_dates_blanked``.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup

from warnlive.migrate import archive_dates
from warnlive.normalize.engine import _record_hash

PREFIX = "agency/mi_archive"
FORMAT = "mi-wayback-archive-v1"
CAPTURES = {
    "year-2014-20200614044757.html": "c8c1f95fa1299bc18bfe01eec4834b2e93be168ea8bd779307fb6741cb7b07d3",
    "year-2015-20200614005221.html": "faa8288b993fee5346ac95d6b369130953c7d8ce84bb69063d4b1f6bf57aa442",
    "year-2016-20200614041252.html": "d0beb2c930a4f8e8b3125acfede0ef3c6b0129d74d5242353c2823bb250a44db",
    "year-2017-20200614041955.html": "7a55f28e5de6f34adcfa142dd415408c160497f1fe6095e15acaacad140aa250",
    "year-2018-20200614085221.html": "40737e3ada38af25a30d056f344828df6d60260819a1b7eb54c6fdc2e30caf8c",
    "year-2019-20200614055705.html": "ccea08a3a1e7ee8425348fa5459ce38044750afbe156c563119f4f1c582ac2b6",
    "year-2020-20200614042117.html": "17eed1f1f91afebaea01c87eaa506cf3cfe2596fac7df8ac330478dbd02f741f",
    "main-20200720071336.html": "87c8e2a0812bff4b81ffaedd143930d4083afc8d1cba5e4577a8d0c308025d68",
    "main-20201127011332.html": "2904dfa1ab1b3dcf98f74f020de91ea236687b5998e2b92604978e420fa07f19",
    "year-2021-20210625222531.html": "5730ee432a7a0c94e13694ce83a39187263263d90ccd2d3019bd56433ec6ff20",
    "main-20220129040313.html": "d9a7a34e86812c68ddfad98179834eb31cfb5c0132e432a476e2b5655cfc64da",
}
MONTHS = ("January February March April May June July August September October "
          "November December").split()
DOCUMENT = re.compile(r"_(\d{5,})_7\.[A-Za-z]+$")
WORKERS = re.compile(r"Numbers? (?:of )?Aff[a-z]*\s*[:;]?\s*([\d,]+)\b", re.I)
# A listing whose title or PDF name declares it an update or revision of
# another notice; the page never says which listing it revises.
AMENDMENT = re.compile(r"updat|revis|amend", re.I)


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _norm(value: str) -> str:
    return " ".join(html.unescape(str(value or "")).replace("\xa0", " ").split())


def _capture_ts(name: str) -> str:
    return name.rsplit("-", 1)[-1].removesuffix(".html")


def _html_listings(content: bytes) -> list[dict]:
    """Dated listings of an old-format page, in document order."""
    soup = BeautifulSoup(content, "html.parser")
    month = year = None
    listings = []
    for element in soup.find_all(["a", "div"]):
        if element.name == "a" and element.get("name"):
            match = re.fullmatch(r"([A-Za-z]+)(\d{4})", element["name"])
            if match and match.group(1) in MONTHS:
                month, year = MONTHS.index(match.group(1)) + 1, int(match.group(2))
            continue
        if element.name != "div" or "indexRow" not in (element.get("class") or []):
            continue
        link = element.select_one(".indexTitle a")
        day = element.select_one(".meDate")
        desc = element.select_one(".shortdesc")
        listings.append({
            "title": _norm(link.get_text(" ") if link else element.get_text(" ")),
            "description": _norm(desc.get_text(" ") if desc else (link.get("title") if link else "")),
            "href": (link.get("href") or "").strip() if link else "",
            "year": year, "month": month,
            "day_text": _norm(day.get_text(" ")) if day else None,
            "year_categories": None,
        })
    return listings


def _json_listings(content: bytes) -> list[dict]:
    """The ``window.contentPieces`` listing with its year categories."""
    text = content.decode("utf-8")
    filters = re.search(r"window\.filters\s*=\s*(\[.*?\]);\s*window\.contentPieces", text, re.S)
    pieces = re.search(r"window\.contentPieces\s*=\s*(\[.*?\]);\s*</script>", text, re.S)
    if not filters or not pieces:
        raise ValueError("Michigan listing JSON not found")
    # The page escapes apostrophes as \' which JSON does not allow.
    items = json.loads(re.sub(r"\\(?![\"\\/bfnrtu])", r"\\\\", pieces.group(1)), strict=False)
    years = {item["id"]: item["name"] for group in json.loads(filters.group(1))
             if group["type"] == "Year" for item in group["filters"]}
    return [{
        "title": _norm(item.get("title")), "description": _norm(item.get("description")),
        "href": (item.get("url") or "").strip(), "year": None, "month": None, "day_text": None,
        "year_categories": sorted({years[c] for c in item.get("categories", []) if c in years}),
        "content_piece_id": item.get("id"),
    } for item in items]


def read_artifacts(directory: Path) -> tuple[list[dict], dict]:
    """Verify every capture and return one observation per distinct listing.

    A listing repeated with the same text in several captures is one source
    row; the captures that show it are recorded with it.
    """
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    specs = {item.get("file"): item for item in manifest.get("artifacts") or []}
    if manifest.get("format") != FORMAT or set(specs) != set(CAPTURES):
        raise ValueError("unsupported Michigan archive manifest")
    observations: dict[str, dict] = {}
    for name in sorted(CAPTURES, key=_capture_ts):
        spec = specs[name]
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"invalid Michigan archive artifact: {name}")
        content = path.read_bytes()
        if (len(content) != spec.get("bytes") or spec.get("sha256") != CAPTURES[name]
                or hashlib.sha256(content).hexdigest() != CAPTURES[name]):
            raise ValueError(f"Michigan archive checksum mismatch: {name}")
        listings = _json_listings(content) if b"window.contentPieces" in content else _html_listings(content)
        if len(listings) != spec.get("data_rows"):
            raise ValueError(f"Michigan archive listing count changed: {name}")
        for listing in listings:
            listing.pop("content_piece_id", None)
            text = _json(listing)
            digest = hashlib.sha256(text.encode()).hexdigest()
            entry = observations.setdefault(digest, {
                "listing": listing, "captures": [],
                "source_artifact": f"{PREFIX}/{name}",
                "source_row": f"{PREFIX}/listing:sha256:{digest}",
                "source_row_sha256": digest, "source_url": spec["wayback_url"],
            })
            entry["captures"].append(_capture_ts(name))
    rows = sorted(observations.values(), key=lambda row: (row["captures"][0], row["source_row"]))
    if len(rows) != manifest.get("distinct_listings"):
        raise ValueError("Michigan distinct listing count changed")
    return rows, manifest


def _fields(listing: dict) -> dict:
    description = listing["description"]
    match = WORKERS.search(description)
    workers = int(match.group(1).replace(",", "")) if match else None
    city = re.search(r"\bCit(?:y|ies):\s*(.*?)(?:[,;]?\s*Count(?:y|ies)(?: Name)?:|,?\s*Number|$)",
                     description)
    county = re.search(r"\bCount(?:y|ies)(?: Name)?:\s*(.*?)(?:[,;]?\s*Number|$)", description)
    action = re.match(r"\s*([A-Za-z ]+?)\s*-", description)
    posted = None
    if listing["year"] and listing["month"] and (listing["day_text"] or "").isdigit():
        try:
            posted = date(listing["year"], listing["month"], int(listing["day_text"])).isoformat()
        except ValueError:
            posted = None
    document = DOCUMENT.search(listing["href"])
    return {"workers": workers if workers else None,
            "city": city.group(1).strip(" ,;") if city else None,
            "county": county.group(1).strip(" ,;") if county else None,
            "action": action.group(1).strip() if action else None,
            "posted": posted, "document_id": document.group(1) if document else None}


def _employer_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def project(directory: Path, existing_events: set[tuple[str, int]] | None = None
            ) -> tuple[list[dict], list[dict], dict]:
    """Admit one notice per archived document; hold repeats and ambiguity.

    ``existing_events`` are (employer key, workers) of Michigan notices
    already in the build; a listing matching one is held rather than admitted
    twice. The dated listing from the latest capture is a document's current
    listing; other listing texts of the same document are held.
    """
    rows, _ = read_artifacts(directory)
    existing_events = set(existing_events or ())
    by_document: dict[str, list[dict]] = {}
    for row in rows:
        row["fields"] = _fields(row["listing"])
        if row["fields"]["document_id"]:
            by_document.setdefault(row["fields"]["document_id"], []).append(row)
    conflicting = {
        doc for doc, group in by_document.items()
        if len({_employer_key(r["listing"]["title"]) for r in group}) > 1
        and len({r["fields"]["workers"] for r in group}) > 1
    }
    records, held = [], []
    for row in rows:
        listing, f = row["listing"], row["fields"]
        doc = f["document_id"]
        year = (f["posted"] or "")[:4] or (str(listing["year"]) if listing["year"] else None) or (
            listing["year_categories"][0] if listing["year_categories"] else None)
        group = by_document.get(doc, [row])
        preferred = max(group, key=lambda r: (r["fields"]["posted"] is not None, r["captures"][-1],
                                              r["captures"][0]))
        text = " ".join([listing["title"], listing["href"]])
        if not doc:
            reason = "no_notice_document_link"
        elif doc in conflicting:
            reason = "document_listed_as_different_events"
        elif preferred is not row:
            reason = "other_capture_listing_of_document"
        elif AMENDMENT.search(text):
            reason = "amendment_listing_parent_unresolved"
        elif not listing["title"]:
            reason = "missing_employer"
        elif (_employer_key(listing["title"]), f["workers"]) in existing_events:
            reason = "possible_overlap_with_current_mi_listing"
        else:
            reason = None
        raw = {**listing, "captures": row["captures"]}
        if reason:
            item = {"origin": row["source_artifact"], "state": "MI", "reason": reason,
                    "source_row": row["source_row"], "source_row_sha256": row["source_row_sha256"],
                    "source_notice_id": doc, "source_url": row["source_url"],
                    "notice_year": year, "raw_extra": _json(raw)}
            if reason == "other_capture_listing_of_document":
                item["related_source_row"] = preferred["source_row"]
            held.append(item)
            continue
        action = (f["action"] or "").casefold()
        identity = f"MI:document:{doc}"
        details = {
            "origin": row["source_artifact"], "source_row": row["source_row"],
            "source_row_sha256": row["source_row_sha256"],
            "identity_basis": "agency_notice_document_id",
            "wayback_captures": row["captures"],
            "agency_posted_date": f["posted"],
            "agency_listing_year": year,
            "date_roles": {"listing day under month heading": "agency_posting"},
            "notice_document_url": "https://www.michigan.gov" + listing["href"]
            if listing["href"].startswith("/") else listing["href"],
            "listing_description": listing["description"],
            "city_text": f["city"], "county_text": f["county"],
            "layoff_type_evidence": {"rule": "mi_archive_listing_action_v1",
                                     "source_text": f["action"]} if f["action"] else None,
        }
        rec = {
            "state": "MI", "employer_name": listing["title"],
            "location": f["city"] or f["county"], "notice_date": None, "effective_date": None,
            "employees_affected": f["workers"],
            "layoff_type": ("closure" if action.startswith(("closure", "closing")) else
                            "mass_layoff" if action.startswith("layoff") else "unknown"),
            "is_temporary": None, "is_amendment": 0,
            "source_url": row["source_url"], "source_notice_id": doc,
            "source_identity": identity,
            "source_details": _json({k: v for k, v in details.items() if v is not None}),
            "raw_extra": _json(raw),
            "dedupe_key": hashlib.sha1(f"MI|archive|{identity}".encode()).hexdigest(),
        }
        rec["raw_record_hash"] = _record_hash(rec)
        records.append(rec)
    if len(records) + len(held) != len(rows) or len({r["dedupe_key"] for r in records}) != len(records):
        raise ValueError("Michigan archive row accounting mismatch")
    blanked = archive_dates.apply(records, "MI", directory)
    return records, held, {
        "implausible_dates_blanked": blanked,
        "source_rows": len(rows), "admitted": len(records), "held": len(held),
        "hold_reasons": dict(sorted(Counter(item["reason"] for item in held).items())),
        "admitted_workers": sum(r["employees_affected"] or 0 for r in records),
        "admitted_missing_workers": sum(r["employees_affected"] is None for r in records),
        "admitted_with_posted_day": sum('"agency_posted_date"' in r["source_details"] for r in records),
    }
