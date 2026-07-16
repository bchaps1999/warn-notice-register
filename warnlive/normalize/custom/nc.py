"""North Carolina transformer for our custom nc.py adapter's CSV."""

from __future__ import annotations

from warn_transformer.schema import BaseTransformer


class Transformer(BaseTransformer):
    """Transform North Carolina raw data for consolidation."""

    postal_code = "NC"
    fields = dict(
        company="WARN Notice: WARN Notice Name",
        location=lambda row: f"{row.get('Address 1', '')} {row.get('City', '')}".strip(),
        notice_date="Date of Notice",
        effective_date="Effective Date",
        jobs="Number affected at this location",
    )
    date_format = ["%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"]
    date_corrections = {}
    # NC effective dates legitimately run 1-2 years out (e.g. phased closures)
    max_future_days = 1100

    def transform_date(self, value: str) -> str | None:
        try:
            return super().transform_date(value)
        except KeyError:
            return None

    def check_if_closure(self, row: dict) -> bool | None:
        value = (row.get("WARN notice type") or "").lower()
        if "closure" in value:
            return True
        if "layoff" in value:
            return False
        return None

    def check_if_temporary(self, row: dict) -> bool | None:
        value = (row.get("Type of layoff or closure") or "").lower()
        if "temporary" in value:
            return True
        if "permanent" in value:
            return False
        return None
