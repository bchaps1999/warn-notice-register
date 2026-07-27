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


# Where a filer stops naming the company and starts naming the site. WARN
# forms have one employer field, so states append the plant, store number,
# airport, campus or trading name to it: "Ford Motor Co. - Flat Rock",
# "KMART - STORE #3671", "Aramark Campus, LLC (University of Kentucky)".
# Everything from the first of these onward describes where, not who.
_QUALIFIER = re.compile(
    r"""
      \s+[-–—]\s* | [-–—]\s+      # a dash with a space on either side.
                                  # Spaced on the left is the common form
                                  # ("Ford Motor Co. - Flat Rock"); spaced
                                  # only on the right is just as common a
                                  # typo ("General Electric Company-
                                  # Lexington") and cost that employer any
                                  # cut at all, so the whole filed name went
                                  # to the matcher and missed. A hyphen
                                  # inside a name has a space on neither
                                  # side, which is what keeps Wal-Mart,
                                  # Harley-Davidson, Coca-Cola and
                                  # Sanmina-SCI whole.
    | \s*[\(\[\{"“]               # parenthetical or quoted nickname
    | \s+(?:dba|d/b/a|aka|a/k/a|fka|f/k/a)\b
    | \s*/\s*(?:updated|revised|amended|new|rescinded|cancelled)\b
                                  # "Thomson Inc / UPDATED" — an edit marker
                                  # some states append. Only these words: a
                                  # bare slash joins a parent to its site in
                                  # "Pfizer/Pharmacia" and names one company
                                  # in "Bridgestone/Firestone", and nothing
                                  # in the string says which.
    | \s*,\s*(?=[A-Z]{2}\b)       # ", FL 32399" — a trailing address
    | \s+(?:store|plant|facility|location|branch|site|unit)?\s*\#\s*\d
    """,
    re.IGNORECASE | re.VERBOSE,
)
# Leading edit markers some states prepend: "*Updated* Acme, Inc."
_LEAD_MARKER = re.compile(r"^\s*[*\[]\s*(updated|revised|amended|new)[^*\]]*[*\]]\s*", re.I)

# A street address appended to the company name. Florida does this on half
# of its notices and leaves the location column empty, so "Staples 2305 S.W.
# 32nd Avenue, Bldg. L Pembroke Park, FL 33023" is the whole record of both
# who and where — the address is in the name because it is nowhere else.
#
# The house number must not begin the string, because a leading number is
# usually part of the name: "99 Cents Only Store", "118 Churchill Avenue
# Corporation", "255 Peter's Street Lounge". And a number alone is not an
# address, or "Kmart 3671" would lose a store number that is at least
# arguably part of the site. So the tail has to corroborate itself.
_HOUSE_NUMBER = re.compile(r"(?<=\S)\s+(?:\d{2,6}(?=\s)|P\.?\s*O\.?\s+Box\b)", re.I)
_ADDRESSISH = re.compile(
    r"""
      \b(?:ave|avenue|st|street|rd|road|blvd|boulevard|dr|drive|hwy|highway
        |way|ln|lane|pkwy|parkway|ct|court|cir|circle|pl|place|ste|suite
        |bldg|building|fl|floor|rte|route|turnpike|trail|terrace|plaza)\b\.?
    | \bP\.?\s*O\.?\s+Box\b
    | \b[A-Za-z]{2}\.?\s*\d{5}(?:-\d{4})?\b          # ", FL 33023"
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _sane_cut(text: str, match: re.Match) -> bool:
    """Whether a qualifier match is really the start of a site.

    Only one form needs checking: a dash with a space after it and none
    before. That form is genuinely ambiguous, because it is written both by
    a state appending a plant — "General Electric Company- Lexington" — and
    by one putting a stray space inside a hyphenated company name. The
    second is common, and the four cases it produced here are all alike:

        Apple- Metro, Inc.        LOUISIANA- PACIFIC CORPORATION
        Anheuser- Busch           Take- Two Interactive Software

    Three of those landed on the right company anyway, because Anheuser,
    Louisiana and Take-Two each dominate their first word. Apple does not:
    cutting it left "Apple", which matched Apple Inc. for a New York
    Applebee's franchisee — a wrong identity published as fact.

    What separates them is length. A site qualifier follows a company's
    whole name, which is usually several words; a split hyphenated name
    leaves exactly one behind. So a one-word head from this form is refused,
    and the employer stays unidentified, which is the safe way to be wrong.
    """
    if not re.match(r"[-–—]\s", match.group()):
        return True
    head = text[: match.start()].strip()
    return len(head.split()) > 1


def _address_start(text: str) -> int | None:
    """Where a street address begins inside an employer name, if it does.

    Conservative on purpose: a house number only counts when what follows
    it reads like an address — a street type, a PO box, or a ZIP. Without
    that second condition the rule eats store numbers and any company whose
    name simply contains a number.
    """
    for m in _HOUSE_NUMBER.finditer(text):
        if _ADDRESSISH.search(text, m.end()):
            return m.start()
    return None


def filed_address(value: str | None) -> str | None:
    """The address part of an employer name, for states that append one.

    The counterpart of base_employer: that returns who, this returns where.
    Given to the place resolver as a last resort, so a notice whose location
    column is empty is not treated as locationless when its address was
    sitting in the name all along.
    """
    if not value:
        return None
    start = _address_start(value)
    if start is None:
        return None
    return value[start:].strip(" ,-–—") or None


def base_employer(value: str | None) -> str | None:
    """The company part of an employer name, without the site it names.

    Used only to retry a failed match: a name that identifies a company
    outright is never touched, and the filed name is what gets displayed
    and keyed. Cutting matters twice over, because cleanco strips a legal
    form only at the end of a string — so "Ford Motor Co. - Flat Rock"
    keeps its "Co." as an interior token until the qualifier goes.
    """
    if not value:
        return None
    text = _LEAD_MARKER.sub("", value).strip()
    # Whichever comes first: the qualifier that starts naming a site, or the
    # street address of one. Florida writes the address with no qualifier at
    # all, so neither rule alone reaches "Staples 2305 S.W. 32nd Avenue".
    cuts = [m.start() for m in _QUALIFIER.finditer(text) if _sane_cut(text, m)]
    address = _address_start(text)
    if address is not None:
        cuts.append(address)
    if cuts:
        text = text[: min(cuts)]
    text = text.strip().rstrip(",;:-–— ")
    # Too little left to identify anyone, or nothing was actually cut.
    if len(text) < 4 or not any(c.isalpha() for c in text):
        return None
    return text if text != value.strip() else None


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
