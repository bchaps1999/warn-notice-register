"""Georgia — patched from upstream warn-scraper ga.py.

tcsg.edu answers the index page and the admin-ajax table reliably, but the
per-notice detail pages it then fetches (one HTTP request per notice, cached
as .format3 files) time out intermittently — and upstream treats any single
failure as fatal, so the whole state dies on one flaky entry.

Patch: run the upstream scraper with utils.fetch_if_not_cached wrapped to
retry once with a shorter timeout and then *skip* the entry instead of
raising. The parse stage reads every cached .format3 file, so coverage
accumulates across runs — entries missed today download on the next run.
"""

from __future__ import annotations

import logging
import os
import random
from pathlib import Path
from time import sleep

from warn import utils
from warn.scrapers import ga as upstream_ga

logger = logging.getLogger(__name__)

DETAIL_TIMEOUT = 30


def scrape(
    data_dir: Path = utils.WARN_DATA_DIR,
    cache_dir: Path = utils.WARN_CACHE_DIR,
) -> Path:
    original = utils.fetch_if_not_cached
    skipped = 0

    def tolerant(filename, url, **kwargs):
        nonlocal skipped
        kwargs["timeout"] = DETAIL_TIMEOUT
        # Pace uncached downloads: tcsg.edu is fragile, and the backlog can
        # be thousands of pages — don't hit it with an unbroken stream.
        if not os.path.exists(filename):
            sleep(random.uniform(1, 2))
        for attempt in (1, 2):
            try:
                return original(filename, url, **kwargs)
            except Exception as exc:  # noqa: BLE001 — flaky host, skip entry
                if attempt == 2:
                    skipped += 1
                    logger.warning("GA: skipping detail page %s (%s)", url, exc)

    utils.fetch_if_not_cached = tolerant
    try:
        result = upstream_ga.scrape(data_dir, cache_dir)
    finally:
        utils.fetch_if_not_cached = original
    if skipped:
        logger.warning(
            "GA: %d detail pages skipped this run; they will be retried "
            "next run (parse uses all cached pages).", skipped
        )
    return result
