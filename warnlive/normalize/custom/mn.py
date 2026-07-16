"""Minnesota transformer for our custom mn.py adapter's CSV.

MN's reports include all Rapid Response-tracked layoffs, not just WARN Act
filings; the WARN Act column is preserved in raw_extra. notice_date uses the
WARN Received date when present, else the layoff start date.
"""

from __future__ import annotations

from warn_transformer.schema import BaseTransformer


def _first_date_token(value: str) -> str:
    return (value or "").strip().split()[0] if (value or "").strip() else ""


class Transformer(BaseTransformer):
    """Transform Minnesota raw data for consolidation."""

    postal_code = "MN"
    fields = dict(
        company="Layoff Name",
        location="City",
        notice_date=lambda row: _first_date_token(row.get("WARN Received", ""))
        or _first_date_token(row.get("Layoff Start", "")),
        effective_date=lambda row: _first_date_token(row.get("Layoff Start", "")),
        jobs="Affected Workers",
    )
    date_format = ["%m/%d/%Y", "%m/%d/%y"]

    def transform_date(self, value: str) -> str | None:
        try:
            return super().transform_date(value)
        except (KeyError, AssertionError):
            return None

    def transform_jobs(self, value: str) -> int | None:
        try:
            return super().transform_jobs(value)
        except KeyError:
            return None

    def check_if_closure(self, row: dict) -> bool | None:
        value = (row.get("Layoff Type") or "").lower()
        if "clos" in value:
            return True
        if value:
            return False
        return None

    def check_if_temporary(self, row: dict) -> bool | None:
        status = (row.get("Layoff Status") or "").lower()
        if "temporary" in status:
            return True
        return None
