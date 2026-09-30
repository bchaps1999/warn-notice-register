"""Complete Delaware JobLink WARN portal capture (see ``job_portal``)."""

from __future__ import annotations

from pathlib import Path

from warnlive.fetch.custom.job_portal import scrape_portal


def scrape(data_dir: Path, cache_dir: Path) -> Path:
    return scrape_portal("de", data_dir, cache_dir)
