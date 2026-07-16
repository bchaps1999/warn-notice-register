"""Nevada transformer for our custom nv.py adapter's CSV.

Nevada's master list mixes WARN and Non-WARN actions; we keep both (the
Notification column is preserved in raw_extra) since all are real layoff
events tracked by DETR.
"""

from __future__ import annotations

from warn_transformer.schema import BaseTransformer


class Transformer(BaseTransformer):
    """Transform Nevada raw data for consolidation."""

    postal_code = "NV"
    fields = dict(
        company="Employer",
        location=lambda row: ", ".join(
            part for part in (row.get("City", ""), row.get("County", "")) if part
        ),
        notice_date="Received Date",
        effective_date="Effective Date",
        jobs="Affected Total",
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
        value = (row.get("Type") or "").lower()
        if "closure" in value:
            return True
        if "layoff" in value:
            return False
        return None
