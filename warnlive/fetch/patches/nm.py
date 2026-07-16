"""New Mexico — patched from upstream warn-scraper nm.py.

The site moved from dws.state.nm.us to dws.nm.gov; the old host now serves
a WAF "Request Rejected" page to the upstream scraper (which then quietly
writes zero rows). Same PDF-table parsing as upstream, new domain, browser
User-Agent.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import pdfplumber
import requests
from bs4 import BeautifulSoup

from warn import utils
from warn.cache import Cache

logger = logging.getLogger(__name__)

BASE_URL = "https://www.dws.nm.gov/"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}


def scrape(
    data_dir: Path = utils.WARN_DATA_DIR,
    cache_dir: Path = utils.WARN_CACHE_DIR,
) -> Path:
    cache = Cache(cache_dir)
    r = requests.get(f"{BASE_URL}Rapid-Response", headers=HEADERS, timeout=120)
    r.raise_for_status()
    if "Request Rejected" in r.text:
        raise ValueError("NM: WAF rejected the request")
    cache.write("nm/Rapid-Response.html", r.text)

    document = BeautifulSoup(r.text, "html.parser")
    pdf_urls = [
        link["href"] if link["href"].startswith("http") else BASE_URL + link["href"].lstrip("/")
        for link in document.find_all("a", href=True)
        if "WARN" in link["href"] and link["href"].endswith(".pdf")
    ]
    if not pdf_urls:
        raise ValueError("NM: no WARN PDF links found")

    output_rows: list = []
    for pdf_index, pdf_url in enumerate(pdf_urls):
        file_name = os.path.basename(pdf_url)
        rp = requests.get(pdf_url, headers=HEADERS, timeout=120)
        rp.raise_for_status()
        pdf_path = cache.write_binary(f"nm/{file_name}", rp.content)
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                for row_index, row in enumerate(page.extract_table() or []):
                    if pdf_index > 0 and row_index == 0:
                        continue
                    cleaned = [_clean_text(cell) for cell in row]
                    if any(cell != "" for cell in cleaned):
                        output_rows.append(cleaned)

    data_path = data_dir / "nm.csv"
    utils.write_rows_to_csv(data_path, output_rows)
    return data_path


def _clean_text(text: str | None) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text)
