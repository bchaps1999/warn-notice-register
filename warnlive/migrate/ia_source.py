"""Extract and conservatively project official Iowa WARN observations.

An Excel row is an evidence pointer, not necessarily an additive layoff. Some
rows describe phases; others amend dates or worker counts. A source row is
admitted only where its event identity is unambiguous: rows of one
employer/notice/layoff date at distinct street addresses are admitted as
separate entries sharing ``source_details.filing_group``, and an amendment
with exactly one earlier admitted notice at the same employer and street
address becomes that notice's next version (``is_amendment=1``).

Input: the pinned ``agency/ia`` workbook and historical PDF. Output: records,
held rows with reasons, and a source-row relation map. ``project_archive``
applies the same rules to the archived IWD logs of 2005-2020
(``agency/ia_archive``: two workbooks and a PDF from Iowa Workforce
Development's former site, via the Wayback Machine), after removing events
printed in more than one archived log or listed in the current logs. No
network access.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

import pdfplumber
from openpyxl import load_workbook

from warnlive.normalize.engine import _fold, _record_hash
from warnlive.normalize.revisions import Disposition, classify_idless_events

HEADERS = (
    "Company", "Street Address", "City", "County", "State", "ZIP",
    "Notice Type", "Number of Employees Affected", "Notice Date",
    "Layoff Date", "Local Workforce Area", "Industry",
)
PDF_COLUMN_STARTS = (0, 168, 254, 310, 353, 369, 399, 448, 467, 515, 564, 632, 792)
PDF_DATE = re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}$")


def _cell(value: object) -> dict:
    """Keep Excel value types in a JSON-safe, stable source projection."""
    if isinstance(value, datetime):
        return {"type": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"type": "date", "value": value.isoformat()}
    if isinstance(value, bool):
        return {"type": "boolean", "value": value}
    if isinstance(value, int):
        return {"type": "integer", "value": value}
    if isinstance(value, float):
        return {"type": "number", "value": value}
    if isinstance(value, str):
        return {"type": "string", "value": value}
    if value is None:
        return {"type": "blank", "value": None}
    raise ValueError(f"unsupported Iowa cell value: {type(value).__name__}")


def _pdf_date(value: str) -> str | None:
    for pattern in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(value, pattern).date().isoformat()
        except ValueError:
            pass
    return None


def _is_amendment(value: str) -> bool:
    text = value.casefold()
    # "Ammendment" is a misspelling in the archived logs.
    return any(marker in text for marker in ("amend", "ammend", "additional employees", "change in"))


def extract(directory: Path) -> list[dict]:
    """Validate a pinned workbook, accounting for every populated data row."""
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("format") != "warn-ia-source-artifacts-v1":
        raise ValueError("unsupported Iowa source manifest")
    artifacts = manifest.get("artifacts") or []
    if {artifact.get("path") for artifact in artifacts} != {
        "event-log.xlsx", "historical-2023.pdf"
    } or len(artifacts) != 2:
        raise ValueError("Iowa source manifest must list both official logs")
    by_name = {artifact["path"]: artifact for artifact in artifacts}
    for artifact in artifacts:
        artifact_path = directory / artifact["path"]
        artifact_bytes = artifact_path.read_bytes()
        if (len(artifact_bytes) != artifact["bytes"] or
                hashlib.sha256(artifact_bytes).hexdigest() != artifact["sha256"]):
            raise ValueError(f"Iowa source checksum mismatch: {artifact_path}")
    historical = directory / "historical-2023.pdf"
    with pdfplumber.open(historical) as pdf:
        if len(pdf.pages) != by_name["historical-2023.pdf"]["pages"]:
            raise ValueError("Iowa historical PDF page count changed")
    item = by_name["event-log.xlsx"]
    path = directory / "event-log.xlsx"

    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        if workbook.sheetnames != [item["sheet"]]:
            raise ValueError("Iowa workbook sheet layout changed")
        sheet = workbook[item["sheet"]]
        if sheet.max_column != len(HEADERS):
            raise ValueError("Iowa workbook column count changed")
        header_row = item["header_row"]
        header = tuple(cell.value for cell in sheet[header_row])
        if header != HEADERS:
            raise ValueError("Iowa workbook header changed")
        if "since June 2021" not in str(sheet["A4"].value):
            raise ValueError("Iowa workbook coverage statement changed")
        result = []
        trailing_blank = False
        for ordinal, cells in enumerate(
            sheet.iter_rows(min_row=header_row + 1, values_only=True),
            start=header_row + 1,
        ):
            if all(cell is None for cell in cells):
                trailing_blank = True
                continue
            if trailing_blank:
                raise ValueError(f"Iowa data follows a blank row at {ordinal}")
            if len(cells) != len(HEADERS) or any(cell is None for cell in cells):
                raise ValueError(f"incomplete Iowa event-log row {ordinal}")
            company, street, city, county, state, zipcode, notice_type, workers, notice, layoff, area, industry = cells
            if not isinstance(workers, int) or isinstance(workers, bool) or workers < 0:
                raise ValueError(f"invalid Iowa worker count at row {ordinal}")
            if not isinstance(notice, (date, datetime)) or not isinstance(layoff, (date, datetime)):
                raise ValueError(f"invalid Iowa date at row {ordinal}")
            raw_cells = [_cell(value) for value in cells]
            raw = json.dumps(raw_cells, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"))
            result.append({
                "source_artifact": "agency/ia/event-log.xlsx",
                "source_row": f"event-log.xlsx:{item['sheet']}:r{ordinal}",
                "source_row_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                "source_xlsx_sha256": item["sha256"],
                "source_url": item["source_url"],
                "raw_cells": raw_cells,
                "company_text": company,
                "street_address_text": street,
                "city_text": city,
                "county_text": county,
                "address_state_text": state,
                "postal_code_text": zipcode,
                "address_role": "unverified",
                "notice_type_text": notice_type,
                "workers_reported": workers,
                "notice_date": notice.date().isoformat() if isinstance(notice, datetime) else notice.isoformat(),
                "effective_date": layoff.date().isoformat() if isinstance(layoff, datetime) else layoff.isoformat(),
                "local_workforce_area_text": area,
                "industry_text": industry,
                "decision_status": "needs_correspondence_and_amendment_review",
            })
        if len(result) != item["data_rows"]:
            raise ValueError("Iowa workbook row count changed")
        return result
    finally:
        workbook.close()


def extract_historical(directory: Path) -> list[dict]:
    """Project each historical-PDF line with its original page and cells.

    The PDF is a fixed-column printed worksheet, not a structured table. The
    worker-count column anchors its rows, including a malformed notice date;
    unexpected layouts fail closed.
    """
    extract(directory)  # validates both artifacts against the pinned manifest
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    artifact = next(item for item in manifest["artifacts"]
                    if item["path"] == "historical-2023.pdf")
    result = []
    with pdfplumber.open(directory / "historical-2023.pdf") as pdf:
        for page_number, (page, expected) in enumerate(
            zip(pdf.pages, artifact["data_rows_by_page"], strict=True), start=1
        ):
            words = page.extract_words(x_tolerance=1, y_tolerance=3, use_text_flow=True)
            anchors = sorted(
                (word for word in words if 448 <= word["x0"] < 467
                 and word["text"].isdigit()),
                key=lambda word: word["top"],
            )
            if len(anchors) != expected:
                raise ValueError(f"Iowa historical PDF row count changed on page {page_number}")
            for ordinal, anchor in enumerate(anchors, start=1):
                # The worker column is present even where the printed notice
                # date is malformed. A long company can wrap onto preceding
                # lines, ending level with the other cells of this row.
                previous_top = anchors[ordinal - 2]["top"] if ordinal > 1 else None
                lower = previous_top + 2.5 if previous_top is not None else anchor["top"] - 4.5
                cells: list[list[str]] = [[] for _ in range(12)]
                for word in words:
                    if not lower <= word["top"] <= anchor["top"] + 1.5:
                        continue
                    for index, (left, right) in enumerate(
                        zip(PDF_COLUMN_STARTS, PDF_COLUMN_STARTS[1:])
                    ):
                        if left <= word["x0"] < right:
                            cells[index].append(word["text"])
                            break
                values = [" ".join(parts) for parts in cells]
                if not values[0] or not values[7] or not values[8]:
                    raise ValueError(f"incomplete Iowa historical row {page_number}:{ordinal}")
                if not values[7].isdigit():
                    raise ValueError(f"invalid Iowa historical workers {page_number}:{ordinal}")
                raw = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
                layout_issues = [f"empty_column_{index + 1}" for index, value in
                                 enumerate(values) if not value]
                notice_date, effective_date = _pdf_date(values[8]), _pdf_date(values[9])
                if notice_date is None:
                    layout_issues.append("invalid_notice_date")
                if effective_date is None:
                    layout_issues.append("invalid_layoff_date")
                result.append({
                    "source_artifact": "agency/ia/historical-2023.pdf",
                    "source_row": f"historical-2023.pdf:p{page_number}:r{ordinal}",
                    "source_row_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                    "source_pdf_sha256": artifact["sha256"],
                    "source_url": artifact["source_url"],
                    "raw_cells": values,
                    "layout_issues": layout_issues,
                    "company_text": values[0], "street_address_text": values[1],
                    "city_text": values[2], "county_text": values[3],
                    "address_state_text": values[4], "postal_code_text": values[5],
                    "address_role": "unverified", "notice_type_text": values[6],
                    "workers_reported": int(values[7]),
                    "notice_date_text": values[8], "effective_date_text": values[9],
                    "notice_date": notice_date, "effective_date": effective_date,
                    "local_workforce_area_text": values[10],
                    "industry_text": values[11],
                    "decision_status": "needs_correspondence_and_amendment_review",
                })
    return result


def _layoff_type(text: str | None) -> str:
    """The agency's Notice Type column: "Closing" or "Mass Layoff"."""
    value = " ".join((text or "").split()).casefold()
    return {"closing": "closure", "mass layoff": "mass_layoff"}.get(value, "unknown")


