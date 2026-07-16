"""Ohio — patched from upstream warn-scraper oh.py.

Upstream drops preamble lines by length (len > 20), but the Ohio CSV's
junk preamble includes a percent row longer than that, which then becomes
the DictReader header and mis-keys every current-year row (employers and
dates end up under '13.00%'-style columns). We instead skip everything
before the real header line, which starts with 'Company,'.
"""

from __future__ import annotations

import csv
import logging
import re
from io import StringIO
from pathlib import Path

import requests

from warn import utils
from warn.cache import Cache

logger = logging.getLogger(__name__)

CURRENT_URL = (
    "https://jfs.ohio.gov/job-workforce-services/job-programs-and-services/"
    "submit-a-warn-notice/current-public-notices-of-layoffs-and-closures"
)
HISTORICAL_URL = (
    "https://storage.googleapis.com/bln-data-public/warn-layoffs/oh_historical.csv"
)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/117.0",
}


def scrape(
    data_dir: Path = utils.WARN_DATA_DIR,
    cache_dir: Path = utils.WARN_CACHE_DIR,
) -> Path:
    cache = Cache(cache_dir)

    r = requests.get(CURRENT_URL, headers=HEADERS, timeout=120)
    cache.write("oh/index.html", r.text)
    csv_url = re.findall(r"(\\\"csvUrl\\\":\\\")(.*?)(\\\")", r.text)[0][1]
    logger.debug("Ohio CSV link: %s", csv_url)
    r = requests.get(csv_url, headers=HEADERS, timeout=120)
    cache.write("oh/rawdata.csv", r.text)

    # Skip the junk preamble: keep from the real header line onward.
    lines = r.text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("Company,"))
    masterlist = list(csv.DictReader(StringIO("\n".join(lines[start:]))))
    if not masterlist:
        raise ValueError("Ohio current CSV parsed to zero rows")

    # Meld in the historical mirror (2017-2022), same as upstream.
    lookup = {
        "Company": "Company",
        "DateReceived": "Date Received",
        "City/County": "City/County",
        "Potential NumberAffected": "Potential Number Affected",
        "LayoffDate(s)": "Layoff Date(s)",
        "PhoneNumber": "Phone Number",
        "Union": "Union",
        "Notice ID": "Notice ID",
    }
    r = requests.get(HISTORICAL_URL, timeout=120)
    for row in csv.DictReader(r.text.splitlines()):
        masterlist.append({new: row[old] for old, new in lookup.items()})

    data_path = data_dir / "oh.csv"
    utils.write_disparate_dict_rows_to_csv(data_path, masterlist)
    return data_path
