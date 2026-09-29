"""Normalize a raw per-state CSV into canonical records.

Wraps Big Local News's warn-transformer per-state Transformer classes
(Apache-2.0), but transforms row-by-row with error capture instead of their
all-or-nothing transform(). Upstream raises KeyError on any date/jobs value
missing from its manual correction tables. Those fields are optional, so an
unparseable date or worker count is blanked, the row is kept, and a
``parse_notes`` entry in ``source_details`` records the source cell; the raw
row stays in ``raw_extra``. Any other failure is a counted parse failure.

Upstream correction tables are audited per cell (see ``corrections``): a
correction that does not match a date written in its cell is replaced by the
cell's own date or null, never kept as a guess. The future-date sanity check
is pinned to the run's observed date so replays do not depend on the day
they run. Input: ``{postal}.csv`` in a raw directory. No network access.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from importlib import import_module
from pathlib import Path

from warnlive.normalize.corrections import sanitized_corrections
from warnlive.normalize.nonnotice import employer_hold_reason, non_notice_reason


@dataclass
class NormalizeResult:
    state: str
    records: list[dict] = field(default_factory=list)
    raw_rows: int = 0
    failed_rows: int = 0
    failure_examples: list[str] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)
    # Rows that are not notices (header/total rows, agency test entries).
    # They are also counted in failed_rows/failures, with a ``hold_reason``,
    # so every caller's row accounting stays complete; they are left out of
    # failure_rate because they are not parser failures.
    held_rows: int = 0

    @property
    def failure_rate(self) -> float:
        failed = self.failed_rows - self.held_rows
        return failed / self.raw_rows if self.raw_rows else 0.0


def get_transformer_class(postal: str):
    """Resolve the Transformer for a state: our custom module wins, else BLN's."""
    postal = postal.lower()
    try:
        mod = import_module(f"warnlive.normalize.custom.{postal}")
    except ModuleNotFoundError:
        mod = import_module(f"warn_transformer.transformers.{postal}")
    return mod.Transformer


def normalize_file(
    postal: str, input_dir: Path, source_url: str | None,
    observed_at: str | date | None = None,
) -> NormalizeResult:
    """Normalize input_dir/{postal}.csv into canonical records.

    ``observed_at`` (YYYY-MM-DD, default today) is the day the source was
    captured. It anchors upstream's "too far in the future" date check so a
    replay of a dated capture gives the same result on any day.
    """
    postal = postal.lower()
    observed = _observed_date(observed_at)
    transformer = get_transformer_class(postal)(Path(input_dir))
    guard = _FieldGuard(transformer, observed)
    result = NormalizeResult(state=postal.upper())

    rows = transformer.prep_row_list(transformer.raw_data)
    result.raw_rows = len(rows)

    for prepared_row, row in enumerate(rows, start=1):
        non_notice = non_notice_reason(postal.upper(), row)
        if non_notice:
            _record_failure(result, row, prepared_row, f"non_notice_row: {non_notice}",
                            hold_reason=non_notice)
            continue
        try:
            data, notes = guard.transform_row(row)
            validated = transformer.schema().load(data)
            rec = _to_canonical(validated, row, source_url, parse_notes=notes)
        except Exception as e:  # noqa: BLE001 — any bad row becomes a counted failure
            _record_failure(result, row, prepared_row, f"{type(e).__name__}: {e}")
            continue
        if rec["employer_name"] is None:
            _record_failure(result, row, prepared_row, "row has no employer name")
            continue
        held = employer_hold_reason(rec["employer_name"])
        if held:
            _record_failure(result, row, prepared_row, f"non_notice_row: {held}",
                            hold_reason=held)
            continue
        # This is an ordinal in the transformer's prepared row list, not a
        # physical CSV line number (quoted fields can span several lines).
        rec["prepared_row"] = prepared_row
        result.records.append(rec)
    return result


def _record_failure(
    result: NormalizeResult, row: dict, prepared_row: int, error: str,
    hold_reason: str | None = None,
) -> None:
    raw_extra = json.dumps(
        {(k if k is not None else "_restkey"): v for k, v in row.items()},
        sort_keys=True, ensure_ascii=False, default=str,
    )
    result.failed_rows += 1
    if len(result.failure_examples) < 5:
        result.failure_examples.append(error)
    result.failures.append({
        "state": result.state, "prepared_row": prepared_row,
        "source_row_sha256": hashlib.sha256(raw_extra.encode()).hexdigest(),
        "raw_extra": raw_extra, "error": error,
    })
    if hold_reason:
        result.held_rows += 1
        result.failures[-1]["hold_reason"] = hold_reason


def _observed_date(value: str | date | None) -> date:
    if value is None:
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


class _AuditedCorrections(dict):
    """An upstream correction table whose lookups are recorded."""

    def __init__(self, table: dict, decisions: dict, log: list):
        super().__init__(table)
        self.decisions = decisions
        self.log = log

    def __getitem__(self, key):
        value = super().__getitem__(key)  # a missing key raises as upstream expects
        decision = self.decisions.get(key)
        if decision is not None:
            self.log.append(("correction", key, decision))
        return value


