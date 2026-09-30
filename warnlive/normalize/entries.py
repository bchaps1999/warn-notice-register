"""Decide what one listed entry of a frozen source document is.

``engine._dedupe_key`` treats rows sharing a key as re-observations of one
notice, so ``dedupe.ingest`` folds distinct same-key rows into versions (the
last wins). Inside a single document (an annual PDF, a workbook, a dashboard
CSV) two such rows cannot be re-observations over time: they are separate
listing entries. This module keeps them apart, or, where the source ties
them together, turns them into one filing with an itemization. The unit is
the agency's listed entry:

- ``qualify_same_document_entries`` (states with ``same_key_policy:
  distinct_rows`` in states.yaml): in a key group with more than one distinct
  row *of one document*, one row keeps the legacy key and every other
  distinct row gets a content-qualified key. Identical rows still coalesce.
  A capture that concatenates several documents (CA's raw file joins the
  EDD fiscal-year reports) names each row's document in
  ``same_key_document_fields``; same-key rows of different documents are a
  notice re-listed over time and stay on the versions path.
- ``fold_phase_group``: rows of one filing that itemize phases (Wisconsin's
  archived logs) become one notice with ``phases[]``, summed workers and the
  min/max action dates.
- ``control_number_versions``: New York detail pages that repeat one Control
  Number are one filing; its pages become versions in detail-id order.
- ``fold_identical_updates``: a Wisconsin/Florida "update" row whose content
  exactly equals an earlier row for the same employer and location becomes a
  version of that earlier notice (see migrate/wi_fl_update_transition.py for
  the released keys this retires).

The live scrape (pipeline.py), the raw replay (clean_rebuild.build) and the
archive replay (offline_rebuild._cached_agencies) call these at the same
point, before ``dedupe.split_collisions``, so both paths assign the same keys
to the same rows. Inputs are normalized canonical records; the database is
only read, to keep a stored notice's key on the row it already holds. No
network access.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from functools import lru_cache

from warnlive.normalize.engine import _fold, _record_hash

#: Content that distinguishes two entries of one document sharing a key. It
#: holds no source-row position, so it does not churn when a document is
#: re-captured with rows reordered or raw cells reformatted.
DISCRIMINATOR_FIELDS = ("effective_date", "employees_affected", "layoff_type")
ENTRY_BASIS = "same_document_distinct_row"

#: States whose "update" rows repeat an earlier row's content (block G).
UPDATE_VERSION_STATES = frozenset({"WI", "FL"})
UPDATE_CONTENT_FIELDS = ("effective_date", "employees_affected", "layoff_type", "is_temporary")
UPDATE_BASIS = "update_row_identical_content"


@lru_cache(maxsize=1)
def _policies() -> dict[str, str]:
    from warnlive.registry import load_registry

    return {cfg.postal.upper(): cfg.same_key_policy for cfg in load_registry().all()}


def same_key_policy(state: str) -> str:
    """The configured policy for rows of one document sharing a key."""
    return _policies().get(state.upper(), "versions")


@lru_cache(maxsize=1)
def _document_fields() -> dict[str, tuple[str, ...]]:
    from warnlive.registry import load_registry

    return {cfg.postal.upper(): tuple(cfg.same_key_document_fields or ())
            for cfg in load_registry().all()}


def source_document(state: str, rec: dict, fields: tuple[str, ...] | None = None) -> str:
    """The source document a row was listed in: the first non-blank
    configured raw field, else ``source_details.source_artifact``; blank
    when the state names none (the capture is one document)."""
    fields = _document_fields().get(state.upper(), ()) if fields is None else fields
    if not fields:
        return ""
    raw = _raw(rec)
    for name in fields:
        value = raw.get(name)
        if value not in (None, ""):
            return f"{name}={value}"
    artifact = _details(rec).get("source_artifact")
    return f"source_artifact={artifact}" if artifact else ""


def _details(rec: dict) -> dict:
    return json.loads(rec.get("source_details") or "{}")


def _with_details(rec: dict, details: dict, **changes) -> dict:
    out = dict(rec, **changes)
    out["source_details"] = json.dumps(details, sort_keys=True, ensure_ascii=False)
    out["raw_record_hash"] = _record_hash(out)
    return out


def _discriminator(rec: dict) -> list:
    return [rec.get(name) for name in DISCRIMINATOR_FIELDS]


def entry_key(legacy_key: str, rec: dict) -> str:
    """The qualified key of a non-first entry sharing ``legacy_key``."""
    token = json.dumps(_discriminator(rec), separators=(",", ":"), default=str)
    return hashlib.sha1(f"{legacy_key}|entry|{token}".encode()).hexdigest()


def _stored(conn: sqlite3.Connection | None, key: str) -> tuple[str | None, set[str], list | None]:
    """Current hash, every version hash, and current discriminator under a key."""
    if conn is None:
        return None, set(), None
    rows = conn.execute(
        "SELECT v.raw_record_hash AS hash, v.fields_json AS fields, "
        "v.version = n.current_version AS current FROM notices n "
        "JOIN notice_versions v ON v.notice_id = n.id WHERE n.dedupe_key = ?",
        (key,),
    ).fetchall()
    current_hash = current_disc = None
    for row in rows:
        if row["current"]:
            current_hash = row["hash"]
            current_disc = _discriminator(json.loads(row["fields"]))
    return current_hash, {row["hash"] for row in rows}, current_disc


def _key_exists(conn: sqlite3.Connection | None, key: str) -> bool:
    return conn is not None and conn.execute(
        "SELECT 1 FROM notices WHERE dedupe_key = ?", (key,)
    ).fetchone() is not None


def qualify_same_document_entries(
    conn: sqlite3.Connection | None, state: str, records: list[dict],
    policy: str | None = None, document_fields: tuple[str, ...] | None = None,
) -> tuple[list[dict], dict]:
    """Give each distinct same-key row of one document its own entry key.

    Rows are grouped by (``dedupe_key``, source document); only groups
    holding more than one distinct ``raw_record_hash`` change. Same-key rows
    of different documents are left alone, so ingest folds them into one
    notice's versions (a re-listing with a corrected count).
    The row that keeps the legacy key is, in order of preference: the row
    whose hash is the stored notice's current version; a row matching any
    stored version; a row with the stored current version's discriminator;
    the first row in source order whose qualified key is not already a
    stored notice; the first row in source order. Singletons and first
    entries therefore keep every released key. Records keep source order.
    """
    policy = policy or same_key_policy(state)
    report = {"policy": policy, "groups": 0, "qualified_rows": 0,
              "qualified_keys": 0, "discriminator_collisions": 0,
              "cross_document_key_groups": 0}
    if policy != "distinct_rows":
        return records, report
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, rec in enumerate(records):
        groups[(rec["dedupe_key"], source_document(state, rec, document_fields))].append(index)
    documents_per_key: dict[str, int] = defaultdict(int)
    for key, _document in groups:
        documents_per_key[key] += 1
    report["cross_document_key_groups"] = sum(n > 1 for n in documents_per_key.values())
    out = list(records)
    for (key, _document), indexes in groups.items():
        by_hash: dict[str, list[int]] = {}
        for index in indexes:
            by_hash.setdefault(records[index]["raw_record_hash"], []).append(index)
        if len(by_hash) < 2:
            continue
        report["groups"] += 1
        order = list(by_hash)
        current_hash, stored_hashes, current_disc = _stored(conn, key)
        first = {h: records[by_hash[h][0]] for h in order}
        keeper = (
            next((h for h in order if h == current_hash), None)
            or next((h for h in order if h in stored_hashes), None)
            or (next((h for h in order if _discriminator(first[h]) == current_disc), None)
                if current_disc is not None else None)
            or next((h for h in order if not _key_exists(conn, entry_key(key, first[h]))), None)
            or order[0]
        )
        new_keys: dict[str, str] = {}
        for h in order:
            if h == keeper:
                continue
            new_key = entry_key(key, first[h])
            if new_key in new_keys.values():
                report["discriminator_collisions"] += 1
            new_keys[h] = new_key
            for index in by_hash[h]:
                details = _details(records[index])
                details["entry"] = {"basis": ENTRY_BASIS,
                                    "discriminator_fields": list(DISCRIMINATOR_FIELDS)}
                out[index] = _with_details(records[index], details, dedupe_key=new_key)
                report["qualified_rows"] += 1
        report["qualified_keys"] += len(set(new_keys.values()))
    return out, report


def fold_phase_group(rows: list[dict]) -> tuple[dict, str]:
    """One filing notice from same-key rows that itemize its phases.

    Workers are summed only when every phase reports a count and no phase
    is an update of another (an original row plus its "update" row can
    restate the same workers, e.g. WI Fleming 2003: 134 then 144): the sum
    is arithmetic on the agency's own itemization, never an estimate. Dates
    span the earliest to the latest phase. Returns the notice and the
    record hash of the row whose fields it is built on (the earliest phase).
    """
    distinct: list[dict] = []
    seen: set[str] = set()
    for rec in rows:
        if rec["raw_record_hash"] not in seen:
            seen.add(rec["raw_record_hash"])
            distinct.append(rec)
    dated = [rec["effective_date"] for rec in distinct if rec.get("effective_date")]
    start, end = (min(dated), max(dated)) if dated else (None, None)
    base = next((rec for rec in distinct if rec.get("effective_date") == start), distinct[0])
    counts = [rec.get("employees_affected") for rec in distinct]
    mixed = len({int(rec.get("is_amendment") or 0) for rec in distinct}) > 1
    complete = all(value is not None for value in counts) and not mixed
    types = {rec.get("layoff_type") for rec in distinct}
    details = _details(base)
    details.update({
        "phases": [{
            "effective_date": rec.get("effective_date"),
            "workers": rec.get("employees_affected"),
            "layoff_type": rec.get("layoff_type"),
            "is_amendment": rec.get("is_amendment"),
            "source_cells": json.loads(rec["raw_extra"]) if rec.get("raw_extra") else None,
        } for rec in distinct],
        "filing_unit": {"basis": "same_document_same_key_phase_rows",
                        "rows": len(distinct)},
        "effective_date_interpretation": "list_or_phases",
        "worker_allocation": ("itemized_phases" if complete
                              else "original_and_update_rows" if mixed
                              else "incomplete_itemization"),
    })
    if complete:
        details["total_workers"] = sum(counts)
    changes = {
        "effective_date": start,
        "effective_date_end": end if end != start else None,
        "effective_date_end_precision": "day" if end != start else None,
        "effective_date_end_basis": "reported" if end != start else None,
        "employees_affected": sum(counts) if complete else None,
        "layoff_type": types.pop() if len(types) == 1 else "unknown",
        "is_amendment": max(int(rec.get("is_amendment") or 0) for rec in distinct),
    }
    return _with_details(base, details, **changes), base["raw_record_hash"]


def _raw(rec: dict) -> dict:
    try:
        value = json.loads(rec.get("raw_extra") or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def control_number_versions(key: str, rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Versions of the NY filings behind a same-key group of detail pages.

    Returns (records in ingest order, rows held). One Control Number keeps
    the legacy key; several get ``sha1(key|control|CN)`` each. A filing's
    distinct pages become its versions ordered by detail id, so the page
    with the highest id is current. Pages that differ only in whitespace
    already share a record hash and coalesce. A page without a Control
    Number cannot be grouped, so the whole key group is held.
    """
    numbers = [(_raw(rec).get("Control Number") or "").strip() for rec in rows]
    if not all(numbers):
        return [], rows
    by_number: dict[str, list[dict]] = defaultdict(list)
    for number, rec in zip(numbers, rows):
        by_number[number].append(rec)
    out: list[dict] = []
    for number, members in by_number.items():
        new_key = key if len(by_number) == 1 else hashlib.sha1(
            f"{key}|control|{number}".encode()).hexdigest()
        members = sorted(members, key=lambda rec: int(_raw(rec).get("wayback_id") or 0))
        # Each distinct content is placed at its latest page, which leads its
        # repeats: the first copy survives coalescing, so the highest detail
        # id ends up as the current version.
        latest: dict[str, int] = {}
        for position, rec in enumerate(members):
            latest[rec["raw_record_hash"]] = position
        members = [rec for digest in sorted(latest, key=latest.get)
                   for rec in [members[latest[digest]]] + [
                       other for position, other in enumerate(members)
                       if other["raw_record_hash"] == digest and position != latest[digest]]]
        multiple = len(latest) > 1
        for rec in members:
            details = _details(rec)
            if multiple:
                details["version_order"] = {"basis": "agency_detail_id_order"}
            if len(by_number) > 1:
                details["entry"] = {"basis": "distinct_control_number",
                                    "control_number": number}
            out.append(_with_details(rec, details, dedupe_key=new_key)
                       if multiple or len(by_number) > 1 else rec)
    return out, []


