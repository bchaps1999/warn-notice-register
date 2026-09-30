"""Blank dates outside the live path's plausibility window in archive projections.

The live scrape reads every date through warn-transformer, whose
``transform_date`` refuses a year below the state transformer's
``minimum_year`` (1988 by default; Kentucky 1997) or a day more than
``max_future_days`` (365 by default) after the run, and ``normalize.engine``
blanks the field and keeps the row. The replay-only archive projectors
(``agency/{ky,tn,la,mi,ct,ia,oh}_archive`` and ``agency/<postal>_portal``)
parse their own cells, so they apply the same window here: a parsed date
outside it is blanked, never guessed, and the row stays admitted.

The upper bound is anchored to the capture date of the artifact the row was
read from (``manifest.json`` in the pinned directory: an artifact's
``capture_utc`` or ``retrieved_live_utc``, else the portal capture's
``capture_end_utc``), a fixed reference, so a replay does not depend on the
day it runs. A blanked value is recorded in ``source_details.parse_notes``
(rule ``archive_date_plausibility_window_v1``, reason
``implausible_date_blanked``) with the window and capture date; the source
cell itself stays in ``raw_extra`` (and ``raw_fields`` where a projector keeps
one). Canonical fields (``notice_date``, ``effective_date``,
``effective_date_end``) lose their precision and basis with the value; parsed
date roles in ``source_details`` (``agency_received_date`` and the like,
including Iowa's ``amendment``/``amendments[]`` dates) are removed.

Pure functions over projector output plus one manifest read; no network access.
"""

from __future__ import annotations

import importlib
import json
import re
from datetime import date, timedelta
from pathlib import Path

from warnlive.normalize.engine import _record_hash

RULE = "archive_date_plausibility_window_v1"
REASON = "implausible_date_blanked"
DEFAULT_MINIMUM_YEAR = 1988
DEFAULT_MAX_FUTURE_DAYS = 365
CANONICAL_FIELDS = ("notice_date", "effective_date", "effective_date_end")
# Parsed date roles the archive projectors write into source_details. Raw
# cells (raw_fields, source_text, parse_notes) are never touched.
DETAIL_FIELDS = frozenset({
    "agency_received_date", "agency_posted_date", "agency_report_notice_date",
    "projected_action_date", "projected_action_date_end",
    "amendment_notice_date", "reported_layoff_date",
})
_SKIP_DETAILS = frozenset({"raw_fields", "parse_notes", "source_text"})
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")


def window(state: str, capture: date) -> tuple[date, date]:
    """The live transformer's bounds for ``state``, anchored to ``capture``."""
    minimum, future = DEFAULT_MINIMUM_YEAR, DEFAULT_MAX_FUTURE_DAYS
    try:
        module = importlib.import_module(f"warn_transformer.transformers.{state.lower()}")
        minimum = getattr(module.Transformer, "minimum_year", minimum)
        future = getattr(module.Transformer, "max_future_days", future)
    except (ImportError, AttributeError):
        pass
    return date(minimum, 1, 1), capture + timedelta(days=future)


def capture_dates(directory: Path) -> tuple[dict[str, date], date | None]:
    """Capture day of each artifact in a pinned directory, plus a directory default."""
    manifest = json.loads((Path(directory) / "manifest.json").read_text())
    by_file: dict[str, date] = {}
    for item in manifest.get("artifacts") or []:
        stamp = item.get("capture_utc") or item.get("retrieved_live_utc")
        if item.get("file") and stamp:
            by_file[item["file"]] = date.fromisoformat(stamp[:10])
    capture = manifest.get("capture") or {}
    stamp = capture.get("capture_end_utc") or capture.get("capture_start_utc")
    default = date.fromisoformat(stamp[:10]) if stamp else None
    return by_file, default


def _artifact(details: dict) -> str | None:
    ref = details.get("source_artifact") or details.get("origin")
    return ref.rsplit("/", 1)[-1] if ref else None


def _outside(value, low: date, high: date) -> bool:
    if not isinstance(value, str) or not _ISO.fullmatch(value):
        return False
    return not (low <= date.fromisoformat(value) <= high)


def _blank_details(node, low: date, high: date, path: str, notes: list[dict]):
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            here = f"{path}.{key}"
            if key in _SKIP_DETAILS:
                out[key] = value
            elif key in DETAIL_FIELDS and _outside(value, low, high):
                notes.append({"field": here, "value": value})
            else:
                out[key] = _blank_details(value, low, high, here, notes)
        return out
    if isinstance(node, list):
        return [_blank_details(item, low, high, f"{path}[{index}]", notes)
                for index, item in enumerate(node)]
    return node


def apply(records: list[dict], state: str, directory: Path,
          fallback: date | None = None) -> list[dict]:
    """Blank out-of-window dates in ``records`` in place; return what was blanked.

    Each returned entry names the state, source row, field, blanked value,
    window and capture day. A record whose artifact has no capture date in
    the manifest (no directory default and no ``fallback``) raises: the
    window cannot be anchored without one. ``fallback`` is for a projector
    whose rows were already read under an observed day (the job portals
    normalize through ``normalize.engine`` at the replay's observed day).
    """
    by_file, default = capture_dates(directory)
    default = default or fallback
    blanked: list[dict] = []
    for rec in records:
        details = json.loads(rec.get("source_details") or "{}")
        name = _artifact(details)
        capture = by_file.get(name) if name else None
        capture = capture or default
        if capture is None:
            raise ValueError(f"{state}: no capture date for artifact {name!r} in {directory}")
        low, high = window(state, capture)
        found: list[dict] = []
        for field in CANONICAL_FIELDS:
            if _outside(rec.get(field), low, high):
                found.append({"field": field, "value": rec[field]})
                rec[field] = None
                for suffix in ("_precision", "_basis"):
                    if f"{field}{suffix}" in rec:
                        rec[f"{field}{suffix}"] = None
        detail_found: list[dict] = []
        details = _blank_details(details, low, high, "source_details", detail_found)
        found.extend(detail_found)
        if not found:
            continue
        notes = details.setdefault("parse_notes", [])
        for item in found:
            note = {"field": item["field"], "value": item["value"], "rule": RULE,
                    "action": "blanked", "reason": REASON,
                    "window": [low.isoformat(), high.isoformat()],
                    "capture_date": capture.isoformat()}
            notes.append(note)
            blanked.append({"state": state.upper(),
                            "source_row": details.get("source_row") or rec.get("source_notice_id"),
                            **{k: note[k] for k in ("field", "value", "window", "capture_date")}})
        rec["source_details"] = json.dumps(details, sort_keys=True, ensure_ascii=False)
        if "raw_record_hash" in rec:
            rec["raw_record_hash"] = _record_hash(rec)
    return blanked
