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
    rf"^\s*(?:(?:Beginning|Commencing):?\s*)?({_SLASH_DATE})"
    rf"(?:\s*\(\d+ employees\))?\s*[-–—;,]\s*"
    rf"(?:Ending|Completed):?\s*({_SLASH_DATE})"
    rf"(?:\s*\(\d+ employees\))?\s*$",
    re.I,
)
_EFFECTIVE_FIELD = {
    "FL": "Layoff Date", "OH": "Layoff Date(s)",
    "NJ": "Effective Date", "MA": "DATE(S) OF LAYOFFS",
    "PA": "date_effective", "KY": "date_effective",
    "IN": "LO/CL Date", "TN": "Effective Date", "RI": "Effective Date",
    "MD": "Effective Date",
}
_MONTH_NAMES = {
    name: month for month, name in enumerate(
        "january february march april may june july august september october november december".split(),
        1,
    )
}
_NJ_PAIR = re.compile(
    rf"^\s*({_SLASH_DATE})\s*(?P<join>[-–—]|and)\s*({_SLASH_DATE})\s*$",
    re.I,
)
_NJ_PAIR_CANDIDATE = re.compile(r"[-–—]|\band\b", re.I)

# These labels identify the date's role in the agency row. A full ISO value
# alone is not evidence of either role or precision. Keep this allowlist
# narrow: receipt, posting, and notification activity fields are excluded.
_REPORTED_DAY_FIELDS = {
    "AL": {"notice_date": ("date_notice",), "effective_date": ("date_action",)},
    "CA": {"notice_date": ("notice_date",), "effective_date": ("effective_date",)},
    "CO": {"notice_date": ("notice_date",), "effective_date": ("begin_date",),
           "effective_date_end": ("end_date",)},
    "ID": {"notice_date": ("Date of Letter",),
           "effective_date": ("Effective or Commencing Date",)},
    "IN": {"notice_date": ("Notice Date",), "effective_date": ("LO/CL Date",)},
    "MA": {"effective_date": ("DATE(S) OF LAYOFFS",)},
    "MD": {"notice_date": ("Notice Date",), "effective_date": ("Effective Date",)},
    "MN": {"effective_date": ("Layoff Start",)},
    "MS": {"notice_date": ("date_notice",), "effective_date": ("date_effective",)},
    "NC": {"notice_date": ("Date of Notice",), "effective_date": ("Effective Date",)},
    "NY": {"notice_date": ("Date of Notice", "Date of WARN Notice "),
           "effective_date": ("Layoff Date", "Closing Date", "Date Layoff/Closure Starts")},
    "OH": {"effective_date": ("Layoff Date", "Layoff Date(s)")},
    "UT": {"notice_date": ("Date of Notice",)},
    "VA": {"notice_date": ("Notice Date",), "effective_date": ("Impact Date",)},
    "WA": {"effective_date": ("Layoff Start Date",)},
}


def _add_reported_day_evidence(state: str, raw: dict, rec: dict,
                               result: dict, details: dict) -> None:
    """Mark a scalar day only when an allowed whole source cell matches it."""
    if state == "CA" and not (
        isinstance(raw.get("source_file"), str)
        and raw.get("company") and raw.get("num_employees")
        and "received_date" in raw and "notice_date" in raw
        and "effective_date" in raw
    ):
        return
    for role, fields in _REPORTED_DAY_FIELDS.get(state, {}).items():
        selected = result.get(role, rec.get(role))
        if not selected or result.get(f"{role}_precision") or rec.get(f"{role}_precision"):
            continue
        if any(isinstance(details.get(flag), str) and
               any(word in details[flag] for word in ("review", "conflict", "invalid"))
               for flag in ("notice_date_status", "effective_date_status",
                            "effective_date_end_status", "date_role_status")):
            continue
        matches = [field for field in fields
                   if isinstance(raw.get(field), str) and _date(raw[field]) == selected]
        if len(matches) != 1:
            continue
        field = matches[0]  # The raw cell remains in the version's raw_extra.
        result[f"{role}_precision"] = "day"
        result[f"{role}_basis"] = "reported"
        details.setdefault("date_precision_evidence", {})[role] = {
            "rule": "labeled_source_day_match_v1",
            "source_field": field,
            "matched_day": selected,
        }
        details.setdefault("date_evidence_rule", "labeled_source_day_match_v1")


