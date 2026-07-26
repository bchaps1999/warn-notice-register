"""Normalize a raw per-state CSV into canonical records.

Wraps Big Local News's warn-transformer per-state Transformer classes
(Apache-2.0), but transforms row-by-row with error capture instead of their
all-or-nothing transform(): upstream raises KeyError on any date/jobs value
missing from its manual correction tables, which for us must degrade into a
counted parse failure, not a crash.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path


@dataclass
class NormalizeResult:
    state: str
    records: list[dict] = field(default_factory=list)
    raw_rows: int = 0
    failed_rows: int = 0
    failure_examples: list[str] = field(default_factory=list)

    @property
    def failure_rate(self) -> float:
        return self.failed_rows / self.raw_rows if self.raw_rows else 0.0


def get_transformer_class(postal: str):
    """Resolve the Transformer for a state: our custom module wins, else BLN's."""
    postal = postal.lower()
    try:
        mod = import_module(f"warnlive.normalize.custom.{postal}")
    except ModuleNotFoundError:
        mod = import_module(f"warn_transformer.transformers.{postal}")
    return mod.Transformer


def normalize_file(postal: str, input_dir: Path, source_url: str | None) -> NormalizeResult:
    """Normalize input_dir/{postal}.csv into canonical records."""
    postal = postal.lower()
    transformer = get_transformer_class(postal)(Path(input_dir))
    result = NormalizeResult(state=postal.upper())

    rows = transformer.prep_row_list(transformer.raw_data)
    result.raw_rows = len(rows)

    for row in rows:
        try:
            data = transformer.transform_row(row)
            validated = transformer.schema().load(data)
        except Exception as e:  # noqa: BLE001 — any bad row becomes a counted failure
            result.failed_rows += 1
            if len(result.failure_examples) < 5:
                result.failure_examples.append(f"{type(e).__name__}: {e}")
            continue
        rec = _to_canonical(validated, row, source_url)
        if rec["employer_name"] is None:
            result.failed_rows += 1
            if len(result.failure_examples) < 5:
                result.failure_examples.append("row has no employer name")
            continue
        result.records.append(rec)
    return result


def _to_canonical(validated: dict, raw_row: dict, source_url: str | None) -> dict:
    state = validated["postal_code"].upper()
    notice_date = _iso(validated.get("notice_date"))
    is_closure = validated.get("is_closure")
    layoff_type = (
        "closure" if is_closure else "mass_layoff" if is_closure is False else "unknown"
    )
    if layoff_type == "unknown":
        layoff_type = _classify_from_raw(raw_row) or "unknown"
    is_temporary = _to_int(validated.get("is_temporary"))
    if is_temporary is None:
        is_temporary = _temporary_from_raw(raw_row)
    # A reported count of 0 means "not reported", not zero workers.
    jobs = validated.get("jobs") or None
    rec = {
        "state": state,
        "employer_name": _clean_text(validated.get("company")),
        "location": _clean_text(validated.get("location")),
        "notice_date": notice_date,
        "effective_date": _iso(validated.get("effective_date")),
        "employees_affected": jobs,
        "layoff_type": layoff_type,
        "is_temporary": is_temporary,
        "is_amendment": int(bool(validated.get("is_amendment"))),
        "source_url": source_url,
        "source_notice_id": validated.get("hash_id"),
        # DictReader can emit a None key (extra cells beyond the header)
        "raw_extra": json.dumps(
            {(k if k is not None else "_restkey"): v for k, v in raw_row.items()},
            sort_keys=True,
            ensure_ascii=False,
        ),
    }
    rec["dedupe_key"] = _dedupe_key(rec)
    rec["raw_record_hash"] = _record_hash(rec)
    return rec


def _dedupe_key(rec: dict) -> str:
    parts = "|".join(
        [
            rec["state"],
            _fold(rec["employer_name"]),
            rec["notice_date"] or "",
            _fold(rec["location"]),
        ]
    )
    return hashlib.sha1(parts.encode("utf-8")).hexdigest()


