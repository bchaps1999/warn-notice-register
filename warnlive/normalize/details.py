"""Conservative structured evidence from source-specific WARN fields.

The original row remains in raw_extra. These projections make named dates
and sites queryable without guessing worker allocation or turning a list of
separation dates into one continuous interval.
"""

from __future__ import annotations

import json
import re
from datetime import datetime

_SLASH_DATE = r"\d{1,2}/\d{1,2}/\d{2,4}"
_DATE_TOKEN = re.compile(rf"(?<!\d){_SLASH_DATE}(?!\d)")
_SIMPLE_RANGE = re.compile(
    rf"^\s*({_SLASH_DATE})\s*(?:-|–|—|to|through|thru)\s*({_SLASH_DATE})\s*$",
    re.I,
)
_LABELED_RANGE = re.compile(
    rf"^\s*Beginning:\s*({_SLASH_DATE})\s*[-–—]\s*Ending:\s*({_SLASH_DATE})\s*$",
    re.I,
)
_EFFECTIVE_FIELD = {
    "FL": "Layoff Date", "OH": "Layoff Date(s)",
    "NJ": "Effective Date", "MA": "DATE(S) OF LAYOFFS",
    "PA": "date_effective", "KY": "date_effective",
    "IN": "LO/CL Date", "TN": "Effective Date",
    "MD": "Effective Date",
}


def _date(value: str | None) -> str | None:
    value = (value or "").strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def extract(state: str, raw: dict, rec: dict) -> dict:
    """Return optional canonical detail fields; omit unsupported guesses."""
    details: dict = {}
    result: dict = {}

    if state == "SC":
        artifact = (raw.get("source") or "").strip()
        ordinal = (raw.get("source_row") or "").strip()
        if artifact and ordinal:
            result["source_identity"] = f"SC:{artifact}:{ordinal}"
        dates = []
        for role, key in (
            ("notice", "notice_date"),
            ("effective_start", "effective_date"),
            ("effective_end", "effective_end_date"),
            ("legacy_projected", "legacy_date"),
        ):
            value = (raw.get(key) or "").strip()
            if value:
                dates.append({"role": role, "source_field": key,
                              "source_text": value, "date": _date(value)})
        if dates:
            details["dates"] = dates
        address = (raw.get("address") or "").strip()
        if address:
            details["sites"] = [{
                "source_field": "address", "address": address,
                "county": (raw.get("county") or "").strip() or None,
                "workers": rec.get("employees_affected"),
            }]
        if artifact:
            details["source_artifact"] = artifact
        if (raw.get("source_page") or "").strip():
            details["source_page"] = raw["source_page"].strip()
        end = _date(raw.get("effective_end_date"))
        if end:
            result["effective_date_end"] = end

    elif state == "GA":
        filing = (raw.get("GA WARN ID") or "").strip()
        if filing:
            result["source_identity"] = f"GA:{filing}"
        dates = []
        for ordinal in ("First", "Second", "Third", "Fourth", "Fifth", "Sixth"):
            key = f"{ordinal} Date of Separation"
            value = (raw.get(key) or "").strip()
            if value:
                dates.append({"role": "separation", "source_field": key,
                              "source_text": value, "date": _date(value)})
        if dates:
            details["dates"] = dates
        sites = []
        for ordinal in ("First", "Second", "Third", "Fourth", "Fifth", "Sixth"):
            key = f"{ordinal} Location Address"
            address = (raw.get(key) or "").strip()
            if address:
                sites.append({"source_field": key, "address": address,
                              "workers": None})
        if sites:
            details["sites"] = sites
        if rec.get("employees_affected") is not None:
            details["total_workers"] = rec["employees_affected"]
            details["worker_allocation"] = "single_site" if len(sites) == 1 else "unresolved"

    elif state == "KS":
        record_number = (raw.get("record_number") or "").strip()
        detail_url = (raw.get("detail_page_url") or "").strip()
        if record_number.isdecimal() and (
            not detail_url or detail_url.rstrip("/").endswith(f"/{record_number}")
        ):
            result["source_identity"] = f"KS:{record_number}"
            details["source_record_number"] = record_number
            if detail_url:
                details["source_detail_url"] = detail_url

    elif state == "NJ" and rec.get("notice_date"):
        result["notice_date_precision"] = "month"
        result["notice_date_basis"] = "inferred_year_from_effective_date"
        posted = (raw.get("Month Posted") or "").strip()
        if posted:
            details["dates"] = [{
                "role": "notice_month", "source_field": "Month Posted",
                "source_text": posted, "date": rec["notice_date"],
                "precision": "month", "basis": "inferred_year_from_effective_date",
            }]

    elif state == "CO":
        end = _date(raw.get("end_date"))
        if end:
            result["effective_date_end"] = end
            details["dates"] = [{
                "role": "effective_end", "source_field": "end_date",
                "source_text": raw["end_date"], "date": end,
            }]

    field = _EFFECTIVE_FIELD.get(state)
    if field:
        source_text = (raw.get(field) or "").strip()
        tokens = _DATE_TOKEN.findall(source_text)
        if len(tokens) >= 2:
            span = _SIMPLE_RANGE.fullmatch(source_text) or _LABELED_RANGE.fullmatch(source_text)
            dates = details.setdefault("dates", [])
            dates.extend({
                "role": "effective_component", "source_field": field,
                "source_text": token, "date": _date(token),
            } for token in tokens)
            details["effective_date_interpretation"] = "interval" if span else "list_or_phases"
            if span:
                start, end = _date(span.group(1)), _date(span.group(2))
                if start and end and start <= end:
                    result["effective_date_end"] = end

    if details:
        result["source_details"] = json.dumps(details, sort_keys=True, ensure_ascii=False)
    return result
