"""West Virginia normalization for the WorkForce WV listing adapter.

Summary-PDF entries carry the agency's labeled ``Date of Notice`` and
``Projected Date``. Listing documents carry only the agency's link label;
their label date has no stated role and is never used as a date here (it
stays in the raw row as ``listing_title_date``).
"""

from __future__ import annotations

import re

from warn_transformer.schema import BaseTransformer

_DAY = r"\d{1,2}/\d{1,2}/\d{2,4}"
_SINGLE = re.compile(rf"({_DAY})")
_RANGE = re.compile(rf"({_DAY})\s*(?:-|to)\s*({_DAY})")
_JOBS = re.compile(r"(?:Total\s+)?(\d[\d,]*)\b")


def projected_start(text: str) -> str:
    """The start of a projected date cell, only when it is one date or one range.

    ``and`` lists and several ranges are phases, not one interval, so they
    are left for review in the raw cell rather than reduced to a date.
    """
    text = " ".join((text or "").split())
    if _SINGLE.fullmatch(text):
        return text
    match = _RANGE.fullmatch(text)
    return match.group(1) if match else ""


class Transformer(BaseTransformer):
    postal_code = "WV"
    fields = dict(
        company="company",
        location=lambda row: row.get("county") or "",
        notice_date=lambda row: (
            row.get("notice_date", "") if row.get("record_kind") == "summary_entry" else ""
        ),
        effective_date=lambda row: (
            projected_start(row.get("projected_date", ""))
            if row.get("record_kind") == "summary_entry" else ""
        ),
        jobs=lambda row: row.get("affected") or "",
    )
    date_format = ["%m/%d/%y", "%m/%d/%Y"]

    def transform_jobs(self, value: str) -> int | None:
        match = _JOBS.match((value or "").strip())
        return int(match.group(1).replace(",", "")) if match else None

    def check_if_amendment(self, row: dict) -> bool:
        note = row.get("entry_note") or ""
        return (row.get("document_role") == "amendment"
                or bool(re.search(r"\b(?:update|postponement|revis)", note, re.I)))

    def check_if_closure(self, row: dict) -> bool | None:
        value = (row.get("action") or "").lower()
        if "clos" in value:
            return True
        if "layoff" in value:
            return False
        return None