def _date(value: str | None) -> str | None:
    value = (value or "").strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def il_report_day(value: str | None) -> str | None:
    """Parse an IEBS report day without treating it as a legal notice day."""
    value = (value or "").strip()
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(?: 00:00:00)?", value)
    return _date(match.group(1)) if match else None


def nj_effective_pair(value: str) -> tuple[str, str | None, str | None] | None:
    """Classify an NJ two-date cell without assigning an unsupported end date."""
    if "/" not in value or not _NJ_PAIR_CANDIDATE.search(value):
        return None
    match = _NJ_PAIR.fullmatch(value)
    if not match:
        return ("review", None, None)
    start, end = _date(match.group(1)), _date(match.group(3))
    if not start or not end or start > end:
        return ("review", start, end)
    return ("list_or_phases" if match.group("join").lower() == "and" else "interval", start, end)


# Source columns that state a notice's type in a form the generic raw-column
# classifier (warnlive.normalize.engine._classify_from_raw) cannot read.
# Each maps an exact source value; anything else stays as the engine left it.
_TYPE_COLUMNS: dict[str, tuple[str, dict[str, str]]] = {
    # Indiana's listing heads its date column "LO/CL Date": LO is a layoff,
    # CL a closure (CL is already read by the engine).
    "IN": ("Notice Type", {"lo": "mass_layoff", "l/o": "mass_layoff"}),
    # Rhode Island's "Closing Yes/No": a WARN notice that is not a closing is
    # a layoff.
    "RI": ("Closing Yes/No", {"yes": "closure", "no": "mass_layoff"}),
    # Michigan's "action" column.
    "MI": ("action", {"layoff": "mass_layoff", "mass layoff": "mass_layoff",
                      "permanent layoff": "mass_layoff",
                      "temporary layoff": "mass_layoff"}),
}
# Michigan's "action" also says whether the layoff is temporary.
_TEMPORARY_VALUES: dict[str, tuple[str, dict[str, int]]] = {
    "MI": ("action", {"temporary layoff": 1, "permanent layoff": 0}),
}
# Coded type columns, read from each agency's own published legend (retrieved
# 2026-09-30; see docs/fl-address-and-type-codes-2026-09-30.md).  The two
# numeric legends are reversed: DC 1 = layoff, 2 = closure; MD 1 = plant
# closure, 2 = mass layoff.
_CODE_TYPE_FIELDS = {"DC": "Code Type", "MD": "Type",
                     "WI": "Original Notice Type / Update Type"}
_CODE_LEGEND_URLS = {
    "DC": "https://does.dc.gov/page/industry-closings-and-layoffs-warn-notifications",
    "MD": "https://labor.maryland.gov/employment/warn2022.shtml",
    "WI": "https://dwd.wisconsin.gov/dislocatedworker/warn/",
}
_DC_CODES = {"1": "mass_layoff", "2": "closure"}
_MD_CODES = {"1": "closure", "2": "mass_layoff"}
# Maryland's free-text type from 2023: only these exact phrases are read.
_MD_PHRASES = {"mass layoff": "mass_layoff", "mass layoff - no recall": "mass_layoff",
               "plant closure": "closure"}
_MD_LEADING_CODE = re.compile(r"^([12])(?!\d)")
_WI_ORIGINAL = {"CL": "closure", "WR": "mass_layoff"}
_WI_UPDATES = ("AW", "LS", "OC", "RN")
_CO_LOSS_FIELDS = (("permanent", "permanent_job_losses"),
                   ("temporary", "temporary_job_losses"),
                   ("furloughs", "furloughs"))


def _count(value) -> int | None:
    text = str(value or "").strip().replace(",", "")
    return int(text) if text.isdigit() else None


