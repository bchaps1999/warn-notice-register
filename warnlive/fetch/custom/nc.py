"""North Carolina — custom adapter (not covered by warn-scraper).

NC Commerce publishes a Salesforce-backed CSV, re-uploaded with a dated
filename on each refresh, linked from a year-specific report page. We scrape
the current year's report page and follow the files.nc.gov CSV link.
Archive years (2014-2025) exist only as PDFs and are not fetched here.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from warn import utils
from warn.cache import Cache

logger = logging.getLogger(__name__)

REPORT_PAGE = (
    "https://www.commerce.nc.gov/data-tools-reports/labor-market-data-tools/"
    "workforce-warn-reports/report-workforce-warn-summary-list-{year}"
)
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}


def scrape(
    data_dir: Path = utils.WARN_DATA_DIR,
    cache_dir: Path = utils.WARN_CACHE_DIR,
) -> Path:
    cache = Cache(cache_dir)
    year = date.today().year

    # The current-year page may not exist yet in early January; fall back.
    for y in (year, year - 1):
        r = requests.get(REPORT_PAGE.format(year=y), headers=HEADERS, timeout=120)
        if r.ok:
            break
    r.raise_for_status()
    cache.write("nc/report_page.html", r.text)

    soup = BeautifulSoup(r.text, "html5lib")
    link = next(
        (
            a["href"]
            for a in soup.find_all("a", href=True)
            if "files.nc.gov" in a["href"] and ".csv" in a["href"].lower()
        ),
        None,
    )
    if link is None:
        raise ValueError("NC: no files.nc.gov CSV link found on report page")
    logger.debug("NC CSV link: %s", link)

    r = requests.get(link, headers=HEADERS, timeout=120)
    r.raise_for_status()
    data_path = data_dir / "nc.csv"
    data_path.write_bytes(r.content)
    cache.write("nc/latest.csv", r.text)
    return data_path
