"""Nevada transformer for our custom nv.py adapter's CSV.

Nevada's master list mixes WARN and Non-WARN actions; we keep both (the
Notification column is preserved in raw_extra) since all are real layoff
events tracked by DETR.

Some 2020 PDF pages print a second date column after Received Date. Earlier
captures zipped those rows onto the usual header, so every later cell sits
one column right: Employer holds the worker count, City the employer, County
the city and Notification the county. ``realign`` reads such a row by
position; the raw row stays in raw_extra unchanged.
"""

from __future__ import annotations

import re

from warn_transformer.schema import BaseTransformer

_DATE = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}")


def shifted(row: dict) -> bool:
    """A row captured from the three-date layout onto the two-date header."""
    return bool(
        _DATE.fullmatch((row.get("Type") or "").strip())
        and re.fullmatch(r"(?i)closure|layoff|layoff/closure",
                         (row.get("Affected Total") or "").strip())
        and re.fullmatch(r"[\d,]+", (row.get("Employer") or "").strip())
        and (row.get("City") or "").strip()
    )


def realign(row: dict) -> dict:
    """The row under its printed column labels (identity for normal rows).

    The shifted layout has no Notification column. The second date's label
    is not in the capture, so it is kept only in raw_extra.
    """
    if not shifted(row):
        return row
    return {
        "Received Date": row.get("Received Date", ""),
        "Second Date": row.get("Effective Date", ""),
        "Effective Date": row.get("Type", ""),
        "Type": row.get("Affected Total", ""),
        "Affected Total": row.get("Employer", ""),
        "Employer": row.get("City", ""),
        "City": row.get("County", ""),
        "County": row.get("Notification", ""),
    }


class Transformer(BaseTransformer):
    """Transform Nevada raw data for consolidation."""

    postal_code = "NV"
    fields = dict(
        company=lambda row: realign(row).get("Employer", ""),
        location=lambda row: ", ".join(
            part for part in (realign(row).get("City", ""), realign(row).get("County", ""))
            if part
        ),
        notice_date="Received Date",
        effective_date=lambda row: realign(row).get("Effective Date", ""),
        jobs=lambda row: realign(row).get("Affected Total", ""),
    )
    date_format = ["%m/%d/%Y", "%m/%d/%y"]

    def transform_date(self, value: str) -> str | None:
        # NV uses Unknown/TBD/NR freely; treat unparseable as null rather
        # than maintaining a corrections table for junk values.
        try:
            return super().transform_date(value)
        except KeyError:
            return None

    def transform_jobs(self, value: str) -> int | None:
        try:
            return super().transform_jobs(value)
        except KeyError:
            return None

    def check_if_closure(self, row: dict) -> bool | None:
        value = (realign(row).get("Type") or "").lower()
        if "closure" in value:
            return True
        if "layoff" in value:
            return False
        return None
