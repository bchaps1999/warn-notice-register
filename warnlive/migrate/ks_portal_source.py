"""Kansas (KANSASWORKS) bindings of the shared job-portal WARN capture.

The capture, staging and freezing logic lives in ``job_portal_source``; this
module binds it to the Kansas portal for existing callers. Command line:
``python -m warnlive.migrate.job_portal_source ks --out <dir> ...``.
"""

from __future__ import annotations

from pathlib import Path

from warnlive.migrate import job_portal_source as _portal

PORTAL = _portal.PORTALS["ks"]
BASE = PORTAL.base
SEARCH = PORTAL.search
suspected_variant_pairs = _portal.suspected_variant_pairs


def parse_listing(html: bytes) -> tuple[list[dict], int]:
    return _portal.parse_listing(html, PORTAL)


def parse_detail(html: bytes) -> dict[str, str]:
    return _portal.parse_detail(html, PORTAL)


def capture(out_dir: Path, **options) -> dict:
    return _portal.capture(out_dir, PORTAL, **options)


def stage_raw(out_dir: Path) -> dict[str, int]:
    return _portal.stage_raw(out_dir, PORTAL)


def freeze_capture(out_dir: Path, archive_path: Path) -> dict:
    return _portal.freeze_capture(out_dir, archive_path, PORTAL)
