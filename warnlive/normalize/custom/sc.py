"""South Carolina normalization for the field-preserving PDF adapter."""

from __future__ import annotations

import re

from warn_transformer.schema import BaseTransformer

_DATE_TOKEN = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}")


class Transformer(BaseTransformer):
    postal_code = "SC"
    fields = dict(
        company="company",
        # Keep the former county-level location for identity compatibility;
        # the worksite address remains in raw_extra/site_address, not the key.
        location=lambda row: row.get("county") or row.get("location") or "",
        # A legacy row's only date is an effective/projected date.  It is not
        # evidence of when SC received the notice.
        notice_date=lambda row: row.get("notice_date", ""),
        effective_date=lambda row: (
            row.get("effective_date") or row.get("legacy_date") or row.get("date") or ""
        ),
        jobs=lambda row: row.get("impacted") or row.get("jobs") or "",
    )
    date_format = ["%m/%d/%Y", "%m/%d/%y"]

    def transform_date(self, value: str) -> str | None:
        value = (value or "").replace("//", "/")
        match = _DATE_TOKEN.search(value)
        value = match.group() if match else value
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
        value = (row.get("action_type") or "").lower()
        if "closure" in value:
            return True
        if "layoff" in value:
            return False
        return None