def _add_type_code_evidence(state: str, raw: dict, rec: dict, result: dict,
                            details: dict) -> None:
    """layoff_type (and MD is_temporary) from DC, MD and WI type codes.

    Only the agency's published legend is applied. A coded cell overrides the
    engine's keyword reading of that same cell; a phrase-only MD cell fills
    the field only where the engine left it unknown.  The raw cell and the legend URL are recorded in
    source_details.  A Wisconsin cell listing both CL and WR stays unknown;
    its update tokens (AW, LS, OC, RN) are recorded but change nothing, and a
    rescission (RN) does not change admission.
    """
    field = _CODE_TYPE_FIELDS[state]
    text = str(raw.get(field) or "").strip()
    if not text:
        return
    norm = " ".join(text.casefold().split())
    mapped = code = None
    temporary = None
    if state == "DC":
        code = norm if norm in _DC_CODES else None
        mapped = _DC_CODES.get(norm)
    elif state == "MD":
        lead = _MD_LEADING_CODE.match(norm)
        if lead:
            code = lead.group(1)
            mapped = _MD_CODES[code]
            if re.search(r"\btemporary\b", norm):
                temporary = 1
        else:
            mapped = _MD_PHRASES.get(norm)
            code = text if mapped else None
    else:
        tokens = [t.strip().upper() for t in text.split(",") if t.strip()]
        updates = [t for t in tokens if t in _WI_UPDATES]
        if updates:
            details["update_types"] = updates
            details["update_types_legend_url"] = _CODE_LEGEND_URLS["WI"]
        originals = {t for t in tokens if t in _WI_ORIGINAL}
        if len(originals) == 1:
            code = next(iter(originals))
            mapped = _WI_ORIGINAL[code]
    evidence = {"rule": "source_type_code_v1", "source_field": field,
                "source_text": text, "type_code": code,
                "type_code_legend_url": _CODE_LEGEND_URLS[state]}
    # A published legend code is the agency's own classification, so it
    # outranks the engine's keyword reading of the same cell ("2 (Possibly
    # turning into a closure)" is code 2, a mass layoff). A phrase-only cell
    # just fills an unknown value.
    coded = state != "MD" or _MD_LEADING_CODE.match(norm) is not None
    previous = rec.get("layoff_type")
    if mapped and (previous == "unknown" or (coded and previous != mapped)):
        result["layoff_type"] = mapped
        if previous not in (None, "unknown", mapped):
            evidence = {**evidence, "replaced_keyword_reading": previous}
        details["layoff_type_evidence"] = evidence
    if temporary is not None and rec.get("is_temporary") is None:
        result["is_temporary"] = temporary
        details["temporary_evidence"] = evidence


def _add_source_type_evidence(state: str, raw: dict, rec: dict, result: dict,
                              details: dict) -> None:
    """layoff_type / is_temporary from a labeled source column.

    Only exact, documented source values are read, and only where the engine
    left the field unknown.  The column and its text go into source_details
    so the reading can be checked against the row.
    """
    if not isinstance(raw, dict):
        return
    if state in _CODE_TYPE_FIELDS:
        _add_type_code_evidence(state, raw, rec, result, details)
    if state in _TYPE_COLUMNS and rec.get("layoff_type") == "unknown":
        field, mapping = _TYPE_COLUMNS[state]
        text = str(raw.get(field) or "").strip()
        mapped = mapping.get(" ".join(text.casefold().split()))
        if mapped:
            result["layoff_type"] = mapped
            details["layoff_type_evidence"] = {
                "rule": "source_type_column_v1", "source_field": field,
                "source_text": text}
    if state in _TEMPORARY_VALUES and rec.get("is_temporary") is None:
        field, mapping = _TEMPORARY_VALUES[state]
        text = str(raw.get(field) or "").strip()
        mapped = mapping.get(" ".join(text.casefold().split()))
        if mapped is not None:
            result["is_temporary"] = mapped
            details["temporary_evidence"] = {
                "rule": "source_type_column_v1", "source_field": field,
                "source_text": text}
    if state == "CO":
        # Colorado reports permanent job losses, temporary job losses and
        # furloughs in separate columns.  The notice is temporary only when
        # it reports temporary losses or furloughs and no permanent loss; any
        # permanent loss with none temporary makes it permanent; a mix, a
        # non-numeric cell, or nothing reported leaves it unknown.
        texts = {name: str(raw.get(field) or "").strip() for name, field in _CO_LOSS_FIELDS}
        if not any(texts.values()):
            return
        counts = {name: _count(text) for name, text in texts.items()}
        details["job_losses"] = {
            "rule": "co_job_loss_columns_v1",
            **{name: counts[name] for name in counts if texts[name]},
            **({"source_text": {name: text for name, text in texts.items() if text}}
               if any(texts[name] and counts[name] is None for name in texts) else {}),
        }
        if any(texts[name] and counts[name] is None for name in texts):
            return
        permanent = counts["permanent"] or 0
        temporary = (counts["temporary"] or 0) + (counts["furloughs"] or 0)
        if rec.get("is_temporary") is None:
            if permanent == 0 and temporary > 0:
                result["is_temporary"] = 1
            elif permanent > 0 and temporary == 0:
                result["is_temporary"] = 0


