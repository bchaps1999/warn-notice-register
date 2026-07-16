"""Fetch a state's raw CSV. Dispatch order: patches -> custom -> upstream.

Every fetcher follows the warn-scraper contract:
    scrape(data_dir, cache_dir) -> Path to the raw {state}.csv
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path


def fetch_state(postal: str, data_dir: Path, cache_dir: Path) -> Path:
    postal = postal.lower()
    data_dir = Path(data_dir)
    cache_dir = Path(cache_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    for package in ("warnlive.fetch.patches", "warnlive.fetch.custom"):
        try:
            mod = import_module(f"{package}.{postal}")
        except ModuleNotFoundError:
            continue
        return Path(mod.scrape(data_dir, cache_dir))

    mod = import_module(f"warn.scrapers.{postal}")
    return Path(mod.scrape(data_dir, cache_dir))
