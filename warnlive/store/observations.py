"""Persist official source observations and their conservative admission decisions.

Statuses: ``admitted`` (linked to a notice), ``event_unresolved``,
``rescinded``, ``annotation``, ``identity_unresolved`` (e.g. Missouri
rapid-response rows: the workbook does not establish a WARN filing per row),
and ``not_in_agency_warn_report`` (e.g. Nebraska layoff/closure-report rows:
the agency lists them outside its WARN report; this says where the row is
listed, not that the event is shown to be non-WARN). Only ``admitted`` rows
link a notice. Writes the ``source_observations`` table; no network access.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping

_STATUSES = {"admitted", "event_unresolved", "rescinded", "annotation", "identity_unresolved",
             "not_in_agency_warn_report"}
# Official artifact directories (agency/<dir>/...) whose rows are retained here.
_ARTIFACT_STATES = {"ia": "IA", "ky": "KY", "la": "LA", "mo_historical": "MO", "ne": "NE"}


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _admission(entry: object) -> tuple[str, int | None]:
    if isinstance(entry, Mapping):
        status, notice_id = entry.get("status"), entry.get("notice_id")
    elif isinstance(entry, (tuple, list)) and len(entry) == 2:
        status, notice_id = entry
    else:
        raise ValueError("admission must supply status and notice_id")
    if status not in _STATUSES:
        raise ValueError(f"unknown observation admission status: {status}")
    if (status == "admitted") != (type(notice_id) is int and notice_id > 0):
        raise ValueError("only admitted observations may have a positive notice_id")
    return status, notice_id


def _deterministic(row: dict) -> dict:
    fields = {
        "employer": row.get("company_text"),
        "notice_date": row.get("notice_date"),
        "layoff_start": row.get("effective_date_start", row.get("effective_date")),
        "layoff_end": row.get("effective_date_end"),
        "workers_total": row.get("workers_total", row.get("workers_reported")),
    }
    result = {key: value for key, value in fields.items() if value is not None}
    street, city = row.get("street_address_text"), row.get("city_text")
    if street or city:
        result["locations"] = [{"id": "L1", **({"postal_address": street} if street else {}),
                                **({"city": city} if city else {})}]
    return result


def store_observations(conn: sqlite3.Connection, rows: list[dict],
                       admission: Mapping[str, object], source_bundle_sha256: str) -> dict:
    """Store every source row exactly once with a disposition and optional notice link."""
    if not isinstance(source_bundle_sha256, str) or not source_bundle_sha256:
        raise ValueError("source bundle fingerprint required")
    pointers = [row["source_row"] for row in rows]
    if len(set(pointers)) != len(pointers) or set(pointers) != set(admission):
        raise ValueError("source rows and admission mapping must match exactly once")
    legacy_input = "input_sha256" in {
        col["name"] for col in conn.execute("PRAGMA table_info(source_observations)")
    }
    count = {status: 0 for status in sorted(_STATUSES)}
    conn.execute("SAVEPOINT source_observations_store")
    try:
        for row in rows:
            status, notice_id = _admission(admission[row["source_row"]])
            artifact = row["source_artifact"]
            parts = artifact.split("/")
            if len(parts) < 3 or parts[0] != "agency" or parts[1].lower() not in _ARTIFACT_STATES:
                raise ValueError(f"unexpected official source artifact: {artifact}")
            state = _ARTIFACT_STATES[parts[1].lower()]
            kind = row.get("kind", "notice")
            if (kind == "annotation") != (status == "annotation"):
                raise ValueError("annotation admission status mismatch")
            raw = _json(row)
            # Older databases used this status name; retain their CHECK constraint.
            stored_status = "event_review" if legacy_input and status == "event_unresolved" else status
            values = (row["source_row_sha256"], raw, _json(_deterministic(row)), stored_status, notice_id)
            key = (source_bundle_sha256, artifact, row["source_row"])
            saved = conn.execute("""SELECT source_row_sha256, raw_json, deterministic_json,
                    admission_status, notice_id FROM source_observations
                    WHERE source_bundle_sha256=? AND source_artifact=? AND source_row=?""", key).fetchone()
            if saved is not None:
                if tuple(saved) != values:
                    raise ValueError(f"stored source observation changed: {row['source_row']}")
            elif legacy_input:
                digest = hashlib.sha256(raw.encode()).hexdigest()
                conn.execute("""INSERT INTO source_observations
                    (source_bundle_sha256, state, observation_kind, source_artifact,
                     source_row, source_row_sha256, input_sha256, raw_json,
                     deterministic_json, admission_status, notice_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (source_bundle_sha256, state, kind, artifact, row["source_row"],
                     row["source_row_sha256"], digest, raw, values[2], stored_status, notice_id))
            else:
                conn.execute("""INSERT INTO source_observations
                    (source_bundle_sha256, state, observation_kind, source_artifact,
                     source_row, source_row_sha256, raw_json, deterministic_json,
                     admission_status, notice_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (source_bundle_sha256, state, kind, artifact, row["source_row"],
                     row["source_row_sha256"], raw, values[2], stored_status, notice_id))
            count[status] += 1
        conn.execute("RELEASE SAVEPOINT source_observations_store")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT source_observations_store")
        conn.execute("RELEASE SAVEPOINT source_observations_store")
        raise
    return {"rows": len(rows), "by_admission_status": count}
