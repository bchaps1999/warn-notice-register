"""Tennessee agency WARN rows from pinned agency pages and Wayback captures.

``read_artifacts``/``project``/``build`` handle ``agency/tn``, the current
reports page's 2021-2024 paragraphs. ``read_archive``/``project_archive``
handle ``agency/tn_archive`` (data/source_snapshots/tn/wayback-2026-09-30):
the agency's "WARN Summary by Month" PDF (2012 to October 2017) and nine
Wayback captures of the reports page (2018-2021). A WARN number is the
identity where the page gives one; a posting day is an agency posting date,
and the report's ``Notice Date`` keeps an unverified role. No Big Local News
rows and no network access.

In ``project_archive``, dates outside the live transformer's window (``archive_dates``) are blanked
and reported under ``implausible_dates_blanked``.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import re
import tarfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from warnlive.migrate import archive_dates
from warnlive.migrate.source_bundle import _entry, verify
from warnlive.normalize.engine import _record_hash

YEARS = ("2021", "2022", "2023", "2024")
LABELS = ("Date Notice Posted", "Company", "County", "Affected Workers",
          "Closure/Layoff Date", "Notice/Type")


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _day(value: str) -> str | None:
    value = value.strip()
    for pattern in ("%m/%d/%Y", "%Y/%m/%d", "%B %d, %Y", "%b %d, %Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(value, pattern).date().isoformat()
        except ValueError:
            pass
    return None


def read_artifacts(directory: Path) -> tuple[list[dict], dict]:
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    if (manifest.get("format") != "warn-tn-agency-archive-v1" or
            manifest.get("artifact") != "reports.html"):
        raise ValueError("unsupported Tennessee source manifest")
    content = (directory / "reports.html").read_bytes()
    if len(content) != manifest.get("bytes") or hashlib.sha256(content).hexdigest() != manifest.get("sha256"):
        raise ValueError("Tennessee archive checksum mismatch")
    soup = BeautifulSoup(content, "html.parser")
    rows = []
    counts: Counter[str] = Counter()
    for section in soup.select(".accordion-item"):
        heading = section.select_one(".accordion-button")
        year = heading.get_text(" ", strip=True) if heading else ""
        if year not in YEARS:
            continue
        for ordinal, paragraph in enumerate(section.select(".accordion-body p"), start=1):
            original = paragraph.get_text(" ", strip=True)
            parts = [part.strip() for part in original.split("|")]
            fields = {}
            if len(parts) == len(LABELS):
                for label, part in zip(LABELS, parts, strict=True):
                    prefix = f"{label}:"
                    if part.startswith(prefix):
                        fields[label] = part[len(prefix):].strip()
            hrefs = [urljoin(manifest["source_url"], a["href"])
                     for a in paragraph.select("a[href]")]
            rows.append({"section_year": year, "section_row": ordinal,
                         "source_row": f"{year}:p{ordinal}", "source_text": original,
                         "fields": fields, "document_urls": hrefs,
                         "source_row_sha256": hashlib.sha256(original.encode()).hexdigest()})
            counts[year] += 1
    if dict(counts) != manifest.get("archived_paragraph_rows"):
        raise ValueError("Tennessee archive row count drift")
    return rows, manifest


def project(directory: Path, current_raw: Path | None = None) -> tuple[list[dict], list[dict], dict]:
    rows, manifest = read_artifacts(directory)
    ids = Counter((row["section_year"], row["fields"].get("Notice/Type", "")) for row in rows)
    current_ids = set()
    if current_raw is not None:
        with Path(current_raw).open(newline="", encoding="utf-8-sig") as stream:
            current_ids = {(row.get("Notice ID") or "").lstrip("# ").strip()
                           for row in csv.DictReader(stream)}
    records, held = [], []
    for row in rows:
        fields = row["fields"]
        year, number = row["section_year"], fields.get("Notice/Type", "")
        number = number.lstrip("# ").strip()
        posting = _day(fields.get("Date Notice Posted", ""))
        workers_text = fields.get("Affected Workers", "")
        company = fields.get("Company", "").strip()
        required = set(LABELS) - {"County"}
        complete = (required.issubset(fields) and bool(re.fullmatch(r"20\d{6,7}", number))
                    and ids[(year, fields.get("Notice/Type", ""))] == 1
                    and number not in current_ids
                    and posting is not None and posting.startswith(year)
                    and bool(company) and company.lower() != "view"
                    and bool(re.fullmatch(r"\d+(?:,\d{3})*", workers_text)))
        if not complete:
            held.append({"origin": "agency/tn/reports.html", "state": "TN",
                         "reason": ("overlaps_current_source_notice_number" if number in current_ids
                                    else "agency_archive_row_ambiguous"),
                         "source_row": row["source_row"],
                         "source_row_sha256": row["source_row_sha256"],
                         "source_notice_id": number or None, "source_url": manifest["source_url"],
                         "notice_year": None, "raw_extra": _json(row)})
            continue
        effective = _day(fields["Closure/Layoff Date"])
        identity = f"TN:agency:{year}:{number}"
        details = {"origin": "agency/tn/reports.html", "source_row": row["source_row"],
                   "source_row_sha256": row["source_row_sha256"],
                   "source_html_sha256": manifest["sha256"],
                   "identity_basis": "archive_section_and_notice_number",
                   "agency_posted_date": posting,
                   "date_roles": {"Date Notice Posted": "agency_posting",
                                  "Closure/Layoff Date": "reported_action"},
                   "reported_action_text": fields["Closure/Layoff Date"],
                   "document_urls": row["document_urls"], "source_text": row["source_text"]}
        rec = {"state": "TN", "employer_name": company,
               "location": fields.get("County") or None, "notice_date": None,
               "effective_date": effective, "employees_affected": int(workers_text.replace(",", "")),
               "layoff_type": "unknown", "is_temporary": None, "is_amendment": 0,
               "source_url": manifest["source_url"], "source_notice_id": number,
               "source_identity": identity, "source_details": _json(details),
               "raw_extra": _json(row),
               "dedupe_key": hashlib.sha1(identity.encode()).hexdigest()}
        if effective:
            rec.update(effective_date_precision="day", effective_date_basis="reported")
        rec["raw_record_hash"] = _record_hash(rec)
        records.append(rec)
    if len(records) + len(held) != len(rows):
        raise ValueError("Tennessee archive source row accounting mismatch")
    return records, held, {"source_rows": len(rows), "admitted": len(records),
                           "held": len(held)}


def build(base_bundle: Path, artifacts: Path, out_bundle: Path) -> dict:
    base_bundle, artifacts, out_bundle = map(Path, (base_bundle, artifacts, out_bundle))
    if out_bundle.exists():
        raise FileExistsError(out_bundle)
    base_manifest = verify(base_bundle)
    if base_manifest.get("admission_inputs") != "agency-only-v1":
        raise ValueError("Tennessee overlay requires an agency-only base")
    project(artifacts)
    with tarfile.open(base_bundle, "r:gz") as archive:
        files = {member.name: archive.extractfile(member).read()
                 for member in archive.getmembers() if member.name != "manifest.json"}
    if any(name.startswith("agency/tn/") for name in files):
        raise ValueError("base bundle already contains Tennessee archive")
    for name in ("manifest.json", "reports.html"):
        files[f"agency/tn/{name}"] = (artifacts / name).read_bytes()
    manifest = {**base_manifest, "files": [
        {"path": name, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        for name, content in sorted(files.items())]}
    out_bundle.parent.mkdir(parents=True, exist_ok=True)
    try:
        with out_bundle.open("xb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as zipped, tarfile.open(fileobj=zipped, mode="w") as archive:
            header = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
            archive.addfile(_entry("manifest.json", header), io.BytesIO(header))
            for name, content in sorted(files.items()):
                archive.addfile(_entry(name, content), io.BytesIO(content))
        verify(out_bundle)
    except BaseException:
        out_bundle.unlink(missing_ok=True)
        raise
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.base, args.artifacts, args.out)
    print(json.dumps({"files": len(result["files"]), "out": str(args.out)}, sort_keys=True))


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------------------
# Tennessee Wayback archive (agency/tn_archive): the agency's "WARN Summary by
# Month" report (notices dated 2012-01-02..2017-10-26) and nine captures of the
# reports page (2018-01..2021-12), pinned in
# data/source_snapshots/tn/wayback-2026-09-30.

ARCHIVE_PREFIX = "agency/tn_archive"
ARCHIVE_FORMAT = "tn-wayback-archive-v1"
REPORT_PDF = "warn-report-by-month-2012-2017.pdf"
REPORT_PDF_SHA256 = "25f1f917936d5fcae50d8ac9d7a79fba5b578fb37022d68122b2e9e7368b4bb9"
REPORT_PDF_PAGES = 44
REPORT_PDF_ROWS = 511
REPORT_HEADER = ["Notice Date", "Effective Date", "Received Date", "Company", "City",
                 "County", "No. Of\nEmployees", "Layoff/Closure"]
PAGE_CAPTURES = {
    "20180112155245": "662fe6386baadd7d505a1f561be22cfea389129418a28bea213cd5f0833671dd",
    "20180712190233": "856d3c51619bcc96bf6a891d855542e9289cf2b4c710450a7f84de0e70cfee40",
    "20181012213659": "b0c0aab7150716775a6771cafc517622ef033ec92acaa5d0084a9cc75bccbf16",
    "20190412171302": "6bd2bbcef63e8c5f931154de074f7ae3dd7abb52c4a9d22bf6bead62950c66b5",
    "20191027173011": "f728cbe2ed8b1f9dfc23f8cdd9567f280a46b602ed1784eafad8f4043d1106b7",
    "20200124205330": "94e2614ce0c9c70a467669c5a0b5fdaf440093bec48ff2f0b5232759999fd02e",
    "20200808210834": "4a798075ac8112517fcb8895153cbf7a857581871923dce9b8a44310fde5cb8a",
    "20210418142217": "e3777c1aeda972175b11d7aa7bf9b4865c20a480410adb53786bafb3c6a434ce",
    "20211209100151": "73bfbc41d8257c3e43dd1000700b20a2f08f4379a227787aa8cfe30952f923e1",
}
PAGE_LABELS = {"Date Notice Posted": "posted", "Company": "company", "County": "county",
               "Counties": "county", "Affected Workers": "workers",
               "Closure/Layoff Date": "action", "Notice/Type": "notice",
               "Notice Type": "notice"}
WARN_NUMBER = re.compile(r"#\s*(\d{6,9})")
VALID_NUMBER = re.compile(r"(20\d\d)(\d{2,5})")


def _canonical(number: str | None) -> str | None:
    """One WARN number printed with or without zero padding ("2019008" and
    "20190008" are the agency's number 8 of 2019)."""
    match = VALID_NUMBER.fullmatch(number or "")
    return f"{match.group(1)}:{int(match.group(2))}" if match else None


def _norm(value: str) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _checked(directory: Path, name: str, sha256: str, spec: dict) -> bytes:
    path = directory / name
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"invalid Tennessee archive artifact: {path}")
    content = path.read_bytes()
    if (spec.get("sha256") != sha256 or len(content) != spec.get("bytes")
            or hashlib.sha256(content).hexdigest() != sha256):
        raise ValueError(f"Tennessee archive checksum mismatch: {name}")
    return content


