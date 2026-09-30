"""Nebraska transformer: key and publish the filed site.

NDOL's reports carry two place columns. The 2010-2019 year reports
(WARNReportData, LayoffAndClosureReportData) fill ``City`` with the city and
``Location`` with a site description ("Oakview Mall", "Distribution Center
- Sidney"). The current WARN page, and its archived captures used for
2020-2022, publish only ``Location``, so the collector leaves ``City``
blank. Upstream's transformer maps location to ``City`` alone, which left
every 2020+ row without a location and let two sites of one employer filed
the same day (Hayneedle, 2020-01-23) share a dedupe key.

The location is ``City`` when the row has one, else ``Location`` as filed.
Rows with a City keep exactly the location (and key) they always had; rows
without one change key, which ``warnlive.migrate.ne_transition`` maps for
published notices.

Rows tagged ``source_report = layoff_closure_report`` come from NDOL's
general layoff/closure report, not its WARN report; ``hold_reason`` names
the hold the engine records for them (see ``nonnotice``). ``source_details``
names the report each row came from. Pure functions; no I/O.
"""

from __future__ import annotations

from warn_transformer.transformers.ne import Transformer as UpstreamTransformer

WARN_REPORT = "warn_report"
LAYOFF_CLOSURE_REPORT = "layoff_closure_report"
SOURCE_REPORTS = (WARN_REPORT, LAYOFF_CLOSURE_REPORT)
LAYOFF_CLOSURE_HOLD = "ne_layoff_closure_report_not_warn"


def _cell(row: dict, key: str) -> str:
    value = row.get(key)
    return value.strip() if isinstance(value, str) else ""


def ne_location(row: dict) -> str:
    """The filed city, else the filed Location cell."""
    return _cell(row, "City") or row.get("Location") or ""


def hold_reason(row: dict) -> str | None:
    """The hold for a raw NE row from the layoff/closure report, else None.

    Only an explicit tag holds a row: an untagged row (an upstream-format
    CSV) is not classified here.
    """
    if _cell(row, "source_report") == LAYOFF_CLOSURE_REPORT:
        return LAYOFF_CLOSURE_HOLD
    return None


def source_details(row: dict) -> dict:
    """Report provenance for ``details.extract``; empty for untagged rows."""
    report = _cell(row, "source_report")
    if not report:
        return {}
    if report not in SOURCE_REPORTS:
        raise ValueError(f"NE: unknown source_report {report!r}")
    details = {"source_report": report}
    for key, name in (("ndol_source_page", "source_page"),
                      ("ndol_page_row", "source_page_row"),
                      ("ndol_notice_link", "notice_document"),
                      ("ndol_matched_warn_row", "matched_warn_report_row")):
        if _cell(row, key):
            details[name] = _cell(row, key)
    if not _cell(row, "City") and _cell(row, "Location"):
        details["location_source_field"] = "Location"
    return details


class Transformer(UpstreamTransformer):
    """Transform Nebraska raw data for consolidation."""

    fields = dict(UpstreamTransformer.fields, location=ne_location)

    def check_if_closure(self, row: dict) -> bool | None:
        # Year-report WARN rows and page rows have no Type column.
        return "closure" in (row.get("Type") or "").lower() or None
