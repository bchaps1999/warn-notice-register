"""Massachusetts transformer for our custom ma.py adapter's CSV."""

from __future__ import annotations

from warn_transformer.schema import BaseTransformer


class Transformer(BaseTransformer):
    """Transform Massachusetts raw data for consolidation."""

    postal_code = "MA"
    fields = dict(
        company="EMPLOYER",
        location=lambda row: ", ".join(
            part
            for part in (row.get("CITY/TOWN", "").strip(), row.get("REGION", "").strip())
            if part
        ),
        notice_date="RECEIVED",
        effective_date="DATE(S) OF LAYOFFS",
        jobs="# EMPLOYEES IMPACTED",
    )
    date_format = ["%m/%d/%Y", "%m/%d/%y"]

    def transform_date(self, value: str) -> str | None:
        # Layoff-date cells are free text at times ("8/15/26 & 11/30/26",
        # "Rolling"); take the first parseable token, else null.
        value = (value or "").strip()
        for token in value.replace("&", " ").split():
            try:
                result = super().transform_date(token)
            except (KeyError, AssertionError):
                continue
            if result:
                return result
        return None

    def transform_jobs(self, value: str) -> int | None:
        try:
            return super().transform_jobs(value)
        except KeyError:
            return None