def _report_rows(content: bytes) -> tuple[list[dict], dict[str, tuple[int, int]]]:
    """Detail rows and the report's own month summary (notices, employees)."""
    import pdfplumber

    rows, summary = [], {}
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        if len(pdf.pages) != REPORT_PDF_PAGES:
            raise ValueError("Tennessee report page count changed")
        for page_number, page in enumerate(pdf.pages, start=1):
            for table in page.extract_tables():
                for ordinal, cells in enumerate(table, start=1):
                    cells = [cell if cell is not None else "" for cell in cells]
                    first = cells[0] if cells else ""
                    if re.fullmatch(r"\d\d/\d\d/\d{4}", first):
                        if len(cells) != len(REPORT_HEADER):
                            raise ValueError(f"Tennessee report row width changed: p{page_number}")
                        rows.append({"page": page_number, "table_row": ordinal,
                                     "cells": dict(zip(REPORT_HEADER, cells, strict=True))})
                    elif re.fullmatch(r"[A-Z][a-z]+ \d{4}", first):
                        summary[first] = (int(cells[1]), int(cells[2].replace(",", "")))
                    elif first in ("Notice Date", "Summary by Month", "Total", "") :
                        if first == "Notice Date" and cells != REPORT_HEADER:
                            raise ValueError(f"Tennessee report header changed: p{page_number}")
                    else:
                        raise ValueError(f"unexpected Tennessee report row: p{page_number}")
    return rows, summary