def _received(rec: dict) -> str | None:
    details = _details(rec)
    return (details.get("agency_received_date") or details.get("legacy_notice_key_date")
            or details.get("agency_notification_date") or rec.get("notice_date"))


def fold_identical_updates(state: str, records: list[dict]) -> tuple[list[dict], dict]:
    """Make a WI/FL update row that repeats an earlier row a version of it.

    The update must be flagged as an amendment, received strictly later, and
    match the earlier row for the same (folded) employer and location on
    every field of ``UPDATE_CONTENT_FIELDS``. A row with neither an action
    date nor a worker count has no content to match and is left alone, as is
    a pair where neither row is an update. The folded row takes the earlier
    notice's key and records the key it would have had; it is placed right
    after its parent's rows so ingest appends it as the next version.
    """
    report = {"folded_rows": 0, "skipped_contentless_updates": 0}
    if state.upper() not in UPDATE_VERSION_STATES:
        return records, report
    groups: dict[tuple, list[int]] = defaultdict(list)
    for index, rec in enumerate(records):
        groups[(_fold(rec.get("employer_name")), _fold(rec.get("location")))].append(index)
    parent_of: dict[int, int] = {}
    for indexes in groups.values():
        if len(indexes) < 2:
            continue
        ordered = sorted((i for i in indexes if _received(records[i])),
                         key=lambda i: (_received(records[i]), i))
        for position, index in enumerate(ordered):
            rec = records[index]
            if not rec.get("is_amendment"):
                continue
            content = tuple(rec.get(name) for name in UPDATE_CONTENT_FIELDS)
            parent = next((
                earlier for earlier in ordered[:position]
                if _received(records[earlier]) < _received(rec)
                and tuple(records[earlier].get(n) for n in UPDATE_CONTENT_FIELDS) == content
            ), None)
            if parent is None:
                continue
            if rec.get("effective_date") is None and rec.get("employees_affected") is None:
                report["skipped_contentless_updates"] += 1
                continue
            while parent in parent_of:
                parent = parent_of[parent]
            parent_of[index] = parent
    if not parent_of:
        return records, report
    children: dict[int, list[int]] = defaultdict(list)
    for child, parent in sorted(parent_of.items(),
                                key=lambda item: (_received(records[item[0]]), item[0])):
        children[parent].append(child)
    out: list[dict] = []
    for index, rec in enumerate(records):
        if index in parent_of:
            continue
        out.append(rec)
        for child in children.get(index, []):
            details = _details(records[child])
            details["version_of"] = {"basis": UPDATE_BASIS,
                                     "retired_dedupe_key": records[child]["dedupe_key"]}
            out.append(_with_details(records[child], details, dedupe_key=rec["dedupe_key"]))
            report["folded_rows"] += 1
    return out, report


def prepare_batch(
    conn: sqlite3.Connection | None, state: str, records: list[dict],
) -> tuple[list[dict], dict]:
    """The entry rules every ingest path applies before split_collisions.

    Live scrape, raw replay and archive replay call this one function so the
    same source rows get the same keys on either path.
    """
    records, updates = fold_identical_updates(state, records)
    records, entries = qualify_same_document_entries(conn, state, records)
    return records, {**entries, "update_versions": updates}
