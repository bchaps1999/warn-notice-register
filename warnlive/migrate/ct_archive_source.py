"""Connecticut WARN listings archived from the agency's annual web pages.

Input: ``agency/ct_archive`` in a source bundle (pinned in
``data/source_snapshots/ct/wayback-2026-09-30``): Wayback Machine captures of
the Connecticut Department of Labor's "Listing of WARN Notices" pages
(``warnreports/warnYYYY.htm``) for 2010-2012 and 2014-2025. Each page is one
HTML table: WARN Date (with the agency's "Rec'd" receipt date), company,
location(s), workers, layoff dates, closing, closing date, union and union
address. The pages carry no notice ID, so the unit is the table row.

Output: ``project`` returns admitted notice records, held rows (for the
exception ledger) and a report. The WARN Date fills ``notice_date`` with no
precision or basis (its legal role is the agency's listing, unaudited); the
"Rec'd" date is ``agency_received_date``. Rows marked as updates or revisions
of an earlier notice, rescinded rows, continuation rows and repeats are held.
A cell spanning several rows (2010) ties those rows to one filing through
``source_details.filing_group``; counts are not summed. Rows narrower than
the header are admitted with only employer and dates: their other cells
cannot be assigned to columns. No network access.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from warnlive.normalize.engine import _record_hash

PREFIX = "agency/ct_archive"
FORMAT = "ct-wayback-archive-v1"
YEARS = (2010, 2011, 2012, *range(2014, 2026))
HEADER = (r"WARN Date", r"Name of Affected Company.*", r"Location\(s\) of Layoffs",
          r"Number (?:of )?Affected Workers", r"Date\(s\) of Layoffs", r"Closing Yes/No",
          r"Date of Closing", r"Union Yes/No", r"Union Address")
WIDTH = len(HEADER)
DATE = r"\d{1,2}\s*/\s*\d{1,2}\s*/\s*(?:\d{4}|\d{2})"
# "12/21/10 Rec'd 12/22/10", "No Date Rec'd 6/23/14", "2/18/22 Received 3/14/22".
WARN_CELL = re.compile(rf"({DATE}|Not? Dated?)\s+(?:Rec['’`]?d\.?|Received)\s*({DATE})\s*(.*)", re.I)
RANGE = re.compile(rf"({DATE})\s*(?:-|–|to|thru|through)\s*({DATE})", re.I)
UPDATE = re.compile(r"\bupdat|\brevis|\bamend", re.I)
RESCINDED = re.compile(r"\brescind|\bwithdr[ae]w", re.I)


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _text(cell) -> str:
    return " ".join(cell.get_text(" ").replace("\xa0", " ").split())


def _day(text: str) -> str | None:
    """A whole-cell m/d/yy or m/d/yyyy date, else None."""
    value = re.sub(r"\s+", "", text or "")
    for pattern in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            parsed = datetime.strptime(value, pattern).date()
        except ValueError:
            continue
        if pattern == "%m/%d/%Y" and len(value.rsplit("/", 1)[-1]) != 4:
            continue
        return parsed.isoformat()
    return None


def _span(text: str) -> tuple[str | None, str | None]:
    """A single whole-cell date, or an ordered two-date range."""
    value = " ".join((text or "").split())
    single = _day(value) if re.fullmatch(DATE, value) else None
    if single:
        return single, None
    match = RANGE.fullmatch(value)
    if not match:
        return None, None
    start, end = _day(match.group(1)), _day(match.group(2))
    if not start or not end or end < start:
        return None, None
    return start, end


def _rows(table) -> list:
    rows = []
    for child in table.children:
        if getattr(child, "name", None) == "tr":
            rows.append(child)
        elif getattr(child, "name", None) in ("tbody", "thead", "tfoot"):
            rows.extend(c for c in child.children if getattr(c, "name", None) == "tr")
    return rows


def _cells(tr) -> list:
    return [c for c in tr.children if getattr(c, "name", None) in ("td", "th")]


def _table(content: bytes, name: str) -> list[dict]:
    """The listing table's data rows, rowspans expanded, in page order."""
    soup = BeautifulSoup(content, "html.parser")
    found = []
    for table in soup.find_all("table"):
        rows = _rows(table)
        for index, tr in enumerate(rows):
            header = [_text(c) for c in _cells(tr)]
            if header and header[0] == "WARN Date":
                found.append((rows, index, header))
    if len(found) != 1:
        raise ValueError(f"Connecticut listing table not found once: {name}")
    rows, index, header = found[0]
    if len(header) != WIDTH or not all(
            re.fullmatch(pattern, value) for pattern, value in zip(HEADER, header)):
        raise ValueError(f"Connecticut listing header changed: {name}")
    result = []
    pending: dict[int, list] = {}  # column -> [text, rows remaining, anchor ordinal]
    for ordinal, tr in enumerate(rows[index + 1:], start=1):
        physical = _cells(tr)
        if not physical and not pending:
            continue  # an empty <tr> between rows
        grid: dict[int, str] = {}
        anchors = set()
        for col, item in list(pending.items()):
            grid[col] = item[0]
            anchors.add(item[2])
            item[1] -= 1
            if item[1] == 0:
                del pending[col]
        column = 0
        for cell in physical:
            while column in grid:
                column += 1
            if int(cell.get("colspan") or 1) != 1:
                raise ValueError(f"Connecticut listing colspan: {name}:{ordinal}")
            grid[column] = _text(cell)
            span = int(cell.get("rowspan") or 1)
            if span > 1:
                pending[column] = [grid[column], span - 1, ordinal]
            column += 1
        width = max(grid) + 1 if grid else 0
        cells = [grid.get(col, "") for col in range(width)]
        if len(anchors) > 1:
            raise ValueError(f"Connecticut listing row spans two groups: {name}:{ordinal}")
        result.append({"table_row": ordinal, "cells": cells,
                       "physical_cells": [_text(c) for c in physical],
                       "span_anchor": next(iter(anchors)) if anchors else None,
                       "starts_span": any(int(c.get("rowspan") or 1) > 1 for c in physical),
                       "complete_grid": len(grid) == width})
    return result