def _page_records(content: bytes) -> list[dict]:
    """Each notice record of one reports-page capture, split on its label."""
    soup = BeautifulSoup(content, "html.parser")
    records = []
    for paragraph in soup.find_all("p"):
        text = _norm(paragraph.get_text(" "))
        if "Date Notice Posted" not in text:
            continue
        hrefs = [urljoin("https://www.tn.gov/", a["href"]) for a in paragraph.select("a[href]")]
        parts = re.split(r"(?=Date Notice Posted\b)", text)
        parts = [part.strip() for part in parts if part.strip()]
        for part in parts:
            fields: dict[str, str] = {}
            for segment in part.split("|"):
                label, sep, value = segment.partition(":")
                key = PAGE_LABELS.get(_norm(label))
                if sep and key and key not in fields:
                    fields[key] = _norm(value)
            records.append({"text": part, "fields": fields,
                            "document_urls": hrefs if len(parts) == 1 else []})
    return records


def read_archive(directory: Path) -> dict:
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    specs = {item.get("file"): item for item in manifest.get("artifacts") or []}
    expected = {REPORT_PDF} | {f"reports-{ts}.html" for ts in PAGE_CAPTURES}
    if manifest.get("format") != ARCHIVE_FORMAT or set(specs) != expected:
        raise ValueError("unsupported Tennessee archive manifest")
    spec = specs[REPORT_PDF]
    rows, summary = _report_rows(_checked(directory, REPORT_PDF, REPORT_PDF_SHA256, spec))
    if len(rows) != REPORT_PDF_ROWS or spec.get("data_rows") != REPORT_PDF_ROWS:
        raise ValueError("Tennessee report row count changed")
    # The report's month summary counts rows that state a worker count.
    months: Counter[str] = Counter()
    workers: Counter[str] = Counter()
    for row in rows:
        stamp = datetime.strptime(row["cells"]["Notice Date"], "%m/%d/%Y")
        count = row["cells"]["No. Of\nEmployees"].replace(",", "")
        if count.isdigit():
            months[stamp.strftime("%B %Y")] += 1
            workers[stamp.strftime("%B %Y")] += int(count)
    if summary != {month: (months[month], workers[month]) for month in summary} or set(months) - set(summary):
        raise ValueError("Tennessee report rows disagree with its month summary")
    report = []
    for row in rows:
        raw_hash = hashlib.sha256(_json(row["cells"]).encode()).hexdigest()
        report.append({**row, "source_artifact": f"{ARCHIVE_PREFIX}/{REPORT_PDF}",
                       "source_row": (f"{ARCHIVE_PREFIX}/{REPORT_PDF}:sha256:{REPORT_PDF_SHA256}:"
                                      f"page:{row['page']}:table_row:{row['table_row']}"),
                       "source_row_sha256": raw_hash, "source_url": spec["wayback_url"]})
    # A page record is one distinct record text; the captures listing it are
    # recorded with it (a later capture repeating the same text adds no row).
    texts: dict[str, dict] = {}
    for ts in sorted(PAGE_CAPTURES):
        name = f"reports-{ts}.html"
        spec = specs[name]
        records = _page_records(_checked(directory, name, PAGE_CAPTURES[ts], spec))
        if len(records) != spec.get("data_rows"):
            raise ValueError(f"Tennessee page capture row count changed: {name}")
        for record in records:
            entry = texts.setdefault(record["text"], {**record, "captures": []})
            entry["captures"].append(ts)
            if record["document_urls"] and not entry["document_urls"]:
                entry["document_urls"] = record["document_urls"]
    pages = []
    for text, entry in texts.items():
        digest = hashlib.sha256(text.encode()).hexdigest()
        first = entry["captures"][0]
        pages.append({**entry, "source_artifact": f"{ARCHIVE_PREFIX}/reports-{first}.html",
                      "source_row": f"{ARCHIVE_PREFIX}/reports.html:text:sha256:{digest}",
                      "source_row_sha256": digest,
                      "source_url": specs[f"reports-{first}.html"]["wayback_url"]})
    pages.sort(key=lambda item: (item["captures"][0], item["source_row"]))
    if len(pages) != manifest.get("distinct_page_records"):
        raise ValueError("Tennessee distinct page record count changed")
    return {"report": report, "pages": pages, "manifest": manifest}


