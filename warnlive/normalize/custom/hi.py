"""Hawaii legacy five-column rows and the WDD detail-page extension.

The legacy WDC list prints amendments as a starred line in the Date column
with the Company column empty: "*Hawaiian Airlines Amended September 16,
2020", "* Correction to FOH Hospitality Inc.". ``legacy_marker`` reads the
employer and the amendment wording from that line; the date in it is the
upstream-corrected notice date. A line that names no employer ("*Errata to
Amended WARN") is left unparsed.
"""

from __future__ import annotations

import re

from warn_transformer.transformers.hi import Transformer as LegacyTransformer

_DAY = re.compile(r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}\b", re.I)
_TOTAL = re.compile(r"\bTotal employees statewide:\s*([\d,]+)\b", re.I)
_AFFECTED = re.compile(r"\bEmployees affected by partial closing:\s*([\d,]+)\b", re.I)
_MARKED = re.compile(
    r"^\*+\s*(?:(?P<fix>correction|errata)\s+to\s+(?P<target>.+?)"
    r"|(?P<company>.+?)\s+(?P<marker>second\s+amended|amended|amendment(?:\s*#\s*\d+)?"
    r"|updated?|supplement)\b.*?)\s*$",
    re.I | re.S,
)


def legacy_marker(row: dict) -> dict | None:
    """Employer and amendment wording from a starred legacy Date cell."""
    if row.get("source_kind") or (row.get("Company") or "").strip():
        return None
    text = " ".join((row.get("Date") or "").split())
    match = _MARKED.match(text)
    if not match:
        return None
    if match.group("fix"):
        company, marker = match.group("target"), f"{match.group('fix')} to"
    else:
        company, marker = match.group("company"), match.group("marker")
    company = company.strip(" ,")
    if re.fullmatch(r"(?i)amended\s+warn(?:\s+notice)?", company):
        return None  # names a document, not an employer
    return {"company": company, "marker": " ".join(marker.lower().split()),
            "source_text": text}


def _company(row: dict) -> str:
    marked = legacy_marker(row)
    return marked["company"] if marked else row.get("Company", "")


class Transformer(LegacyTransformer):
    fields = dict(
        company=_company,
        notice_date="Date",
        location="location",
        jobs=lambda row: _wdd_total(row) if row.get("source_kind") == "wdd_detail" else row.get("jobs", ""),
        effective_date=lambda row: _wdd_effective(row) if row.get("source_kind") == "wdd_detail" else "",
    )

    def check_if_amendment(self, row: dict) -> bool:
        return legacy_marker(row) is not None or super().check_if_amendment(row)

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
