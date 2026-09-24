"""Hawaii legacy five-column rows and the WDD detail-page extension."""

from __future__ import annotations

import re

from warn_transformer.transformers.hi import Transformer as LegacyTransformer

_DAY = re.compile(r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}\b", re.I)
_TOTAL = re.compile(r"\bTotal employees statewide:\s*([\d,]+)\b", re.I)
_AFFECTED = re.compile(r"\bEmployees affected by partial closing:\s*([\d,]+)\b", re.I)


class Transformer(LegacyTransformer):
    fields = dict(
        company="Company",
        notice_date="Date",
        location="location",
        jobs=lambda row: _wdd_total(row) if row.get("source_kind") == "wdd_detail" else row.get("jobs", ""),
        effective_date=lambda row: _wdd_effective(row) if row.get("source_kind") == "wdd_detail" else "",
    )

    def transform_date(self, value: str) -> str | None:
        if not value:
            return None
        if _DAY.fullmatch(value):
            from datetime import datetime
            return datetime.strptime(value, "%B %d, %Y").date().isoformat()
        return super().transform_date(value)


def _wdd_effective(row: dict) -> str:
    text = row.get("Date of Closure or When Employees Will Be Affected", "")
    match = re.search(r"\b(?:Layoff|Closure) Effective:\s*", text, re.I)
    if not match:
        return ""
    remainder = text[match.end():].strip()
    if re.match(r"(?:on or after|no earlier than|between|beginning|starting|approximately)\b",
                remainder, re.I):
        return ""
    day = _DAY.match(remainder)
    if not day:
        return ""
    tail = remainder[day.end():].lstrip()
    if re.match(r"(?:through|thru|to|and|until|[-–—]|,?\s*ending\b)", tail, re.I):
        return ""
    return day.group()


def _wdd_total(row: dict) -> str:
    text = row.get("Number of Affected Employees (Total)", "")
    match = _AFFECTED.search(text)
    if match:
        return match.group(1)
    # Statewide payroll is not a layoff count on a transfer or divestiture.
    # Amentum explicitly describes a layoff of its complete stated workforce.
    if not re.search(r"\bLayoff Effective:", row.get("Date of Closure or When Employees Will Be Affected", ""), re.I):
        return ""
    match = _TOTAL.search(text)
    return match.group(1) if match else ""