def _company_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def project_archive(directory: Path, known_numbers: set[str] | None = None
                    ) -> tuple[list[dict], list[dict], dict]:
    """Admit Wayback report rows and page records; hold overlaps and ambiguity.

    ``known_numbers`` are the WARN numbers already in the build (agency/tn
    archive rows, admitted or held, and the current raw capture). The page's
    WARN number is the identity; the report has none, so its row is.
    """
    data = read_archive(directory)
    known = {_canonical(str(x).lstrip("# ").strip()) for x in known_numbers or () if x} - {None}
    records, held = [], []

    def hold(row: dict, reason: str, number: str | None, year: str | None, raw: object,
             **extra) -> None:
        held.append({"origin": row["source_artifact"], "state": "TN", "reason": reason,
                     "source_row": row["source_row"],
                     "source_row_sha256": row["source_row_sha256"],
                     "source_notice_id": number, "source_url": row["source_url"],
                     "notice_year": year, "raw_extra": _json(raw), **extra})

    # Page records: one notice per WARN number; the latest capture's text is
    # current, and an earlier differing text of that number is held. A number
    # whose texts name different events (the agency reused or permuted it) and
    # a record listed under two numbers are held as ambiguous.
    def same_event(a: dict, b: dict) -> bool:
        fa, fb = a["fields"], b["fields"]
        return (_company_key(fa.get("company", "")) == _company_key(fb.get("company", ""))
                or all(fa.get(k) == fb.get(k) for k in ("posted", "workers", "county")))

    by_number: dict[str, list[dict]] = {}
    page_numbers = set()
    for row in data["pages"]:
        match = WARN_NUMBER.search(row["fields"].get("notice", ""))
        row["number"] = match.group(1) if match else None
        row["canonical"] = _canonical(row["number"])
        if row["canonical"]:
            by_number.setdefault(row["canonical"], []).append(row)
    reused = set()
    for number, group in by_number.items():
        clusters: list[list[dict]] = []
        for row in group:
            for cluster in clusters:
                if any(same_event(row, other) for other in cluster):
                    cluster.append(row)
                    break
            else:
                clusters.append([row])
        if len(clusters) > 1:
            reused.add(number)
    signatures: dict[tuple, set[str]] = {}
    for row in data["pages"]:
        f = row["fields"]
        signatures.setdefault((_company_key(f.get("company", "")), f.get("posted"),
                               f.get("workers")), set()).add(row["canonical"] or "")
    for row in data["pages"]:
        f, number = row["fields"], row["number"]
        posted = _day(f.get("posted", "").replace(" ", ""))
        year = posted[:4] if posted else None
        raw = {k: row[k] for k in ("text", "fields", "document_urls", "captures")}
        workers_text = f.get("workers", "")
        numbers = signatures[(_company_key(f.get("company", "")), f.get("posted"), f.get("workers"))]
        if not number:
            hold(row, "agency_archive_row_ambiguous", None, year, raw)
            continue
        if row["canonical"] in known:
            hold(row, "already_represented_tn_warn_number", number, year, raw)
            continue
        if not row["canonical"]:
            hold(row, "agency_archive_row_ambiguous", number, year, raw)
            continue
        if row["canonical"] in reused:
            hold(row, "warn_number_lists_different_events", number, year, raw)
            continue
        if len(numbers - {""}) > 1:
            hold(row, "same_record_listed_under_two_warn_numbers", number, year, raw)
            continue
        latest = max(by_number[row["canonical"]], key=lambda r: (r["captures"][-1], r["captures"][0]))
        if latest is not row:
            hold(row, "earlier_capture_text_of_warn_number", number, year, raw,
                 related_source_row=latest["source_row"])
            continue
        company = f.get("company", "")
        if not company or posted is None or not {"posted", "company", "notice"} <= set(f):
            hold(row, "agency_archive_row_ambiguous", number, year, raw)
            continue
        page_numbers.add(row["canonical"])
        effective = _day(f.get("action", ""))
        identity = f"TN:warn:{row['canonical']}"
        details = {"origin": row["source_artifact"], "source_row": row["source_row"],
                   "source_row_sha256": row["source_row_sha256"],
                   "identity_basis": "agency_warn_number",
                   "wayback_captures": row["captures"],
                   "agency_posted_date": posted,
                   "date_roles": {"Date Notice Posted": "agency_posting",
                                  "Closure/Layoff Date": "reported_action"},
                   "reported_action_text": f.get("action"),
                   "document_urls": row["document_urls"], "source_text": row["text"]}
        rec = {"state": "TN", "employer_name": company, "location": f.get("county") or None,
               "notice_date": None, "effective_date": effective,
               "employees_affected": (int(workers_text.replace(",", ""))
                                      if re.fullmatch(r"\d+(?:,\d{3})*", workers_text)
                                      and int(workers_text.replace(",", "")) > 0 else None),
               "layoff_type": "unknown", "is_temporary": None, "is_amendment": 0,
               "source_url": row["source_url"], "source_notice_id": number,
               "source_identity": identity, "source_details": _json(details),
               "raw_extra": _json(raw),
               "dedupe_key": hashlib.sha1(f"TN|archive|{identity}".encode()).hexdigest()}
        if effective:
            rec.update(effective_date_precision="day", effective_date_basis="reported")
        rec["raw_record_hash"] = _record_hash(rec)
        records.append(rec)
    admitted_pages = [(json.loads(rec["raw_extra"])["fields"]) for rec in records]
    page_events = {(_company_key(f.get("company", "")), _norm(f.get("county", "")).casefold(),
                    f.get("workers", "").replace(",", "")) for f in admitted_pages}
    # Report rows: the page begins where the report ends (late 2017), so a
    # report row repeating an admitted page record (employer, county, workers)
    # is held instead of admitted twice.
    seen: dict[str, str] = {}
    for row in data["report"]:
        c = row["cells"]
        year = c["Notice Date"][-4:]
        county = _norm(c["County"])
        workers_text = c["No. Of\nEmployees"].replace(",", "")
        event = (_company_key(c["Company"]), re.sub(r"\s+county$", "", county, flags=re.I).casefold(),
                 workers_text)
        content = _json(c)
        if not _norm(c["Company"]):
            hold(row, "missing_employer", None, year, c)
            continue
        if event in page_events:
            hold(row, "listed_on_reports_page_with_warn_number", None, year, c)
            continue
        if content in seen:
            hold(row, "duplicate_row_in_source", None, year, c, related_source_row=seen[content])
            continue
        seen[content] = row["source_row"]
        notice = _day(c["Notice Date"])
        effective = _day(c["Effective Date"])
        kind, _, term = _norm(c["Layoff/Closure"]).partition(" ")
        identity = f"TN:report-by-month:p{row['page']}:r{row['table_row']}"
        details = {"origin": row["source_artifact"], "source_row": row["source_row"],
                   "source_row_sha256": row["source_row_sha256"],
                   "source_artifact_sha256": REPORT_PDF_SHA256,
                   "identity_basis": "agency_report_row",
                   "agency_received_date": _day(c["Received Date"]),
                   "agency_report_notice_date": notice,
                   "date_roles": {"Notice Date": "agency_report_notice_date_role_unverified",
                                  "Received Date": "agency_receipt",
                                  "Effective Date": "reported_action"},
                   "city_text": _norm(c["City"]) or None,
                   "layoff_closure_text": _norm(c["Layoff/Closure"]) or None,
                   "raw_fields": c}
        rec = {"state": "TN", "employer_name": _norm(c["Company"]),
               "location": county or None, "notice_date": notice, "effective_date": effective,
               "employees_affected": int(workers_text) if workers_text.isdigit()
               and int(workers_text) > 0 else None,
               "layoff_type": ("closure" if kind == "Closure" else
                               "mass_layoff" if kind == "Layoff" else "unknown"),
               "is_temporary": (1 if term == "Temporary" else 0 if term == "Permanent" else None),
               "is_amendment": 0,
               "source_url": row["source_url"], "source_notice_id": None,
               "source_identity": identity,
               "source_details": _json({k: v for k, v in details.items() if v is not None}),
               "raw_extra": _json(c),
               "dedupe_key": hashlib.sha1(f"TN|archive|{identity}".encode()).hexdigest()}
        if effective:
            rec.update(effective_date_precision="day", effective_date_basis="reported")
        rec["raw_record_hash"] = _record_hash(rec)
        records.append(rec)
    total = len(data["report"]) + len(data["pages"])
    if len(records) + len(held) != total or len({r["dedupe_key"] for r in records}) != len(records):
        raise ValueError("Tennessee archive source row accounting mismatch")
    blanked = archive_dates.apply(records, "TN", directory)
    return records, held, {
        "implausible_dates_blanked": blanked,
        "source_rows": total, "report_rows": len(data["report"]),
        "page_records": len(data["pages"]), "admitted": len(records), "held": len(held),
        "admitted_report_rows": sum("report-by-month" in r["source_identity"] for r in records),
        "admitted_warn_numbers": len(page_numbers),
        "hold_reasons": dict(sorted(Counter(item["reason"] for item in held).items())),
        "admitted_workers": sum(r["employees_affected"] or 0 for r in records),
        "admitted_missing_workers": sum(r["employees_affected"] is None for r in records),
    }
