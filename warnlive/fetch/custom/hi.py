"""Hawaii WDC archive and WDD current WARN notices.

The WDD page labels agency receipt separately from the effective event. Its
receipt field is deliberately never written into the legacy ``Date`` column.
"""

from __future__ import annotations

import csv
import hashlib
import re
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from warn import utils

ARCHIVE_INDEX = "https://labor.hawaii.gov/wdc/real-time-warn-updates/"
CURRENT_INDEX = "https://labor.hawaii.gov/wdd/warn-notices/"
BASE_COLUMNS = ["Company", "Date", "PDF url", "location", "jobs"]
EVIDENCE_COLUMNS = [
    "source_kind", "source_page", "source_identity", "source_text",
    "archive_list_date", "document_role",
    "Date Department Received WARN", "Date of Closure or When Employees Will Be Affected",
    "Number of Affected Employees (Total)", "status_text", "count_text",
]
_YEAR_PATH = re.compile(r"^/wdc/(20\d{2})-warn-notices/$", re.I)
_DETAIL_PATH = re.compile(r"^/wdd/warn-notices/warn-notice-[^/]+/$", re.I)
_DATE = re.compile(r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+20\d{2}\b", re.I)


def _official(url: str, base: str) -> str | None:
    resolved = urljoin(base, url)
    parsed = urlparse(resolved)
    if parsed.scheme != "https" or parsed.hostname != "labor.hawaii.gov":
        return None
    return parsed._replace(fragment="", query="").geturl()


def _get(url: str) -> str:
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    return response.content.decode("utf-8", errors="replace")


def _main(html: str) -> BeautifulSoup:
    main = BeautifulSoup(html, "html.parser").select_one("div#container_main")
    if main is None:
        raise ValueError("HI: missing main source content")
    return main


def _archive_links(html: str) -> list[str]:
    links = set()
    for anchor in _main(html).select("a[href]"):
        url = _official(anchor["href"], ARCHIVE_INDEX)
        if url and _YEAR_PATH.fullmatch(urlparse(url).path) and re.fullmatch(
            r"20\d{2} WARN Notices", anchor.get_text(" ", strip=True), re.I
        ):
            links.add(url)
    if not links:
        raise ValueError("HI: no official yearly WARN archive links")
    return sorted(links)


def _detail_links(html: str) -> list[str]:
    links = set()
    for anchor in _main(html).select("a[href]"):
        url = _official(anchor["href"], CURRENT_INDEX)
        if url and _DETAIL_PATH.fullmatch(urlparse(url).path):
            links.add(url)
    if not links:
        raise ValueError("HI: no official WDD notice detail links")
    return sorted(links)


def _archive_rows(html: str, page_url: str) -> list[dict]:
    rows = []
    main = _main(html)
    for paragraph in main.select("p"):
        # Older yearly pages place several entries in one paragraph, split by br.
        for fragment in re.split(r"<br\s*/?>", str(paragraph), flags=re.I):
            node = BeautifulSoup(fragment, "html.parser")
            text = node.get_text(" ", strip=True)
            date_match = _DATE.search(text)
            if not date_match or date_match.start() != 0:
                continue
            listing = text[date_match.end():].lstrip(" –—-")
            status = text if re.search(
                r"\b(?:update|amend|rescind(?:ed|ing)?|correct|conditional|supplement)\b",
                text, re.I,
            ) else ""
            company = re.sub(r"^(?:Conditional WARN|UPDATE)\s*[–—-]\s*", "", listing, flags=re.I)
            company = re.split(
                r"\s*\((?:Additional Notice|\s*WARN Rescinded|update to previous WARN)\b",
                company, maxsplit=1, flags=re.I,
            )[0].strip(" –—-")
            documents = []
            for anchor in node.select("a[href]"):
                url = _official(anchor["href"], page_url)
                if not url or not urlparse(url).path.lower().endswith(".pdf"):
                    continue
                documents.append((url, anchor.get_text(" ", strip=True)))
            if not company or not documents:
                continue
            # A rescission PDF can be the only link on an otherwise dated
            # employer listing. It is a status document, not the employer.
            primary = next(
                ((url, label) for url, label in documents
                 if not re.search(r"\brescind(?:ed|ing)?\b", label, re.I)),
                documents[0],
            )
            day = datetime.strptime(date_match.group(), "%B %d, %Y").date().isoformat()
            role = (
                "rescission_attachment"
                if re.search(r"\brescind(?:ed|ing)?\b", primary[1], re.I)
                else "notice_attachment"
            )
            identity = hashlib.sha256(f"{page_url}|{day}|{company}|{text}".encode()).hexdigest()
            rows.append({
                "Company": company, "Date": "", "PDF url": primary[0],
                "location": "", "jobs": "", "source_kind": "wdc_archive",
                "source_page": page_url, "source_identity": f"HI:wdc:{identity}",
                "source_text": text, "archive_list_date": day,
                "document_role": role, "status_text": status,
            })
    if not rows:
        raise ValueError(f"HI: no PDF WARN entries on {page_url}")
    return rows


def _sections(main: BeautifulSoup) -> dict[str, str]:
    result = {}
    for heading in main.find_all(re.compile("^h[1-6]$")):
        label = heading.get_text(" ", strip=True)
        if not label:
            continue
        parts = []
        for sibling in heading.next_siblings:
            if getattr(sibling, "name", None) and re.fullmatch(r"h[1-6]", sibling.name):
                break
            if hasattr(sibling, "get_text"):
                value = sibling.get_text(" ", strip=True)
                if value:
                    parts.append(value)
        result[label] = " ".join(parts)
    return result


def _detail_row(html: str, url: str) -> dict:
    main = _main(html)
    sections = _sections(main)
    heading = BeautifulSoup(html, "html.parser").find("h1")
    if not heading or not heading.get_text(" ", strip=True).lower().startswith("warn notice"):
        raise ValueError(f"HI: missing WDD WARN heading on {url}")
    received = sections.get("Date Department Received WARN", "")
    event = sections.get("Date of Closure or When Employees Will Be Affected", "")
    count = sections.get("Number of Affected Employees (Total)", "")
    employer = sections.get("Company/Employer Information", "")
    if not received or not employer:
        raise ValueError(f"HI: missing required WDD fields on {url}")
    name = re.search(r"(?:Company Name|Seller):\s*(.*?)(?=\s+(?:Worksite Address|Address|Seller Corporate Office|Contact Person|Buyer):|$)", employer)
    if not name:
        raise ValueError(f"HI: missing employer name on {url}")
    address = re.search(r"Worksite Address:\s*(.*?)(?=\s+Contact Person:|\s+Contact information:|$)", employer)
    pdf = ""
    for anchor in main.select("a[href]"):
        candidate = _official(anchor["href"], url)
        if candidate and urlparse(candidate).path.lower().endswith(".pdf"):
            pdf = candidate
            break
    return {
        "Company": name.group(1).strip(), "Date": "", "PDF url": pdf,
        "location": address.group(1).strip() if address else "", "jobs": "",
        "source_kind": "wdd_detail", "source_page": url,
        "source_identity": f"HI:wdd:{urlparse(url).path}",
        "source_text": heading.get_text(" ", strip=True),
        "Date Department Received WARN": received,
        "Date of Closure or When Employees Will Be Affected": event,
        "Number of Affected Employees (Total)": count,
        "status_text": event, "count_text": count,
    }


def scrape(data_dir: Path = utils.WARN_DATA_DIR, cache_dir: Path = utils.WARN_CACHE_DIR) -> Path:
    # Fetch and validate the complete inventory before replacing any raw CSV.
    archive_index = _get(ARCHIVE_INDEX)
    current_index = _get(CURRENT_INDEX)
    archive_urls = _archive_links(archive_index)
    detail_urls = _detail_links(current_index)
    rows = []
    for url in archive_urls:
        rows.extend(_archive_rows(_get(url), url))
    for url in detail_urls:
        rows.append(_detail_row(_get(url), url))
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / "hi.csv"
    with tempfile.NamedTemporaryFile("w", dir=data_dir, newline="", encoding="utf-8", delete=False) as handle:
        pending = Path(handle.name)
        try:
            writer = csv.DictWriter(handle, fieldnames=BASE_COLUMNS + EVIDENCE_COLUMNS, extrasaction="raise")
            writer.writeheader()
            writer.writerows(rows)
        except BaseException:
            pending.unlink(missing_ok=True)
            raise
    pending.replace(target)
    return target
