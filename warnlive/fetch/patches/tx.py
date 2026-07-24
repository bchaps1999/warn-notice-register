"""Texas — patched from upstream warn-scraper tx.py.

The TWC index page (twc.texas.gov/data-reports/warn-notice) is
intermittently blocked from GitHub runner IPs (Cloudflare; upstream #767),
which makes link discovery return zero spreadsheet links and the upstream
scraper raise. But the yearly workbook URLs are predictable
(warn-act-listings-{year}-twc.xlsx) and the assets themselves have not been
blocked — same situation as MN, where only the HTML is fenced off. So:
try upstream-style discovery first, and when it yields nothing, probe the
constructed per-year URLs directly. Downloads are validated as real
xlsx (zip magic) so a challenge page can't masquerade as a workbook.
"""

from __future__ import annotations

import logging
import random
import re
from datetime import date
from pathlib import Path
from time import sleep

import niquests as requests
from bs4 import BeautifulSoup
from openpyxl import load_workbook

from warn import utils
from warn.cache import Cache

logger = logging.getLogger(__name__)

INDEX_URL = "https://www.twc.texas.gov/data-reports/warn-notice"
ASSET_ROOT = "https://www.twc.texas.gov"
# Years covered by the yearly workbooks; earlier years come from the BLN
# historical file. Upstream keeps 2019+, but the index currently lists
# 2020+ with a -twc suffix; older names lacked it, so probe both.
FIRST_YEAR = 2019
HISTORICAL_URL = (
    "https://storage.googleapis.com/bln-data-public/warn-layoffs/tx_historical.xlsx"
)

XLSX_MAGIC = b"PK\x03\x04"


def scrape(
    data_dir: Path = utils.WARN_DATA_DIR,
    cache_dir: Path = utils.WARN_CACHE_DIR,
) -> Path:
    cache = Cache(cache_dir)
    session = requests.Session()

    hrefs = _discover_links(session, cache)
    if hrefs:
        candidates = [(_get_year(h), [f"{ASSET_ROOT}{h}"]) for h in hrefs]
    else:
        logger.warning(
            "TX index page yielded no spreadsheet links (likely blocked); "
            "falling back to constructed per-year URLs."
        )
        candidates = [
            (
                year,
                [
                    f"{ASSET_ROOT}/sites/default/files/oei/docs/warn-act-listings-{year}-twc.xlsx",
                    f"{ASSET_ROOT}/sites/default/files/oei/docs/warn-act-listings-{year}.xlsx",
                ],
            )
            for year in range(FIRST_YEAR, date.today().year + 1)
        ]

    row_list: list[list] = []
    workbooks = 0
    for year, urls in candidates:
        excel_path = _download_year(session, cache_dir, year, urls)
        if excel_path is None:
            continue
        worksheet = load_workbook(filename=excel_path).worksheets[0]
        for irow, row in enumerate(worksheet.rows):
            if workbooks > 0 and irow == 0:
                continue  # keep the header only from the first workbook
            cell_list = [cell.value for cell in row]
            if cell_list[0] is None:
                continue
            row_list.append(cell_list)
        workbooks += 1

    if workbooks == 0:
        raise Exception(
            "TX: no yearly workbooks retrievable via discovery or constructed URLs."
        )

    # Workbooks vary in trailing empty columns; strip them from the header so
    # the emitted CSV header doesn't depend on which year came first.
    header = row_list[0]
    while header and header[-1] in (None, ""):
        header.pop()

    # Historical data (pre-2019) from BLN's archived workbook, trimmed to the
    # same columns as the yearly files — unchanged from upstream.
    excel_path = cache.download("tx/historical.xlsx", HISTORICAL_URL)
    worksheet = load_workbook(filename=excel_path).worksheets[0]
    for i, row in enumerate(worksheet.rows):
        if i == 0:
            continue
        select_columns = [
            row[8],  # NOTICE_DATE
            row[0],  # JOB_SITE_NAME
            row[2],  # COUNTY_NAME
            row[5],  # WDA_NAME
            row[6],  # TOTAL_LAYOFF_NUMBER
            row[7],  # LayOff_Date
            row[11],  # WFDD_RECEIVED_DATE
            row[1],  # CITY_NAME
        ]
        row_list.append([c.value for c in select_columns])

    data_path = data_dir / "tx.csv"
    utils.write_rows_to_csv(data_path, row_list)
    return data_path


def _get_year(url: str) -> int:
    """Plucks the year from a workbook URL (upstream logic)."""
    m = re.match(r".*-(\d{4})(.*)$", url, re.I)
    assert m is not None
    return int(m.group(1)[-4:])


def _discover_links(session: requests.Session, cache: Cache) -> list[str]:
    """Upstream-style link discovery; returns [] instead of raising."""
    try:
        page = session.get(INDEX_URL, timeout=60)
        logger.debug("TX index page status %s", page.status_code)
        html = page.text
    except Exception as exc:  # blocked/reset — the fallback handles it
        logger.warning("TX index page fetch failed: %s", exc)
        return []
    cache.write("tx/source.html", html)
    soup = BeautifulSoup(html, "html5lib")
    link_list = soup.find_all(
        "a", href=re.compile("^/sites/default/files/oei/docs/warn-act-listings-")
    )
    hrefs = [link.get("href") for link in link_list]
    return [h for h in hrefs if _get_year(h) >= FIRST_YEAR]


def _download_year(
    session: requests.Session, cache_dir: Path, year: int, urls: list[str]
) -> Path | None:
    """Fetch the first URL that returns a genuine xlsx; None if none do."""
    for url in urls:
        try:
            r = session.get(url, timeout=60)
        except Exception as exc:
            logger.warning("TX %s: fetch failed (%s)", year, exc)
            continue
        sleep(random.uniform(2, 4))
        if r.status_code != 200 or not r.content.startswith(XLSX_MAGIC):
            logger.info(
                "TX %s: %s -> status %s, not a workbook", year, url, r.status_code
            )
            continue
        excel_path = cache_dir / f"tx/{year}.xlsx"
        excel_path.parent.mkdir(parents=True, exist_ok=True)
        excel_path.write_bytes(r.content)
        return excel_path
    logger.warning("TX %s: no retrievable workbook", year)
    return None
