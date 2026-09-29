"""West Virginia — WorkForce West Virginia WARN listing (custom adapter).

Source: https://workforcewv.org/businesses/layoffs-downsizing/warn-listing/
(verified 2026-09-29). The agency page groups links under ``<details>``
blocks titled ``YYYY WARN Listings``. Two kinds of artifact are linked:

* one agency-compiled summary PDF, ``WEST VIRGINIA WARN NOTICES 1/1/22 to
  1/3/25``, with a labeled block per notice (Company, Address, Region,
  County, Date of Notice, Projected Date, Closure/Mass Layoff, Number
  Affected). The agency states that values set in bold are changes from the
  original notice; those fields are recorded in ``bold_fields``.
* one PDF per employer notice. Nearly all are image scans with no text
  layer, so the only machine-readable facts are the agency's link label and
  the PDF URL. The label's date token has no stated role (several labels
  differ from the summary's Date of Notice), so it is kept as
  ``listing_title_date`` and never used as a notice date.

The per-notice PDFs for 2022-2024 overlap the summary PDF's window. Their
labels cannot be tied to a summary entry without a name/date match, which
this register does not use as identity, and at least one (Kroger Gassaway,
10-28-22) has no summary entry. Documents whose label date (or, if the label
has no day date, listing section year) falls inside the summary window are
therefore written to ``wv.listing_review.csv`` with a reason instead of the
raw CSV, so they cannot double-count a summary entry. Every link is
accounted for in one of the two files. A label with no day date is held when
its listing section year falls in the window (old notices were re-uploaded in
bulk in 2025, so upload folders do not date them).

Contact names, phone numbers and e-mail addresses in the summary are not
copied; the cached PDF retains them.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pdfplumber
import requests
from bs4 import BeautifulSoup

from warn import utils

logger = logging.getLogger(__name__)

PAGE_URL = "https://workforcewv.org/businesses/layoffs-downsizing/warn-listing/"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
COLUMNS = [
    "record_kind", "source_identity", "company", "address", "region", "county",
    "notice_date", "projected_date", "action", "affected", "entry_note",
    "bold_fields", "listing_section", "listing_title", "listing_title_date",
    "revision_marker", "document_role", "source_url", "source_sha256",
    "source_page", "source_row",
]
REVIEW_COLUMNS = COLUMNS + ["review_reason"]

# Summary block labels, in source order, mapped to output columns.
_LABELS = [
    ("Company", "company"),
    ("Address", "address"),
    ("Contact Information", None),  # personal contact data: not copied
    ("Region", "region"),
    ("County", "county"),
    ("Date of Notice", "notice_date"),
    ("Projected Date", "projected_date"),
    ("Closure/Mass Layoff", "action"),
    ("Number Affected", "affected"),
]
_MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|"
    "November|December"
)
_MONTH_HEADING = re.compile(rf"^(?:{_MONTHS})\s+\d{{4}}$")
_PAGE_BOILERPLATE = re.compile(
    r"^(?:WEST VIRGINIA WARN NOTICES|\d{1,2}/\d{1,2}/\d{2,4} to \d{1,2}/\d{1,2}/\d{2,4}|"
    r"Organized by notice date\.|Note: Entries in bold represent the changes from the original|"
    r"notice\.|Page \d+ of \d+)$"
)
_WINDOW = re.compile(r"^(\d{1,2}/\d{1,2}/\d{2,4}) to (\d{1,2}/\d{1,2}/\d{2,4})$")
# Day-precision date tokens in link labels: 8-19-26, 6-4-2021, 04_1_2026, WARN5-12-23.
_TITLE_DAY = re.compile(r"(?<!\d)(\d{1,2})[-_](\d{1,2})[-_](\d{4}|\d{2})(?!\d)")
_TITLE_MONTH = re.compile(r"(?<![\d-])(\d{2})-(\d{4})(?!\d)")
_SUMMARY_LINK = re.compile(r"WV-WARN-Notices", re.I)


def scrape(
    data_dir: Path = utils.WARN_DATA_DIR,
    cache_dir: Path = utils.WARN_CACHE_DIR,
) -> Path:
    """Fetch the listing and linked PDFs; write wv.csv and wv.listing_review.csv."""
    root = Path(cache_dir) / "wv"
    root.mkdir(parents=True, exist_ok=True)
    retrieved = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    response = requests.get(PAGE_URL, headers=HEADERS, timeout=120)
    response.raise_for_status()
    html = response.text
    (root / "listing.html").write_text(html)
    manifest = [_manifest_entry(PAGE_URL, "listing.html", html.encode(), retrieved)]

    links = parse_listing(html)
    for link in links:
        local = root / link["cache_path"]
        # Upload URLs are immutable WordPress media paths; reuse a cached copy.
        if not local.is_file():
            pdf = requests.get(link["source_url"], headers=HEADERS, timeout=120)
            pdf.raise_for_status()
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(pdf.content)
        manifest.append(_manifest_entry(
            link["source_url"], link["cache_path"], local.read_bytes(), retrieved,
        ))
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    rows, review = build_rows(links, root)
    return _write(Path(data_dir), rows, review)


def cached_csv(cache_dir: Path, out_dir: Path) -> tuple[int, int]:
    """Rebuild wv.csv/wv.listing_review.csv from a cached capture, no network.

    ``cache_dir`` is the ``wv`` capture directory (listing.html plus PDFs).
    Writes only under ``out_dir``. Returns (raw rows, review rows).
    """
    root = Path(cache_dir)
    links = parse_listing((root / "listing.html").read_text())
    rows, review = build_rows(links, root)
    _write(Path(out_dir), rows, review)
    return len(rows), len(review)


def parse_listing(html: str) -> list[dict[str, str]]:
    """Every PDF link inside a ``YYYY WARN Listings`` block, in page order."""
    soup = BeautifulSoup(html, "html.parser")
    links: list[dict[str, str]] = []
    for block in soup.find_all("details"):
        summary = block.find("summary")
        section = " ".join(summary.get_text(" ").split()) if summary else ""
        if not re.fullmatch(r"\d{4} WARN Listings", section):
            continue
        for anchor in block.find_all("a", href=True):
            href = anchor["href"]
            if not urlparse(href).path.lower().endswith(".pdf"):
                continue
            url = urljoin(PAGE_URL, href)
            path = urlparse(url).path
            links.append({
                "listing_section": section,
                "listing_title": " ".join(anchor.get_text(" ").split()),
                "source_url": url,
                "cache_path": "pdf/" + path.split("/wp-content/uploads/", 1)[-1],
            })
    if not links:
        raise ValueError("WV: no PDF links found under 'YYYY WARN Listings' blocks")
    if not any(_SUMMARY_LINK.search(link["source_url"]) for link in links):
        raise ValueError("WV: agency summary PDF link (WV-WARN-Notices) not found")
    return links


def build_rows(
    links: list[dict[str, str]], root: Path,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Split links into raw notice rows and held listing documents."""
    summary_links = [link for link in links if _SUMMARY_LINK.search(link["source_url"])]
    if len(summary_links) != 1:
        raise ValueError(f"WV: expected one summary PDF link, found {len(summary_links)}")
    summary = summary_links[0]
    summary_path = root / summary["cache_path"]
    pages = summary_pdf_lines(summary_path)
    window, entries = parse_summary_lines(pages)
    sha = _sha256(summary_path)
    rows: list[dict[str, str]] = []
    for ordinal, entry in enumerate(entries, 1):
        path = urlparse(summary["source_url"]).path
        rows.append(_blank() | entry | {
            "record_kind": "summary_entry",
            "source_identity": f"WV:{path}#entry-{ordinal}",
            "listing_section": summary["listing_section"],
            "listing_title": summary["listing_title"],
            "source_url": summary["source_url"],
            "source_sha256": sha,
            "source_row": str(ordinal),
        })

    review: list[dict[str, str]] = []
    for link in links:
        if link is summary:
            continue
        row = _blank() | listing_document(link)
        row["source_sha256"] = _sha256(root / link["cache_path"])
        reason = window_review_reason(row, window)
        if reason:
            review.append(row | {"review_reason": reason})
        else:
            rows.append(row)
    return rows, review