def _record_hash(rec: dict) -> str:
    from warnlive.store.dedupe import VERSIONED_FIELDS

    payload = json.dumps({f: rec[f] for f in VERSIONED_FIELDS}, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


_JUNK_VALUES = {"", ".", "-", "n/a", "na", "none", "unknown", "tbd"}


_TAG = re.compile(r"<[a-zA-Z/!][^>]*>")


def _clean_text(value: str | None) -> str | None:
    """Display-value hygiene: normalize NBSP and whitespace, strip, and null
    out placeholder junk. (Distinct from _fold, which is key-only.)

    Sources sometimes leak markup into name fields (e.g. WI rows arriving as
    'Company<br/><em>* footnote…</em>'). When a real tag is present, the name
    is the text before the first tag — everything after is display chrome.
    HTML entities (&amp;, &quot;) are unescaped either way."""
    if value is None:
        return None
    if _TAG.search(value):
        value = value.split("<", 1)[0]
    v = html.unescape(value)
    v = _WS.sub(" ", v.replace("\xa0", " ")).strip()
    return None if v.lower() in _JUNK_VALUES else v


def _type_columns(raw_row: dict):
    """Yield values of raw columns that plausibly carry the layoff/closure
    type, across the naming conventions states actually use."""
    for k, v in raw_row.items():
        if not k or not isinstance(v, str) or not v:
            continue
        kl = k.lower()
        if (
            "closure" in kl
            or kl in ("action_type", "warn_type", "type")
            or ("type" in kl and ("layoff" in kl or "notice" in kl or "action" in kl))
        ):
            yield v.lower()


def _classify_from_raw(raw_row: dict) -> str | None:
    """Fallback layoff_type when a state's transformer doesn't classify:
    read the type column most states publish (preserved in raw_extra)."""
    for v in _type_columns(raw_row):
        if "clos" in v:
            return "closure"
        if any(t in v for t in ("layoff", "lay-off", "lay off", "reduction", "downsiz")):
            return "mass_layoff"
    return None


def _temporary_from_raw(raw_row: dict) -> int | None:
    for k, v in raw_row.items():
        if not k or not isinstance(v, str) or not v:
            continue
        kl = k.lower()
        if "temporar" in kl or "permanent" in kl or ("type" in kl and "layoff" in kl):
            vl = v.lower()
            if "temp" in vl:
                return 1
            if "perman" in vl:
                return 0
    return None


_SUFFIXES = re.compile(
    r"\b(inc|incorporated|llc|l\.l\.c|llp|ltd|limited|corp|corporation|co|company)\b\.?",
)
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")


def normalized_employer(value: str | None) -> str | None:
    """Standardized employer name for cross-notice/cross-state matching:
    legal suffixes stripped via cleanco's curated list (applied twice for
    nested forms like 'X, LLC, Inc.'), lowercased, punctuation collapsed,
    leading article dropped — states file "The Boeing Company" where the
    SEC registers "BOEING CO", and the article carries no identity.
    Display names stay untouched — this is a derived matching column."""
    if not value:
        return None
    from cleanco import basename

    v = value
    for _ in range(2):
        v = basename(v)
    v = _NON_ALNUM.sub(" ", v.lower())
    v = _WS.sub(" ", v).strip()
    if v.startswith("the ") and len(v) > 4:
        v = v[4:]
    return v or None


def _fold(value: str | None) -> str:
    """Normalize a name/location for the dedupe key only (never for display):
    lowercase, strip corporate suffixes and punctuation, collapse whitespace."""
    if not value:
        return ""
    v = value.lower()
    v = _SUFFIXES.sub(" ", v)
    v = _NON_ALNUM.sub(" ", v)
    return _WS.sub(" ", v).strip()


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _to_int(value) -> int | None:
    return None if value is None else int(bool(value))