def _location(row: dict) -> str | None:
    """City and county as the agency listed them, for place resolution.

    The row's street address is kept only in source_details with an
    unverified role; it may be a mailing address. An address outside Iowa
    names no Iowa place, so it yields no location.
    """
    state = (row.get("address_state_text") or "").strip().upper()
    if state and state != "IA":
        return None
    city = " ".join((row.get("city_text") or "").split())
    county = " ".join((row.get("county_text") or "").split())
    if county and not county.casefold().endswith(" county"):
        county = f"{county} County"
    return ", ".join(part for part in (city, county) if part) or None


def _distinct_sites(rows: list[dict]) -> list[list[dict]] | None:
    """One family's rows as distinct street-address sites, or None.

    Rows sharing an address are one site only when no artifact lists that
    address twice and every capture agrees on city and workers; the
    structured event log is preferred over the printed PDF. A blank address,
    a repeated address, or a disagreement leaves the family unresolved.
    """
    by_address: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        address = _fold(row["street_address_text"])
        if not address:
            return None
        by_address[address].append(row)
    if len(by_address) < 2:
        return None
    sites = []
    for address in sorted(by_address):
        captures = by_address[address]
        artifacts = Counter(row["source_artifact"] for row in captures)
        if (any(count > 1 for count in artifacts.values())
                or len({(row["workers_reported"], _fold(row["city_text"]))
                        for row in captures}) != 1):
            return None
        sites.append(sorted(captures, key=lambda row: (
            row["source_artifact"] != "agency/ia/event-log.xlsx", row["source_row"])))
    return sites