class _FieldGuard:
    """Run upstream transform_row with optional fields made non-fatal.

    * transform_date/transform_jobs errors (upstream raises KeyError for a
      value missing from its correction table) blank that field;
    * the date correction table is replaced by the audited one;
    * the future-date window is anchored to the observed day.

    Returns the transformed dict and parse notes naming the source cells.
    """

    def __init__(self, transformer, observed: date):
        self.transformer = transformer
        cls = type(transformer)
        self.upstream_corrections = cls.date_corrections
        table, decisions = sanitized_corrections(cls, observed)
        self.log: list = []
        self.corrections = _AuditedCorrections(table, decisions, self.log)
        self.changed = {key for key, d in decisions.items() if d.used != d.upstream}
        transformer.date_corrections = self.corrections
        # schema.transform_date compares against datetime.today() plus
        # max_future_days; shift the allowance so the limit is the observed
        # day plus the transformer's own allowance, whatever day this runs.
        transformer.max_future_days = (
            cls.max_future_days + (observed - date.today()).days
        )
        self._date = transformer.transform_date
        self._jobs = transformer.transform_jobs
        transformer.transform_date = self._guard_date
        transformer.transform_jobs = self._guard_jobs

    def _guard_date(self, value):
        start = len(self.log)
        try:
            return self._date(value)
        except (KeyError, ValueError, TypeError, AssertionError, AttributeError) as e:
            # Upstream checks the year of a correction it assumes is a date,
            # so an audited null correction surfaces here as AttributeError;
            # its correction note already says why the field is empty.
            if not any(kind == "correction" for kind, _, _ in self.log[start:]):
                self.log.append(("unparseable_date", value, type(e).__name__))
            return None

    def _guard_jobs(self, value):
        try:
            return self._jobs(value)
        except (KeyError, ValueError, TypeError, AssertionError) as e:
            self.log.append(("unparseable_jobs", value, type(e).__name__))
            return None

    def _raw(self, row: dict, name: str):
        method = self.transformer.fields.get(name)
        if method is None:
            return None
        try:
            value = self.transformer.get_raw_value(row, method)
        except Exception:  # noqa: BLE001 — a missing column is not a note
            return None
        return value.strip() if isinstance(value, str) else value

    def transform_row(self, row: dict) -> tuple[dict, list[dict]]:
        self.log.clear()
        data = self.transformer.transform_row(row)
        notes = self._notes(row, data)
        if any(kind == "correction" and key in self.changed for kind, key, _ in self.log):
            # Keep upstream's row hash (source_notice_id) stable: it is the
            # BLN identifier other tables refer to. Only the audited values
            # are published.
            self.transformer.date_corrections = self.upstream_corrections
            try:
                data["hash_id"] = self.transformer.transform_row(row)["hash_id"]
            except Exception:  # noqa: BLE001 — upstream would have dropped it
                pass
            finally:
                self.transformer.date_corrections = self.corrections
        return data, notes

    def _notes(self, row: dict, data: dict) -> list[dict]:
        notes: list[dict] = []
        fields = {name: self._raw(row, name)
                  for name in ("notice_date", "effective_date", "jobs")}
        for kind, value, extra in self.log:
            text = value.strip() if isinstance(value, str) else value
            names = [name for name, raw in fields.items()
                     if raw == text and (name == "jobs") == (kind == "unparseable_jobs")]
            for name in names or [None]:
                if kind == "correction":
                    d = extra
                    if d.action in ("keep", "keep_null", "keep_linked_document") \
                            and d.precision != "month":
                        continue  # upstream value stands; nothing to record
                    notes.append({
                        "field": name, "source_text": value,
                        "rule": "upstream_date_correction_audit_v1",
                        "action": d.action, "reason": d.reason,
                        "upstream_value": d.upstream.isoformat() if d.upstream else None,
                        "value": d.used.isoformat() if d.used else None,
                        "precision": d.precision,
                    })
                else:
                    notes.append({
                        "field": name, "source_text": value,
                        "rule": "unparseable_optional_field_v1",
                        "action": "blanked", "reason": kind, "error": extra,
                    })
        return notes


def _to_canonical(validated: dict, raw_row: dict, source_url: str | None,
                  parse_notes: list[dict] | None = None) -> dict:
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
    from warnlive.normalize.details import extract

    rec.update(extract(state, raw_row, rec))
    if parse_notes:
        _apply_parse_notes(rec, parse_notes)
    if state == "NJ":
        # NJ publishes no filing ID or notice date. Keep distinct raw table
        # observations distinct; a changed row is a new observation until a
        # reviewed correspondence establishes an amendment/event identity.
        # This is deliberately an observation anchor, not a legal filing ID.
        raw_value = json.loads(rec["raw_extra"])
        raw_sha = hashlib.sha256(json.dumps(
            raw_value, sort_keys=True, ensure_ascii=False, default=str,
            separators=(",", ":"),
        ).encode()).hexdigest()
        rec["source_identity"] = f"NJ:raw-row:{raw_sha}"
        details = json.loads(rec.get("source_details") or "{}")
        details["identity_basis"] = "raw_row_observation"
        details["source_row_sha256"] = raw_sha
        rec["source_details"] = json.dumps(details, sort_keys=True, ensure_ascii=False)
    rec["dedupe_key"] = _dedupe_key(rec)
    rec["raw_record_hash"] = _record_hash(rec)
    return rec