def read_archive(directory: Path) -> tuple[list[dict], dict]:
    """Verify every pinned page and return each non-empty listing row."""
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    specs = manifest.get("artifacts") or []
    if (manifest.get("format") != FORMAT
            or [item.get("year") for item in specs] != list(YEARS)):
        raise ValueError("unsupported Connecticut archive manifest")
    rows = []
    for spec in specs:
        name = spec["file"]
        path = directory / name
        if (path.is_symlink() or not path.is_file()
                or not re.fullmatch(rf"warn{spec['year']}-\d{{14}}\.htm", name)):
            raise ValueError(f"invalid Connecticut archive artifact: {name}")
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if len(content) != spec["bytes"] or digest != spec["sha256"]:
            raise ValueError(f"Connecticut archive checksum mismatch: {name}")
        table = _table(content, name)
        if len(table) != spec["data_rows"]:
            raise ValueError(f"Connecticut archive row count changed: {name}")
        for item in table:
            pointer = f"{PREFIX}/{name}:sha256:{digest}:table_row:{item['table_row']}"
            raw = {"cells": item["cells"], "physical_cells": item["physical_cells"]}
            rows.append({
                **item, "year": spec["year"], "file": name,
                "source_artifact": f"{PREFIX}/{name}", "source_page_sha256": digest,
                "source_row": pointer,
                "source_row_sha256": hashlib.sha256(_json(raw).encode()).hexdigest(),
                "source_url": spec["wayback_url"], "raw": raw,
                "span_anchor_row": (f"{PREFIX}/{name}:sha256:{digest}:table_row:{item['span_anchor']}"
                                    if item["span_anchor"] else None),
            })
    return rows, manifest


def _employer_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").casefold())[:12]


def _fields(row: dict) -> dict:
    cells = row["cells"]
    warn = WARN_CELL.fullmatch(cells[0]) if cells else None
    notice = _day(warn.group(1)) if warn else None
    received = _day(warn.group(2)) if warn else None
    full = len(cells) == WIDTH and row["complete_grid"]
    company = cells[1] if len(cells) > 1 else ""
    employer = re.sub(r"\s*\*+$", "", company).strip()
    fields = {"warn_match": bool(warn), "notice": notice, "received": received,
              "warn_note": warn.group(3).strip() if warn else None,
              "company": company, "employer": employer,
              "footnote_marker": company.rstrip().endswith("*"), "full": full}
    if full:
        workers = cells[3].replace(",", "").strip()
        start, end = _span(cells[4])
        closing = cells[5].strip().casefold()
        fields.update({
            "location": cells[2] or None,
            "workers": int(workers) if workers.isdigit() and int(workers) > 0 else None,
            "start": start, "end": end,
            "layoff_type": ("closure" if closing.startswith("yes") else
                            "mass_layoff" if closing == "no" else "unknown")})
    else:
        fields.update({"location": None, "workers": None, "start": None, "end": None,
                       "layoff_type": "unknown"})
    return fields


def existing_events(conn) -> set[tuple[str, str]]:
    """(employer key, notice date) of Connecticut notices already in a build."""
    return {(_employer_key(row["employer_name"]), row["notice_date"])
            for row in conn.execute("SELECT employer_name, notice_date FROM notices WHERE state='CT'")}


def _near(known: set[tuple[str, str]], key: str, day: str | None, window: int = 60) -> bool:
    if not day:
        return any(k == key for k, _ in known)
    target = datetime.fromisoformat(day).date()
    return any(k == key and d and abs((datetime.fromisoformat(d).date() - target).days) <= window
               for k, d in known)