def _filing_order(row: dict) -> tuple:
    ordinal = re.search(r"(\d+)$", row["source_row"])
    return (row["notice_date"] or "", row["effective_date"] or "",
            row["source_artifact"] != "agency/ia/event-log.xlsx",
            int(ordinal.group(1)) if ordinal else 0, row["source_row"])


def _amendment_parents(rows: list[dict], status: dict[str, str],
                       admitted: dict[str, dict]) -> dict[str, tuple[str, str]]:
    """Each amendment's decision: ("version", parent pointer), ("duplicate",
    parent pointer) for a second capture of a versioned amendment, or
    ("held", reason).

    The parent is the unique admitted row with the same folded employer and
    street address whose notice date is on or before the amendment's. An
    identical amendment printed in both artifacts is one amendment. A version
    moving the action date is still that notice's revision: the row declares
    itself an amendment and has exactly one parent at its site, so the
    rebuild exempts the parent's key from the 45-day collision check.
    """
    parents_by_site: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in admitted.values():
        parents_by_site[(_fold(row["company_text"]), _fold(row["street_address_text"]))].append(row)
    pending = [row for row in rows if status[row["source_row"]] == "amendment_without_verified_parent"]
    result: dict[str, str] = {}
    captures: dict[tuple, list[dict]] = defaultdict(list)
    for row in pending:
        captures[(_fold(row["company_text"]), _fold(row["street_address_text"]),
                  row["notice_date"], row["effective_date"], row["workers_reported"])].append(row)
    chosen: dict[str, dict] = {}
    for key, group in captures.items():
        group.sort(key=_filing_order)
        for row in group[1:]:
            # Resolved below once the kept capture's outcome is known.
            chosen[row["source_row"]] = group[0]
    accepted: dict[str, list[str]] = defaultdict(list)
    for row in sorted(pending, key=_filing_order):
        pointer = row["source_row"]
        if pointer in chosen:
            continue
        address = _fold(row["street_address_text"])
        if row.get("layout_issues") or not row["notice_date"] or not row["effective_date"] or not address:
            result[pointer] = "amendment_without_verified_parent"
            continue
        parents = [parent for parent in parents_by_site.get((_fold(row["company_text"]), address), [])
                   if parent["notice_date"] <= row["notice_date"]]
        if len(parents) != 1:
            result[pointer] = ("amendment_parent_ambiguous" if parents
                               else "amendment_without_verified_parent")
            continue
        parent = parents[0]["source_row"]
        # A declared amendment with one parent at its exact site is that
        # filing's revision even when it moves the action date past the
        # 45-day collision window: ingest exempts its key (revision_keys).
        accepted[parent].append(row["effective_date"])
        result[pointer] = parent
    decisions: dict[str, tuple[str, str]] = {
        pointer: ("version", outcome) if outcome in admitted else ("held", outcome)
        for pointer, outcome in result.items()}
    for pointer, kept in chosen.items():
        outcome = result[kept["source_row"]]
        # A second capture of a versioned amendment points at the notice.
        decisions[pointer] = (("duplicate", outcome) if outcome in admitted
                              else ("held", outcome))
    return decisions


