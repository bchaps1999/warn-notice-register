"""Historical backfill from biglocalnews/warn-github-flow data branches.

Each state has a branch named after its postal code containing the raw
scraper export at data/warn-scraper/exports/{state}.csv. We download that
file and push it through the exact same normalize -> verify -> ingest path
as a live scrape, with trigger='backfill'.
"""

from __future__ import annotations

import logging
import urllib.request
from pathlib import Path

RAW_URL = (
    "https://raw.githubusercontent.com/biglocalnews/warn-github-flow/"
    "{postal}/data/warn-scraper/exports/{postal}.csv"
)

logger = logging.getLogger("warnlive")


def download_state(postal: str, dest_dir: Path) -> Path | None:
    """Download one state's historical raw CSV. Returns None if the branch
    or file doesn't exist (custom states have no upstream history)."""
    postal = postal.lower()
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{postal}.csv"
    url = RAW_URL.format(postal=postal)
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            dest.write_bytes(resp.read())
    except Exception as e:  # noqa: BLE001
        logger.warning("backfill %s: could not fetch %s (%s)", postal, url, e)
        return None
    return dest