def summary_pdf_lines(path: Path) -> list[list[tuple[str, int]]]:
    """Per page, each text line with its count of bold glyphs."""
    pages: list[list[tuple[str, int]]] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            pages.append([
                (line["text"], sum("Bold" in c["fontname"] for c in line["chars"]))
                for line in page.extract_text_lines()
            ])
    return pages


def parse_summary_lines(
    pages: list[list[tuple[str, int]]],
) -> tuple[tuple[date, date], list[dict[str, str]]]:
    """Parse the summary PDF's labeled blocks.

    Returns the stated coverage window and one dict per ``Company`` block.
    A value continues onto following unlabeled lines (addresses, per-site
    counts, second date ranges), including across a page break. A non-month
    line immediately before a block (``Update``, ``Postponement of ...``)
    is kept as ``entry_note``.
    """
    window: tuple[date, date] | None = None
    entries: list[dict[str, str]] = []
    current: dict | None = None
    field: str | None = None
    pending_note: list[str] = []
    for page_number, lines in enumerate(pages, 1):
        for text, bold in lines:
            text = " ".join(text.split())
            match = _WINDOW.fullmatch(text)
            if match and window is None:
                window = (_parse_day(match.group(1)), _parse_day(match.group(2)))
            if not text or _PAGE_BOILERPLATE.fullmatch(text):
                continue
            label = next((lab for lab, _ in _LABELS if text == lab or text.startswith(lab + " ")), None)
            if label is None:
                if _MONTH_HEADING.fullmatch(text):
                    field = None
                    continue
                if field is None:
                    # Text between a month heading and a block describes it.
                    pending_note.append(text)
                    continue
                if field != "contact":
                    current["values"][field].append(text)
                    if bold:
                        current["bold"].append(field)
                continue
            column = dict(_LABELS)[label]
            value = text[len(label):].strip()
            if label == "Company":
                current = {"values": {c: [] for _, c in _LABELS if c}, "bold": [],
                           "note": pending_note, "page": page_number}
                pending_note = []
                entries.append(current)
            if current is None:
                raise ValueError(f"WV summary: '{label}' line before any Company")
            field = column or "contact"
            if column is None:
                continue
            if value:
                current["values"][column].append(value)
            # Labels are set in bold; any further bold glyph is in the value.
            if bold > len(label.replace(" ", "")):
                current["bold"].append(column)
    if window is None or None in window:
        raise ValueError("WV summary: coverage window line not found")
    if pending_note:
        raise ValueError(f"WV summary: trailing text after last block: {pending_note}")
    out: list[dict[str, str]] = []
    for entry in entries:
        values = entry["values"]
        # Address and per-site count lines are separate source lines; other
        # values only wrap (e.g. a long county list).
        row = {column: ("; " if column in ("address", "affected") else " ").join(parts)
               for column, parts in values.items()}
        row["entry_note"] = "; ".join(entry["note"])
        row["bold_fields"] = ";".join(entry["bold"])
        row["source_page"] = str(entry["page"])
        if not row["company"] or not row["notice_date"]:
            raise ValueError(f"WV summary: block without company/date: {row}")
        out.append(row)
    return window, out