# Amendment type text that states the row's count is the notice's restated
# total. The event log's own types ("Amendment", "Additional Employees",
# "Reduction in number laid off", "Change in date") do not say whether a
# count is an increment, a reduction, a phase or a new total, so none of
# them applies its count; a type naming a restated total would.
_RESTATED_TOTAL_MARKERS = ("revised total", "restated total", "new total", "total revised")
_DATE_CHANGE_MARKERS = ("change in date",)


def _amendment_version(row: dict, latest: dict) -> dict:
    """The notice's next version after ``latest`` (its current version).

    Every amendment row is appended to ``source_details.amendments[]`` with
    its reported date, count, type and pointer; ``amendment`` names the
    newest. The row's layoff date replaces the notice's only for a "Change
    in date" type whose count is blank or equals the notice's (a smaller
    count is a phase of the layoff), and its worker count only when its type
    states a restated total. Otherwise the notice keeps its count and date. A notice with more
    than one amendment gets ``worker_allocation: "unresolved"``: the rows do
    not say how their counts combine. The filing identity, notice date,
    location and type stay the original notice's."""
    details = json.loads(latest["source_details"])
    type_text = str(row.get("notice_type_text") or "").casefold()
    date_change = any(marker in type_text for marker in _DATE_CHANGE_MARKERS)
    # A date change moves the whole notice only when it covers the whole
    # notice: a row reporting a different (smaller) count is a phase of the
    # layoff (Tyson Perry: 32, 32, 5, 19 of 1,276), not a new bulk date.
    reported, current = row["workers_reported"], latest.get("employees_affected")
    applies_date = date_change and (not reported or reported == current)
    applies_count = (any(marker in type_text for marker in _RESTATED_TOTAL_MARKERS)
                     and bool(row["workers_reported"]))
    entry = {
        "source_artifact": row["source_artifact"], "source_row": row["source_row"],
        "source_row_sha256": row["source_row_sha256"],
        "parent_basis": "same_folded_employer_and_street_address_earlier_notice",
        "revision_basis": "declared_amendment_unique_parent_site",
        "notice_type_text": row["notice_type_text"],
        "amendment_notice_date": row["notice_date"],
        "reported_layoff_date": row["effective_date"],
        "reported_workers": row["workers_reported"],
        "layoff_date_applied": applies_date,
        "workers_applied": applies_count,
        "raw_cells": row["raw_cells"]}
    if not applies_count:
        entry["workers_not_applied_reason"] = "amendment_count_meaning_unstated"
    if date_change and not applies_date:
        entry["layoff_date_not_applied_reason"] = "date_change_covers_part_of_notice"
    details["amendment"] = entry
    details["amendments"] = details.get("amendments", []) + [
        {name: entry[name] for name in (
            "source_row", "notice_type_text", "amendment_notice_date",
            "reported_layoff_date", "reported_workers", "layoff_date_applied",
            "workers_applied")}]
    if len(details["amendments"]) > 1:
        details["worker_allocation"] = "unresolved"
    rec = {**latest,
           "effective_date": (row["effective_date"] if applies_date and row["effective_date"]
                              else latest["effective_date"]),
           "employees_affected": (row["workers_reported"] if applies_count
                                  else latest["employees_affected"]),
           "is_amendment": 1,
           "source_details": json.dumps(details, sort_keys=True),
           "raw_extra": json.dumps(row["raw_cells"], ensure_ascii=False, default=str)}
    rec["raw_record_hash"] = _record_hash(rec)
    return rec