def _apply_parse_notes(rec: dict, notes: list[dict]) -> None:
    """Record blanked or audited fields in source_details.

    A notice date whose upstream correction was rejected keeps the upstream
    value as its internal key date (as the date-role fixes do), so an
    existing notice keeps its identity; a notice date that could not be read
    at all keys on its source text instead of colliding with undated rows.
    """
    details = json.loads(rec.get("source_details") or "{}")
    details.setdefault("parse_notes", []).extend(notes)
    for note in notes:
        name = note.get("field")
        if name not in ("notice_date", "effective_date"):
            continue
        if note.get("precision") == "month" and rec.get(name) == note.get("value"):
            rec[f"{name}_precision"] = "month"
            rec[f"{name}_basis"] = "reported"
        if name != "notice_date":
            continue
        if note["rule"] == "upstream_date_correction_audit_v1" and \
                note["upstream_value"] != note["value"]:
            details["upstream_notice_key_date"] = note["upstream_value"]
        elif note["rule"] == "unparseable_optional_field_v1":
            details["notice_key_source_text"] = note["source_text"]
    if "upstream_notice_key_date" in details and "legacy_notice_key_date" in details:
        details["legacy_notice_key_date"] = details["upstream_notice_key_date"]
    rec["source_details"] = json.dumps(details, sort_keys=True, ensure_ascii=False)


def _dedupe_key(rec: dict) -> str:
    # These sources provide filing/row identity stronger than employer +
    # notice date + place. GA often omits a notice date, historical SC reports
    # have none, and KS workforce-area names change while record IDs persist.
    # Keep legacy keys for ID-less rows and other states; only a fresh build
    # may adopt changed keys until old notices are migrated or archived.
    source_identity = rec.get("source_identity")
    if rec["state"] in {"GA", "SC", "IL", "KS", "NJ"} and source_identity:
        return hashlib.sha1(f"{rec['state']}|source|{source_identity}".encode()).hexdigest()
    # Some source rows exposed agency receipt/notification (or, in MN, even
    # a layoff-start fallback) as notice_date. Keep their old internal key
    # date while clearing the falsely labeled legal notice day. This avoids
    # collapsing unrelated observations during the date-role correction.
    key_date = rec["notice_date"]
    details = json.loads(rec.get("source_details") or "{}")
    if "upstream_notice_key_date" in details:
        key_date = details["upstream_notice_key_date"]
    elif details.get("notice_key_source_text"):
        key_date = f"text:{details['notice_key_source_text'].strip()}"
    if rec["state"] in {"KY", "MA", "NV", "WA", "MN", "WI", "FL"}:
        if rec["state"] in {"WI", "FL"}:
            # This is exactly the old transformed date, even if a malformed
            # source cell fails the stricter typed-date parser.
            key_date = (details.get("legacy_notice_key_date") or
                        details.get("agency_received_date") or
                        details.get("agency_notification_date") or key_date)
        else:
            key_date = (details.get("agency_received_date") or
                        details.get("legacy_notice_key_date") or key_date)
    # A row whose employer/location columns were corrected (NV column
    # shift, MS site split) keeps the key its filed cells always had.
    legacy = details.get("legacy_key_fields") or {}
    parts = "|".join(
        [
            rec["state"],
            _fold(legacy.get("employer_name", rec["employer_name"])),
            key_date or "",
            _fold(legacy.get("location", rec["location"])),
        ]
    )
    return hashlib.sha1(parts.encode("utf-8")).hexdigest()


def _record_hash(rec: dict) -> str:
    from warnlive.store.dedupe import DETAIL_FIELDS, VERSIONED_FIELDS

    fields = {f: rec[f] for f in VERSIONED_FIELDS}
    fields.update({f: rec[f] for f in DETAIL_FIELDS if rec.get(f) is not None})
    payload = json.dumps(fields, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


_JUNK_VALUES = {"", ".", "-", "n/a", "na", "none", "unknown", "tbd"}


_TAG = re.compile(r"<[a-zA-Z/!][^>]*>")
_LEADING_TAGS = re.compile(r"^(?:\s*<[a-zA-Z/!][^>]*>)+")


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
        # A name wrapped in markup ("<b>Gamma Inc</b>") keeps its text; only
        # what follows the name's first closing tag or break is chrome.
        value = _LEADING_TAGS.sub("", value).split("<", 1)[0]
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