def project(directory: Path, known_events: set[tuple[str, str]] | None = None
            ) -> tuple[list[dict], list[dict], dict]:
    """Admit original listing rows; hold updates, repeats and continuations.

    ``known_events`` are (employer key, notice date) of Connecticut notices
    already in the build; a row within 60 days of one for the same employer
    key is held rather than admitted twice.
    """
    rows, manifest = read_archive(directory)
    known = set(known_events or ())
    partial_years = {item["year"]: item.get("coverage") for item in manifest["artifacts"]}
    records, held = [], []
    seen_cells: dict[tuple, str] = {}
    seen_events: dict[tuple, tuple[str, str]] = {}
    previous = None
    group_rows: Counter = Counter(row["span_anchor_row"] or (row["source_row"] if row["starts_span"] else None)
                                  for row in rows)
    for row in rows:
        f = _fields(row)
        text = " ".join(row["cells"][:2])
        rest = (((f["location"] or "").casefold(), f["workers"]) if f["full"]
                else tuple(row["cells"][2:]))
        event = (_employer_key(f["employer"]), f["notice"], f["received"], *rest)
        related = None
        if not f["warn_match"] and not row["span_anchor_row"]:
            reason, related = "official_source_continuation_row", previous
        elif RESCINDED.search(text):
            reason = "official_source_rescinded"
        elif UPDATE.search(text):
            reason = "official_source_update_unresolved"
        elif not f["employer"]:
            reason = "missing_employer"
        elif (row["file"], tuple(row["cells"])) in seen_cells:
            reason, related = "duplicate_row_in_source", seen_cells[(row["file"], tuple(row["cells"]))]
        elif event in seen_events and seen_events[event][0] != row["file"]:
            reason, related = "duplicate_listing_in_other_annual_page", seen_events[event][1]
        elif _near(known, event[0], f["notice"]):
            reason = "possible_overlap_with_current_ct_notice"
        else:
            reason = None
        if f["warn_match"]:
            previous = row["source_row"]
        if reason:
            item = {"origin": row["source_artifact"], "state": "CT", "reason": reason,
                    "source_row": row["source_row"], "source_row_sha256": row["source_row_sha256"],
                    "source_url": row["source_url"],
                    "notice_year": (f["notice"] or str(row["year"]))[:4],
                    "raw_extra": _json(row["raw"])}
            if related:
                item["related_source_row"] = related
            held.append(item)
            continue
        seen_cells.setdefault((row["file"], tuple(row["cells"])), row["source_row"])
        seen_events.setdefault(event, (row["file"], row["source_row"]))
        identity = f"CT:archive:{row['year']}:r{row['table_row']}"
        anchor = row["span_anchor_row"] or (row["source_row"] if row["starts_span"] else None)
        details = {
            "origin": row["source_artifact"], "source_row": row["source_row"],
            "source_row_sha256": row["source_row_sha256"],
            "source_page_sha256": row["source_page_sha256"],
            "identity_basis": "agency_listing_table_row",
            "listing_year": row["year"], "listing_coverage": partial_years.get(row["year"]),
            "raw_cells": row["cells"],
            "agency_received_date": f["received"],
            "warn_date_text": row["cells"][0], "warn_date_note": f["warn_note"] or None,
            "date_roles": {"WARN Date": "agency_listed_warn_date",
                           "Rec'd": "agency_receipt",
                           "Date(s) of Layoffs": "reported_action"},
            "date_interpretation": ("interval" if f["end"] else "single_date" if f["start"]
                                    else "unparsed"),
            "worker_allocation": "listing_row",
            "footnote_marker": "*" if f["footnote_marker"] else None,
            "layoff_type_evidence": ({"rule": "ct_archive_closing_column_v1",
                                      "source_text": row["cells"][5]} if f["full"] else None),
            "cell_alignment": (None if f["full"] else
                               "row_narrower_than_header_fields_after_company_unassigned"),
        }
        if anchor and group_rows[anchor] > 1:
            details["filing_group"] = {
                "id": f"CT:archive-filing-group:{row['year']}:{anchor.rsplit(':', 1)[-1]}",
                "rows": group_rows[anchor], "basis": "rows_share_spanned_warn_date_cell"}
        rec = {
            "state": "CT", "employer_name": f["employer"], "location": f["location"],
            "notice_date": f["notice"], "effective_date": f["start"],
            "effective_date_end": f["end"], "employees_affected": f["workers"],
            "layoff_type": f["layoff_type"], "is_temporary": None, "is_amendment": 0,
            "source_url": row["source_url"], "source_notice_id": None,
            "source_identity": identity,
            "source_details": _json({k: v for k, v in details.items() if v is not None}),
            "raw_extra": _json(row["raw"]),
            "dedupe_key": hashlib.sha1(f"CT|archive|{identity}".encode()).hexdigest(),
        }
        rec["raw_record_hash"] = _record_hash(rec)
        records.append(rec)
    if len(records) + len(held) != len(rows) or len({r["dedupe_key"] for r in records}) != len(records):
        raise ValueError("Connecticut archive row accounting mismatch")
    return records, held, {
        "source_rows": len(rows), "admitted": len(records), "held": len(held),
        "hold_reasons": dict(sorted(Counter(item["reason"] for item in held).items())),
        "admitted_workers": sum(r["employees_affected"] or 0 for r in records),
        "admitted_missing_workers": sum(r["employees_affected"] is None for r in records),
        "admitted_missing_notice_date": sum(r["notice_date"] is None for r in records),
        "admitted_narrow_rows": sum('"cell_alignment"' in r["source_details"] for r in records),
        "admitted_filing_group_rows": sum('"filing_group"' in r["source_details"] for r in records),
    }