def project(rows: list[dict], layoff_type=None) -> tuple[list[dict], list[dict], dict, dict[str, str]]:
    """Admit uniquely identified ordinary rows; retain amendment and site questions.

    The two agency artifacts overlap. A printed PDF row and an Excel row with
    the same employer, dates, city and worker count are capture observations of
    one event, not two notices. Differing sites or worker allocations remain
    unresolved unless an agency filing identifier establishes their meaning.
    """
    if len({row["source_row"] for row in rows}) != len(rows):
        raise ValueError("duplicate Iowa source row pointer")
    families: dict[tuple[str, str | None, str | None], list[dict]] = defaultdict(list)
    for row in rows:
        families[(_fold(row["company_text"]), row["notice_date"],
                  row["effective_date"])].append(row)
    status: dict[str, str] = {}
    related: dict[str, str] = {}
    candidates: list[dict] = []
    filing_groups: dict[str, dict] = {}
    for group in families.values():
        ordinary = [row for row in group if not _is_amendment(row["notice_type_text"])]
        for row in group:
            if _is_amendment(row["notice_type_text"]):
                status[row["source_row"]] = "amendment_without_verified_parent"
            elif row.get("layout_issues") or not row["notice_date"] or not row["effective_date"]:
                status[row["source_row"]] = "unresolved_source_layout_or_date"
        ordinary = [row for row in ordinary if row["source_row"] not in status]
        # Multiple worker totals or locations under one employer/date pair may
        # be sites, phases, or revisions. Do not sum or publish them separately.
        workers = {row["workers_reported"] for row in ordinary}
        cities = {_fold(row["city_text"]) for row in ordinary}
        addresses = {_fold(row["street_address_text"]) for row in ordinary}
        if len(workers) > 1 or len(cities) > 1 or len(addresses) > 1:
            sites = _distinct_sites(ordinary)
            if sites is None:
                for row in ordinary:
                    status[row["source_row"]] = "site_phase_or_worker_allocation_unresolved"
                continue
            # Each listed street address is its own entry of one filing.
            first = ordinary[0]
            group_id = "IA:agency-filing-group:" + hashlib.sha1("|".join((
                _fold(first["company_text"]), first["notice_date"] or "",
                first["effective_date"] or "")).encode()).hexdigest()
            for captures in sites:
                preferred = captures[0]
                candidates.append(preferred)
                filing_groups[preferred["source_row"]] = {
                    "id": group_id, "rows": len(sites),
                    "basis": "same_employer_notice_and_layoff_date_distinct_addresses"}
                for row in captures[1:]:
                    status[row["source_row"]] = "duplicate_agency_capture"
                    related[row["source_row"]] = preferred["source_row"]
            continue
        if ordinary:
            # Prefer the structured, current agency log when a historical PDF
            # prints the same event. Distinct rows in the same artifact remain
            # unresolved because matching totals do not prove one filing.
            by_artifact = Counter(row["source_artifact"] for row in ordinary)
            if any(count > 1 for count in by_artifact.values()):
                for row in ordinary:
                    status[row["source_row"]] = "repeated_source_event_unresolved"
                continue
            preferred = sorted(ordinary, key=lambda row:
                               (row["source_artifact"] != "agency/ia/event-log.xlsx",
                                row["source_row"]))[0]
            candidates.append(preferred)
            for row in ordinary:
                if row is not preferred:
                    status[row["source_row"]] = "duplicate_agency_capture"
                    related[row["source_row"]] = preferred["source_row"]

    def classify(events: list[dict]) -> dict:
        return classify_idless_events(
            events, row_key=lambda row: row["source_row"],
            employer=lambda row: _fold(row["company_text"]),
            site=lambda row: _fold(row["street_address_text"] or row["city_text"]),
            action=lambda row: row["effective_date"],
            notice=lambda row: row["notice_date"],
            content=lambda row: json.dumps((row["notice_date"], row["effective_date"],
                                            row["workers_reported"])),
        )

    # Single-site events are decided on their own, so admitting a filing's
    # listed sites can never demote one of them. A site entry sharing an
    # employer/site event date with a single-site row is held instead.
    def signatures(row: dict) -> set[tuple]:
        firm, site = _fold(row["company_text"]), _fold(row["street_address_text"] or row["city_text"])
        return {(firm, site, "notice", row["notice_date"]), (firm, site, "action", row["effective_date"])}

    singles = [row for row in candidates if row["source_row"] not in filing_groups]
    entries = [row for row in candidates if row["source_row"] in filing_groups]
    decisions = classify(singles)
    taken = set().union(*(signatures(row) for row in singles)) if singles else set()
    overlapping = [row for row in entries if signatures(row) & taken]
    for row in overlapping:
        decisions[row["source_row"]] = Disposition("unresolved", "possible_revision_same_event")
    decisions.update(classify([row for row in entries if row not in overlapping]))
    for pointer, decision in decisions.items():
        status[pointer] = ("admitted" if decision.kind == "notice" else
                           "possible_revision_same_event" if decision.kind == "unresolved" else
                           "duplicate_agency_capture")
        if decision.related_row:
            related[pointer] = decision.related_row
    records, held = [], []
    admitted_rows = {row["source_row"]: row for row in rows if status[row["source_row"]] == "admitted"}
    amendments = _amendment_parents(rows, status, admitted_rows)
    for pointer, (kind, value) in amendments.items():
        if kind == "held":
            status[pointer] = value
            continue
        status[pointer] = "amendment_version" if kind == "version" else "duplicate_agency_capture"
        related[pointer] = value
    by_pointer: dict[str, dict] = {}
    for row in rows:
        pointer = row["source_row"]
        reason = status[pointer]
        row["disposition"] = reason
        if pointer in related:
            row["related_source_row"] = related[pointer]
        if reason == "amendment_version":
            continue
        if reason != "admitted":
            held.append({"origin": row["source_artifact"], "state": "IA", "reason": reason,
                         "source_row": pointer, "source_row_sha256": row["source_row_sha256"],
                         "source_url": row["source_url"], "notice_year":
                         row["notice_date"][:4] if row["notice_date"] else None,
                         "related_source_row": related.get(pointer),
                         "raw_extra": json.dumps(row["raw_cells"], ensure_ascii=False, default=str)})
            continue
        identity = f"IA:agency-observation:{hashlib.sha256(pointer.encode()).hexdigest()}"
        details = {"source_artifact": row["source_artifact"], "source_row": pointer,
                   "source_row_sha256": row["source_row_sha256"],
                   "identity_basis": "unique_agency_event_observation_not_filing_id",
                   "notice_type_text": row["notice_type_text"],
                   "address_role": "unverified", "street_address_text": row["street_address_text"],
                   "city_text": row["city_text"], "county_text": row["county_text"],
                   "address_state_text": row["address_state_text"]}
        if pointer in filing_groups:
            details["filing_group"] = filing_groups[pointer]
        rec = {"state": "IA", "employer_name": row["company_text"].strip(),
               "location": _location(row),
               "notice_date": row["notice_date"], "notice_date_precision": "day",
               "notice_date_basis": "reported", "effective_date": row["effective_date"],
               "effective_date_precision": "day", "effective_date_basis": "reported",
               "employees_affected": row["workers_reported"] or None,
               "layoff_type": (layoff_type or _layoff_type)(row["notice_type_text"]),
               "is_temporary": None, "is_amendment": 0,
               "source_url": row["source_url"], "source_notice_id": pointer,
               "source_identity": identity, "source_details": json.dumps(details, sort_keys=True),
               "raw_extra": json.dumps(row["raw_cells"], ensure_ascii=False, default=str),
               "dedupe_key": hashlib.sha1(identity.encode()).hexdigest()}
        rec["raw_record_hash"] = _record_hash(rec)
        records.append(rec)
        by_pointer[pointer] = rec
    # Amendments follow their parent notice as later versions, in filing order.
    rows_by_pointer = {row["source_row"]: row for row in rows}
    versions = []
    latest = dict(by_pointer)
    for pointer in sorted((p for p, reason in status.items() if reason == "amendment_version"),
                          key=lambda p: _filing_order(rows_by_pointer[p])):
        # Each amendment builds on the notice's latest version, in source order.
        version = _amendment_version(rows_by_pointer[pointer], latest[related[pointer]])
        latest[related[pointer]] = version
        versions.append(version)
    records.extend(versions)
    if len(records) + len(held) != len(rows):
        raise ValueError("Iowa source row accounting mismatch")
    return records, held, {"source_rows": len(rows), "admitted": len(records) - len(versions),
                           "amendment_versions": len(versions),
                           "admitted_filing_group_rows": sum(
                               "filing_group" in json.loads(rec["source_details"])
                               for rec in records if not rec["is_amendment"]),
                           "held": len(held), "hold_reasons": dict(Counter(x["reason"] for x in held))}, related


