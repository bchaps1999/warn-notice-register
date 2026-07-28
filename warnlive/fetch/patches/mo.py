"""Missouri — patched from upstream warn-scraper mo.py.

jobs.mo.gov sits behind Incapsula bot protection, which serves plain HTTP
clients a JavaScript-challenge iframe instead of the per-year WARN tables.
A real browser passes the challenge automatically.

Patch: run the upstream scraper with utils.get_url wrapped — plain HTTP
first, and when the response is the Incapsula challenge (or errors), load
the page in headless Chrome and hand back its rendered HTML. One Chrome
instance is shared across the year loop; upstream's own caching keeps old
years from refetching at all.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from time import sleep, time

from warn import utils
from warn.scrapers import mo as upstream_mo

logger = logging.getLogger(__name__)

CHALLENGE_MARKER = "_Incapsula_Resource"


@dataclass
class _Response:
    text: str


def scrape(
    data_dir: Path = utils.WARN_DATA_DIR,
    cache_dir: Path = utils.WARN_CACHE_DIR,
) -> Path:
    driver_holder: list = []
    original = utils.get_url

    def patched(url, **kwargs):
        try:
            # Upstream's kwargs ride along; only the timeout is overridden.
            # Dropping them would silently ignore any params or headers a
            # future upstream revision starts passing.
            r = original(url, **{**kwargs, "timeout": 30})
            if r.ok and CHALLENGE_MARKER not in r.text:
                return r
            logger.info("MO: %s served the Incapsula challenge; using Chrome", url)
        except Exception as exc:  # noqa: BLE001 — fall through to the browser
            logger.info("MO: plain fetch of %s failed (%s); using Chrome", url, exc)
        return _Response(_browser_get(driver_holder, url))

    utils.get_url = patched
    try:
        return upstream_mo.scrape(data_dir, cache_dir)
    finally:
        utils.get_url = original
        if driver_holder:
            driver_holder[0].quit()


def _browser_get(driver_holder: list, url: str) -> str:
    """Fetch url in a shared headless-Chrome instance, waiting out the
    Incapsula challenge; raises if the table never appears."""
    if not driver_holder:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options as ChromeOptions

        options = ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(60)
        driver_holder.append(driver)
    driver = driver_holder[0]

    # Two attempts: Incapsula sometimes serves a heavier challenge on the
    # first load that clears on a fresh navigation.
    for attempt in (1, 2):
        driver.get(url)
        deadline = time() + 75
        html = driver.page_source
        while "<table" not in html and time() < deadline:
            sleep(2)
            html = driver.page_source
        if "<table" in html:
            return html
        logger.warning("MO: challenge did not clear for %s (attempt %d)", url, attempt)
        sleep(10)
    raise Exception(f"MO: Incapsula challenge did not clear for {url}")