def extract(state: str, raw: dict, rec: dict) -> dict:
    """Return optional canonical detail fields; omit unsupported guesses."""
    details: dict = {}
    result: dict = {}


    if state == "HI" and raw.get("source_kind") in {"wdd_detail", "wdc_archive"}:
        kind = raw["source_kind"]
        result["source_identity"] = raw.get("source_identity") or None
        details["source_artifact"] = raw.get("PDF url") or None
        details["source_page"] = raw.get("source_page") or None
        details["source_text"] = raw.get("source_text") or None
        details["source_kind"] = kind
        if kind == "wdd_detail":
            # A WDD receipt day is an agency fact, never the legal notice day.
            receipt_text = (raw.get("Date Department Received WARN") or "").strip()
            try:
                receipt = datetime.strptime(receipt_text, "%B %d, %Y").date().isoformat()
            except ValueError:
                receipt = None
            event_text = (raw.get("Date of Closure or When Employees Will Be Affected") or "").strip()
            count_text = (raw.get("Number of Affected Employees (Total)") or "").strip()
            details["agency_received_date"] = receipt
            details["date_evidence_rule"] = "hi_wdd_labeled_fields_v1"
            details["dates"] = [{
                "role": "agency_received", "source_field": "Date Department Received WARN",
                "source_text": receipt_text, "date": receipt,
                "precision": "day" if receipt else "unknown", "basis": "reported",
            }, {
                "role": "effective_start", "source_field": "Date of Closure or When Employees Will Be Affected",
                "source_text": event_text, "date": rec.get("effective_date"),
                "precision": "day" if rec.get("effective_date") else "unknown", "basis": "reported",
            }]
            details["status_text"] = (raw.get("status_text") or "").strip()
            details["count_text"] = (raw.get("count_text") or count_text).strip()
            details["projected_affected_workers"] = rec.get("employees_affected")
            result["notice_date"] = None
            if rec.get("effective_date"):
                result["effective_date_precision"] = "day"
                result["effective_date_basis"] = "reported"
        else:
            details["status_text"] = (raw.get("status_text") or "").strip()
            details["document_role"] = (raw.get("document_role") or "").strip()
            details["date_evidence_rule"] = "hi_wdc_archive_list_v1"
            archive_day = (raw.get("archive_list_date") or "").strip()
            if archive_day:
                details["dates"] = [{
                    "role": "archive_list_date", "source_field": "archive_list_date",
                    "source_text": archive_day, "date": archive_day,
                }]
            result["notice_date"] = None

    elif state == "HI" and not raw.get("source_kind"):
        from warnlive.normalize.custom.hi import legacy_marker

        marked = legacy_marker(raw)
        if marked:
            # The employer came from the starred Date line; two amendments
            # with the same line differ only in their linked document.
            details["employer_basis"] = "hi_legacy_starred_date_line_v1"
            details["amendment_marker"] = marked["marker"]
            details["marker_source_text"] = marked["source_text"]
            details["source_artifact"] = (raw.get("PDF url") or "").strip() or None
            # Without a date, employer alone would merge distinct amendment
            # letters into one notice; the linked PDF is the filing identity.
            if not result.get("notice_date") and details["source_artifact"]:
                details["notice_key_source_text"] = details["source_artifact"]

    elif state == "WV":
        # Each summary entry or linked notice PDF is its own filing; undated
        # documents for one employer must not merge on the legacy key.
        result["source_identity"] = (raw.get("source_identity") or "").strip() or None

    elif state == "SC":
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
        if artifact == "sc/2026.pdf" and raw.get("source_page") == "1":
            # The pinned 2026 report labels these columns Notice Date and
            # Layoff/Closure Date. Match each projected day to its raw cell;
            # the report's Start/End Date header is a report coverage window.
            for key, canonical, role in (
                ("notice_date", rec.get("notice_date"), "notice_date"),
                ("effective_date", rec.get("effective_date"), "effective_date"),
                ("effective_end_date", end, "effective_date_end"),
            ):
                parsed = _date(raw.get(key))
                if parsed and parsed == canonical:
                    result[f"{role}_precision"] = "day"
                    result[f"{role}_basis"] = "reported"
            if (result.get("notice_date_precision") or
                    result.get("effective_date_precision") or
                    result.get("effective_date_end_precision")):
                details["date_evidence_rule"] = "sc_2026_report_notice_layoff_v1"

    elif state == "WI" and "Notice Received" in raw:
        # The Wisconsin roster gives an agency receipt day, not the date the
        # employer's letter was sent or delivered. Preserve it for identity.
        source_text = (raw.get("Notice Received") or "").strip()
        try:
            received = datetime.strptime(source_text, "%Y%m%d").date().isoformat()
        except ValueError:
            received = _date(source_text)
        details["agency_received_date"] = received
        details["legacy_notice_key_date"] = rec.get("notice_date")
        details["date_evidence_rule"] = "wi_notice_received_role_v1"
        details["dates"] = [{
            "role": "agency_received", "source_field": "Notice Received",
            "source_text": source_text, "date": received,
            "precision": "day" if received else "unknown", "basis": "reported",
        }]
        if received != rec.get("notice_date"):
            details["date_role_status"] = "received_date_mismatch_review"
        result["notice_date"] = None

    elif state == "FL" and "State Notification Date" in raw:
        # This current portal field says when the state was notified. Older
        # Florida archive tables have a distinct, labeled NOTICE DATE and
        # must retain their source-supported canonical notice day.
        source_text = (raw.get("State Notification Date") or "").strip()
        try:
            notification = datetime.strptime(source_text, "%m-%d-%y").date().isoformat()
        except ValueError:
            notification = _date(source_text)
        details["agency_notification_date"] = notification
        details["legacy_notice_key_date"] = rec.get("notice_date")
        details["date_evidence_rule"] = "fl_state_notification_role_v1"
        details["dates"] = [{
            "role": "agency_notification", "source_field": "State Notification Date",
            "source_text": source_text, "date": notification,
            "precision": "day" if notification else "unknown", "basis": "reported",
        }]
        if notification != rec.get("notice_date"):
            details["date_role_status"] = "notification_date_mismatch_review"
        result["notice_date"] = None

    elif state == "KY" and "date_received" in raw:
        # The state field is receipt at a workforce unit, not the employer's
        # dated notice to workers. Preserve the old date only for identity.
        receipt_text = (raw.get("date_received") or "").strip()
        match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(?: 00:00:00)?", receipt_text)
        receipt = _date(match.group(1)) if match else None
        details["agency_received_date"] = receipt
        details["legacy_notice_key_date"] = rec.get("notice_date")
        details["date_evidence_rule"] = "ky_agency_received_role_v1"
        details["dates"] = [{
            "role": "agency_received", "source_field": "date_received",
            "source_text": receipt_text, "date": receipt,
            "precision": "day" if receipt else "unknown", "basis": "reported",
        }]
        if receipt != rec.get("notice_date"):
            details["received_date_status"] = "source_projection_mismatch_review"
        result["notice_date"] = None

    elif state == "MA" and "RECEIVED" in raw:
        # The agency tracker labels this field RECEIVED; a dated filing can
        # differ from it. Keep the old key date but do not call receipt legal
        # notice. One source cell can itself contain two dates.
        receipt_text = (raw.get("RECEIVED") or "").strip()
        tokens = _DATE_TOKEN.findall(receipt_text)
        parsed = [_date(token) for token in tokens]
        receipt = parsed[0] if len(parsed) == 1 else None
        details["agency_received_date"] = receipt
        details["legacy_notice_key_date"] = rec.get("notice_date")
        details["date_evidence_rule"] = "ma_agency_received_role_v1"
        details["dates"] = ([{
            "role": "agency_received" if receipt else "received_field_component",
            "source_field": "RECEIVED", "source_text": token,
            "date": day, "precision": "day" if day else "unknown",
            "basis": "reported",
        } for token, day in zip(tokens, parsed)] or [{
            "role": "agency_received_field", "source_field": "RECEIVED",
            "source_text": receipt_text, "date": None,
            "precision": "unknown", "basis": "reported",
        }])
        if len(tokens) > 1:
            details["received_date_status"] = "multiple_dates_in_received_field_review"
            details["received_source_text"] = receipt_text
        result["notice_date"] = None

    elif state == "NV" and "Received Date" in raw:
        # Nevada reports receipt, notice, and effective dates as distinct
        # fields in older agency PDFs. The frozen projection retained only
        # receipt and effective dates, so receipt cannot be legal notice.
        received = _date(raw.get("Received Date"))
        details["agency_received_date"] = received
        details["legacy_notice_key_date"] = rec.get("notice_date")
        details["date_evidence_rule"] = "nv_agency_received_role_v1"
        details["dates"] = [{
            "role": "agency_received", "source_field": "Received Date",
            "source_text": raw.get("Received Date"), "date": received,
            "precision": "day" if received else "unknown", "basis": "reported",
        }]
        result["notice_date"] = None
        from warnlive.normalize.custom.nv import shifted, transcription_details

        # Rows transcribed from the scanned 2021 list name their evidence.
        details.update(transcription_details(raw))

        if shifted(raw):
            # Earlier releases keyed these rows on the shifted cells; keep
            # that identity while publishing the realigned fields.
            details["column_realignment"] = "nv_three_date_layout_v1"
            # The capture lost this column's label; its cell sits under
            # "Effective Date" in raw_extra. Not treated as any legal date.
            details["unlabeled_second_date_text"] = raw.get("Effective Date") or None
            details["legacy_key_fields"] = {
                "employer_name": raw.get("Employer") or None,
                "location": ", ".join(
                    part for part in (raw.get("City", ""), raw.get("County", "")) if part
                ) or None,
            }

    elif state == "MS" and "company" in raw:
        from warnlive.normalize.custom.ms import filed_company, spill_details, split_row

        details.update(spill_details(raw))
        if split_row(raw):
            details["employer_location_split"] = {
                "rule": "ms_company_city_county_v1",
                "source_field": "company", "source_text": filed_company(raw),
            }
        if split_row(raw) or filed_company(raw) != (raw.get("company") or ""):
            details["legacy_key_fields"] = {
                "employer_name": (raw.get("company") or "").strip() or None,
                "location": (raw.get("county") or "").strip() or None,
            }

    elif state == "NE" and "source_report" in raw:
        # Which NDOL report the row came from (WARN report or the general
        # layoff/closure report), with its page, row and notice document.
        from warnlive.normalize.custom.ne import source_details as ne_source_details

        details.update(ne_source_details(raw))

    elif state == "WA" and "Received Date" in raw:
        # ESD defines Received Date as the day the agency received the WARN
        # notice. It is not the date the employer gave notice to workers.
        # Keep the agency fact and clear the falsely labeled canonical date.
        received = _date(raw.get("Received Date"))
        details["agency_received_date"] = received
        details["date_evidence_rule"] = "wa_agency_received_role_v1"
        details["dates"] = [{
            "role": "agency_received", "source_field": "Received Date",
            "source_text": raw.get("Received Date"), "date": received,
            "precision": "day" if received else "unknown", "basis": "reported",
        }]
        result["notice_date"] = None

    elif state == "MN" and "WARN Received" in raw:
        # This is the Rapid Response team's receipt field. The upstream
        # adapter also falls back to Layoff Start when receipt is absent;
        # neither value establishes the employer's legal notice day.
        receipt_text = (raw.get("WARN Received") or "").strip()
        receipt_token = receipt_text.split()[0] if receipt_text else ""
        receipt = _date(receipt_token)
        details["legacy_notice_key_date"] = rec.get("notice_date")
        details["date_evidence_rule"] = "mn_warn_received_role_v1"
        details["dates"] = [{
            "role": "agency_received", "source_field": "WARN Received",
            "source_text": receipt_text, "date": receipt,
            "precision": "day" if receipt else "unknown", "basis": "reported",
        }]
        if not receipt_text and (raw.get("Layoff Start") or "").strip():
            details["legacy_notice_fallback"] = {
                "source_field": "Layoff Start",
                "source_text": raw["Layoff Start"],
                "reason": "receipt_missing_upstream_used_layoff_start",
            }
        result["notice_date"] = None

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

    elif state == "IL":
        record_id = (raw.get("IEBS Id") or "").strip()
        if record_id:
            # IEBS is the export's row identifier. It is opaque: its digits
            # must not be interpreted as a date or an employer identity.
            result["source_identity"] = f"IL:IEBS:{record_id}"
            details["source_record_id"] = record_id
            details["identity_basis"] = "illinois_iebs_export_record"
        # IEBS calls these reporting/notification activity dates. Its guide
        # distinguishes them from WARN receipt and allows the report date to
        # be backdated. None establishes employee notice delivery.
        details["date_evidence_rule"] = "il_iebs_agency_dates_v1"
        report_text = (raw.get("Initial Date Reported") or "").strip()
        report_day = il_report_day(report_text)
        details["agency_reported_date"] = report_day
        last_text = (raw.get("Last Report Date") or "").strip()
        last_day = il_report_day(last_text)
        dates = [{
            "role": "agency_reported", "source_field": "Initial Date Reported",
            "source_text": report_text, "date": report_day,
            "precision": "day" if report_day else "unknown", "basis": "reported",
        }, {
            "role": "agency_last_reported", "source_field": "Last Report Date",
            "source_text": last_text, "date": last_day,
            "precision": "day" if last_day else "unknown", "basis": "reported",
        }]
        notification_text = (raw.get("Notification Date(s)") or "").strip()
        details["notification_dates_text"] = notification_text
        for ordinal, token in enumerate(notification_text.split(","), start=1):
            token = token.strip()
            if token:
                parsed = _date(token)
                dates.append({
                    "role": "agency_notification", "source_field": "Notification Date(s)",
                    "source_text": token, "ordinal": ordinal, "date": parsed,
                    "precision": "day" if parsed else "unknown", "basis": "reported",
                })
        details["dates"] = dates
        result["notice_date"] = None

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
        # The portal's City/ZIP can identify the employer's out-of-state
        # contact rather than a Kansas layoff site (e.g. Charlotte 28203).
        # Keep that source text, but display only the Kansas workforce area
        # until a site-specific address is established.
        listed_zip = re.sub(r"\D", "", raw.get("zip") or "")[:5]
        if listed_zip and not listed_zip.startswith(("66", "67")):
            result["location"] = (raw.get("lwib_area") or "").strip() or None
            details["listed_contact_city"] = raw.get("city") or None
            details["listed_contact_zip"] = raw.get("zip") or None
            details["location_basis"] = "workforce_area_out_of_state_contact_zip"
        elif not (raw.get("city") or "").strip() and re.match(
            r"^\s*(?:p\.?\s*o\.?\s*(?:box|#)|post office box)(?:\s|$)",
            raw.get("address") or "", re.I,
        ):
            result["location"] = (raw.get("lwib_area") or "").strip() or None
            details["listed_postal_address"] = raw.get("address") or None
            details["location_basis"] = "workforce_area_postal_address_only"

    elif state == "NJ" and "Month Posted" in raw:
        posted = raw.get("Month Posted") or ""
        posted_month = _MONTH_NAMES.get(posted.strip().lower())
        details["dates"] = [{
            "role": "posting_month", "source_field": "Month Posted",
            "source_text": posted,
            "month": posted_month,
            "year": None, "date": None,
            "precision": "month" if posted_month else "unknown", "basis": "reported",
        }]

    elif state == "CO":
        end = _date(raw.get("end_date"))
        if end:
            details["dates"] = [{
                "role": "effective_end", "source_field": "end_date",
                "source_text": raw["end_date"], "date": end,
            }]
            start = rec.get("effective_date")
            if start and end < start:
                details["effective_date_end_status"] = "before_start_review"
            else:
                result["effective_date_end"] = end

    field = _EFFECTIVE_FIELD.get(state)
    if field:
        source_text = (raw.get(field) or "").strip()
        nj_pair = nj_effective_pair(source_text) if state == "NJ" else None
        if nj_pair is not None:
            interpretation, start, end = nj_pair
            details["effective_date_interpretation"] = interpretation
            if interpretation == "review":
                details["effective_date_status"] = "invalid_or_reversed_pair_review"
                details["effective_date_source_text"] = source_text
            else:
                tokens = _DATE_TOKEN.findall(source_text)
                details.setdefault("dates", []).extend({
                    "role": "effective_component", "source_field": field,
                    "source_text": token, "date": parsed,
                } for token, parsed in zip(tokens, (start, end)))
                if interpretation == "interval":
                    result["effective_date_end"] = end
        else:
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
                    if state == "PA":
                        # The upstream manual correction table contains stale
                        # starts for otherwise unambiguous reported intervals.
                        # An unreadable start ("2/30/2025") keeps the existing
                        # value; a reversed range is flagged, not published.
                        if start and (not end or start <= end):
                            result["effective_date"] = start
                        elif start and end:
                            details["effective_date_status"] = "reversed_range_review"
                            details["effective_date_source_text"] = source_text

    if state == "NJ" and details.get("effective_date_interpretation") == "list_or_phases":
        components = [item["date"] for item in details.get("dates", [])
                      if item.get("role") == "effective_component" and item.get("date")]
        selected = result.get("effective_date", rec.get("effective_date"))
        if components and selected != min(components):
            details["effective_date_status"] = "phase_start_selection_review"

    if state == "NJ":
        source_text = (raw.get("Effective Date") or "").strip()
        selected = result.get("effective_date", rec.get("effective_date"))
        single = _date(source_text)
        if single is None:
            try:
                single = datetime.strptime(source_text, "%Y-%m-%d %H:%M:%S").date().isoformat()
            except ValueError:
                pass
        pair = nj_effective_pair(source_text)
        if selected and not details.get("effective_date_status"):
            if single == selected:
                result["effective_date_precision"] = "day"
                result["effective_date_basis"] = "reported"
            elif pair and pair[0] in {"interval", "list_or_phases"} and pair[1] == selected:
                result["effective_date_precision"] = "day"
                result["effective_date_basis"] = (
                    "reported" if pair[0] == "interval"
                    else "derived_from_reported_components"
                )
                if pair[0] == "interval" and result.get("effective_date_end") == pair[2]:
                    result["effective_date_end_precision"] = "day"
                    result["effective_date_end_basis"] = "reported"
        if result.get("effective_date_precision"):
            details["date_evidence_rule"] = "nj_effective_field_v1"
            details["effective_date_source_field"] = "Effective Date"
            details["effective_date_source_text"] = source_text

    _add_reported_day_evidence(state, raw, rec, result, details)
    _add_source_type_evidence(state, raw, rec, result, details)
    if details:
        result["source_details"] = json.dumps(details, sort_keys=True, ensure_ascii=False)
    return result