# ---------------------------------------------------------------------------
# Archived IWD WARN logs (``agency/ia_archive``), replay only.

ARCHIVE_PREFIX = "agency/ia_archive"
ARCHIVE_FORMAT = "ia-iwd-archive-v1"
ARCHIVE_WORKBOOKS = {
    "WARN_20200420-2-20210309153239.xlsx": ("WARN log for website", (
        "Company", "Address Line 1", "City", "County", "St.", "ZIP", "Notice Type",
        "Emp #", "Notice Date", "Layoff Date")),
    "WARN_20180503-20210309152211.xlsx": ("WARN Log 7_12_17- 2", (
        "Company", "Address Line 1", "City", "County", "State", "ZIP", "Notice Type",
        "Emp #", "Notice Date", "Layoff Date")),
}
ARCHIVE_PDF = "warn_20150812-20161227190458.pdf"
ARCHIVE_PDF_HEADER = ["Company", "Address", "City", "County", "State", "ZIP",
                      "Type of Notice", "Employees Affected", "Notice Date", "Layoff Date"]
# Newest log first: an event listed in several logs is cited from the newest.
ARCHIVE_ORDER = (*ARCHIVE_WORKBOOKS, ARCHIVE_PDF)


def _archive_date(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == datetime.min.time() else None
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and PDF_DATE.fullmatch(value.strip()):
        return _pdf_date(value.strip())
    return None


def _archive_row(name: str, pointer: str, digest: str, url: str, values: list,
                 raw_cells: list) -> dict:
    company, street, city, county, state, zipcode, notice_type, workers, notice, layoff = values
    text = lambda value: " ".join(str(value).split()) if value is not None else ""  # noqa: E731
    if isinstance(workers, str) and workers.strip().isdigit():
        workers = int(workers.strip())
    valid_workers = isinstance(workers, int) and not isinstance(workers, bool) and workers >= 0
    # An unreadable count blanks the count and keeps the row.
    issues = []
    notice_date, effective_date = _archive_date(notice), _archive_date(layoff)
    if notice_date is None:
        issues.append("invalid_notice_date")
    if effective_date is None:
        issues.append("invalid_layoff_date")
    raw = json.dumps(raw_cells, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return {
        "source_artifact": f"{ARCHIVE_PREFIX}/{name}", "source_row": pointer,
        "source_row_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "source_file_sha256": digest, "source_url": url, "raw_cells": raw_cells,
        "layout_issues": issues,
        "company_text": text(company), "street_address_text": text(street),
        "city_text": text(city), "county_text": text(county),
        "address_state_text": text(state), "postal_code_text": text(zipcode),
        "address_role": "unverified", "notice_type_text": text(notice_type),
        "workers_reported": workers if valid_workers else None,
        "notice_date": notice_date, "effective_date": effective_date,
    }


def extract_archive(directory: Path) -> list[dict]:
    """Every data row of the pinned archived IWD logs, newest log first."""
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    specs = {item.get("file"): item for item in manifest.get("artifacts") or []}
    if manifest.get("format") != ARCHIVE_FORMAT or set(specs) != set(ARCHIVE_ORDER):
        raise ValueError("unsupported Iowa archive manifest")
    rows: list[dict] = []
    for name in ARCHIVE_ORDER:
        spec, path = specs[name], directory / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"invalid Iowa archive artifact: {name}")
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if len(content) != spec["bytes"] or digest != spec["sha256"]:
            raise ValueError(f"Iowa archive checksum mismatch: {name}")
        found = []
        if name in ARCHIVE_WORKBOOKS:
            sheet_name, header = ARCHIVE_WORKBOOKS[name]
            book = load_workbook(path, read_only=True, data_only=True)
            try:
                if book.sheetnames != [sheet_name]:
                    raise ValueError(f"Iowa archive sheet changed: {name}")
                values = list(book[sheet_name].values)
            finally:
                book.close()
            if tuple(values[0]) != header:
                raise ValueError(f"Iowa archive header changed: {name}")
            for ordinal, cells in enumerate(values[1:], start=2):
                if all(cell is None or cell == "" for cell in cells):
                    continue
                if len(cells) != len(header):
                    raise ValueError(f"Iowa archive row width changed: {name}:{ordinal}")
                found.append(_archive_row(
                    name, f"{ARCHIVE_PREFIX}/{name}:{sheet_name}:r{ordinal}", digest,
                    spec["wayback_url"], list(cells), [_cell(value) for value in cells]))
        else:
            with pdfplumber.open(path) as pdf:
                if len(pdf.pages) != spec["pages"]:
                    raise ValueError(f"Iowa archive PDF page count changed: {name}")
                for page_number, page in enumerate(pdf.pages, start=1):
                    tables = page.extract_tables()
                    if len(tables) != 1 or [" ".join((c or "").split()) for c in tables[0][0]] != ARCHIVE_PDF_HEADER:
                        raise ValueError(f"Iowa archive PDF layout changed: {name}:{page_number}")
                    for ordinal, cells in enumerate(tables[0][1:], start=2):
                        if len(cells) != len(ARCHIVE_PDF_HEADER):
                            raise ValueError(f"Iowa archive PDF row width changed: {name}:{page_number}")
                        cells = [" ".join((cell or "").split()) for cell in cells]
                        if not any(cells):
                            continue
                        found.append(_archive_row(
                            name, f"{ARCHIVE_PREFIX}/{name}:p{page_number}:r{ordinal}", digest,
                            spec["wayback_url"], cells, cells))
        if len(found) != spec["data_rows"]:
            raise ValueError(f"Iowa archive row count changed: {name}")
        rows.extend(found)
    return rows


def _alnum(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").casefold())


def _same_employer(a: str, b: str) -> bool:
    """Equal names, or one a prefix of the other (the 2015 PDF cuts names at 30 characters)."""
    a, b = _alnum(a), _alnum(b)
    return bool(a and b) and (a == b or (min(len(a), len(b)) >= 12 and (a.startswith(b) or b.startswith(a))))


def project_archive(directory: Path, current_rows: list[dict]
                    ) -> tuple[list[dict], list[dict], dict]:
    """Project the archived logs with the event-log rules, never double counting.

    An event printed in more than one archived log is taken from the newest
    (an exact repeat is a duplicate capture; a changed listing is held). An
    event also listed in the current logs (``current_rows``, admitted or
    held there) is held, as is an amendment that may belong to a current-log
    notice. The remaining rows go through ``project``: its family, site and
    amendment rules (``_amendment_version``) apply unchanged.
    """
    rows = extract_archive(directory)
    rank = {name: index for index, name in enumerate(ARCHIVE_ORDER)}
    pre_held: dict[str, tuple[str, str | None]] = {}
    kept: list[dict] = []
    seen: list[dict] = []
    representative: dict[str, str] = {}
    paired: set[tuple[str, str]] = set()  # (newer row, older log) pairs already used
    for row in rows:
        log = row["source_artifact"].rsplit("/", 1)[-1]
        newer = [other for other in seen if rank[other["source_artifact"].rsplit("/", 1)[-1]] < rank[log]
                 and other["notice_date"] and other["notice_date"] == row["notice_date"]
                 and _same_employer(other["company_text"], row["company_text"])]
        twins = [other for other in newer if (other["source_row"], log) not in paired
                 and (other["effective_date"], other["workers_reported"], _is_amendment(other["notice_type_text"]))
                 == (row["effective_date"], row["workers_reported"], _is_amendment(row["notice_type_text"]))]
        twins.sort(key=lambda other: (_alnum(other["city_text"]) != _alnum(row["city_text"]),
                                      rank[other["source_artifact"].rsplit("/", 1)[-1]]))
        seen.append(row)
        if twins:
            paired.add((twins[0]["source_row"], log))
            representative[row["source_row"]] = representative[twins[0]["source_row"]]
            pre_held[row["source_row"]] = ("duplicate_agency_capture", representative[row["source_row"]])
        elif newer:
            representative[row["source_row"]] = row["source_row"]
            pre_held[row["source_row"]] = ("listing_differs_in_newer_archived_log",
                                           representative[newer[0]["source_row"]])
        else:
            representative[row["source_row"]] = row["source_row"]
            kept.append(row)
    current_by_date: dict[str, list[dict]] = defaultdict(list)
    current_sites: dict[str, list[dict]] = defaultdict(list)
    for row in current_rows:
        if row.get("notice_date"):
            current_by_date[row["notice_date"]].append(row)
        current_sites[_alnum(row["street_address_text"])].append(row)
    in_current: dict[str, str] = {}
    for row in kept:
        match = next((other for other in current_by_date.get(row["notice_date"], [])
                      if _same_employer(other["company_text"], row["company_text"])), None)
        if match and _is_amendment(row["notice_type_text"]):
            # Not a parent of any other row, so it can leave before projection.
            pre_held[row["source_row"]] = ("listed_in_current_ia_logs", match["source_row"])
        elif match:
            # Kept through projection (it may be an amendment's parent), then held.
            in_current[row["source_row"]] = match["source_row"]
        elif _is_amendment(row["notice_type_text"]):
            parent = next((other for other in current_sites.get(_alnum(row["street_address_text"]), [])
                           if _alnum(row["street_address_text"]) and other.get("notice_date")
                           and row["notice_date"] and other["notice_date"] <= row["notice_date"]
                           and _same_employer(other["company_text"], row["company_text"])), None)
            if parent:
                pre_held[row["source_row"]] = ("amendment_parent_may_be_in_current_ia_logs",
                                               parent["source_row"])
    projected_rows = [row for row in kept if row["source_row"] not in pre_held]
    records, held, _, related = (project(projected_rows, _archive_layoff_type)
                                 if projected_rows else ([], [], {}, {}))
    by_pointer = {row["source_row"]: row for row in rows}
    out_records: list[dict] = []
    for rec in records:
        details = json.loads(rec["source_details"])
        pointer = details["source_row"]
        if rec["is_amendment"]:
            pointer = details["amendment"]["source_row"]
            if related.get(pointer) in in_current:
                held.append(_archive_held(by_pointer[pointer], "amendment_parent_listed_in_current_ia_logs",
                                          in_current[related[pointer]]))
                continue
        elif pointer in in_current:
            held.append(_archive_held(by_pointer[pointer], "listed_in_current_ia_logs", in_current[pointer]))
            continue
        out_records.append(rec)
    for pointer, (reason, other) in pre_held.items():
        held.append(_archive_held(by_pointer[pointer], reason, other))
    order = {row["source_row"]: index for index, row in enumerate(rows)}
    held.sort(key=lambda item: order[item["source_row"]])
    versions = sum(rec["is_amendment"] for rec in out_records)
    if len(out_records) + len(held) != len(rows):
        raise ValueError("Iowa archive row accounting mismatch")
    notices = [rec for rec in out_records if not rec["is_amendment"]]
    return out_records, held, {
        "source_rows": len(rows),
        "rows_by_file": dict(Counter(row["source_artifact"].rsplit("/", 1)[-1] for row in rows)),
        "admitted": len(notices), "amendment_versions": versions, "held": len(held),
        "hold_reasons": dict(sorted(Counter(item["reason"] for item in held).items())),
        "admitted_workers": sum(rec["employees_affected"] or 0 for rec in notices),
        "admitted_filing_group_rows": sum(
            "filing_group" in json.loads(rec["source_details"]) for rec in notices),
    }


def _archive_layoff_type(text: str | None) -> str:
    """The archived logs' Notice Type, which also reads "Closure" or "Layoff"."""
    value = " ".join((text or "").split()).casefold()
    return {"closure": "closure", "layoff": "mass_layoff"}.get(value) or _layoff_type(text)


def _archive_held(row: dict, reason: str, related: str | None) -> dict:
    return {"origin": row["source_artifact"], "state": "IA", "reason": reason,
            "source_row": row["source_row"], "source_row_sha256": row["source_row_sha256"],
            "source_url": row["source_url"],
            "notice_year": row["notice_date"][:4] if row["notice_date"] else None,
            "related_source_row": related,
            "raw_extra": json.dumps(row["raw_cells"], ensure_ascii=False, default=str)}