def listing_document(link: dict[str, str]) -> dict[str, str]:
    """A raw row for one per-notice PDF, from the agency's link label only."""
    title = link["listing_title"]
    day = title_day(title)
    month = _TITLE_MONTH.search(title) if day is None else None
    revision = re.search(r"\br(\d+)\b", title)
    role = "amendment" if re.search(r"\b(?:update|supplemental)\b", title.replace("_", " "), re.I) else ""
    return {
        "record_kind": "listing_document",
        "source_identity": f"WV:{urlparse(link['source_url']).path}",
        "company": label_employer(title),
        "listing_section": link["listing_section"],
        "listing_title": title,
        "listing_title_date": (
            day.group(0) if day else month.group(0) if month else ""
        ),
        "revision_marker": revision.group(0) if revision else "",
        "document_role": role,
        "source_url": link["source_url"],
    }


def label_employer(title: str) -> str:
    """Employer text from a link label: drop WARN/notice words and date tokens.

    The label is agency-written free text. This removes only document words
    (WARN, Notice, State, Supplemental, Update, Received), date tokens and
    revision markers; it does not correct spellings or resolve facility
    labels (e.g. "Beckley Mechanic Shop") to a parent employer.
    """
    text = _TITLE_DAY.sub(" ", title.replace("WARN", " WARN ")).replace("_", " ")
    text = re.sub(r"^\s*WARN\s+Notice(?:\s+State)?\s*[–—-]\s*(?:West Virginia\s+)?", "", text, flags=re.I)
    text = _TITLE_MONTH.sub(" ", text)
    text = re.sub(rf"\b(?:{_MONTHS})\s+(?=WARN\b)", " ", text)
    text = re.sub(r"\b(?:WARN|Notice|State|Supplemental|Update|Received|r\d+)\b", " ", text, flags=re.I)
    text = " ".join(text.split())
    return text.strip(" -–—,")


def title_day(title: str) -> re.Match | None:
    """A day-precision date token in a link label, if it is a valid date."""
    for match in _TITLE_DAY.finditer(title):
        if _token_day(match) is not None:
            return match
    return None


def window_review_reason(
    row: dict[str, str], window: tuple[date, date],
) -> str | None:
    """Hold a listing document that may duplicate a summary entry."""
    start, end = window
    day = _TITLE_DAY.search(row["listing_title_date"] or "")
    when = _token_day(day) if day else None
    if when is not None:
        if start <= when <= end:
            return "label_date_inside_summary_window"
        return None
    # Old notices were re-uploaded in bulk (2025/02), so the upload folder
    # says nothing about the notice. The listing section year is the only
    # other agency grouping; hold any undated label in a year the summary
    # window reaches.
    section = re.match(r"(\d{4})", row["listing_section"] or "")
    if section is None:
        return "undated_label_without_listing_year"
    if int(section.group(1)) <= end.year:
        return "undated_label_in_summary_window_year"
    return None


def _token_day(match: re.Match) -> date | None:
    month, day, year = (int(g) for g in match.groups())
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _parse_day(text: str) -> date | None:
    for fmt in ("%m/%d/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def _blank() -> dict[str, str]:
    return {column: "" for column in COLUMNS}


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _manifest_entry(url: str, path: str, content: bytes, retrieved: str) -> dict:
    return {"url": url, "path": path, "retrieved_at": retrieved,
            "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}


def _write(
    out_dir: Path, rows: list[dict[str, str]], review: list[dict[str, str]],
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "wv.csv"
    with out.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    with (out_dir / "wv.listing_review.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        writer.writerows(review)
    return out
