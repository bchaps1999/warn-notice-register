"""Conservative source-record projection of pinned NY WARN dashboard rows."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from warnlive.migrate.ny_source import read_artifacts
from warnlive.normalize.engine import _record_hash


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _employer_group(name: str) -> str:
    """Collapse obvious display aliases before the singleton admission rule."""
    name = re.sub(r"\([^)]*\)", " ", name.casefold())
    name = re.sub(r"\b(?:incorporated|inc|llc|ltd|corp|corporation)\b", " ", name)
    return re.sub(r"[^a-z0-9]+", " ", name).strip()


def project(directory: Path) -> tuple[list[dict], list[dict], dict]:
    """Admit only employers appearing once in the frozen 2022-24 dashboard.

    The dashboard Index repeats and is never used as an identity. A singleton
    row is keyed as a source observation, not claimed to have a filing number.
    Repeated employers remain held because amendments and site fanout cannot
    be resolved from the dashboard CSV alone.
    """
    rows = read_artifacts(directory)
    counts = Counter(_employer_group(row["company"]) for row in rows)
    site_action_counts = Counter(
        (row["address"].casefold().strip(), row["effective_date"], row["posted_date"])
        for row in rows
    )
    records, held = [], []
    for row in rows:
        company = row["company"].strip()
        workers_text = row["workers"].replace(",", "").strip()
        site_action = (row["address"].casefold().strip(), row["effective_date"], row["posted_date"])
        complete = (counts[_employer_group(company)] == 1 and site_action_counts[site_action] == 1
                    and workers_text.isdigit()
                    and int(workers_text) > 0 and bool(row["address"])
                    and row["notice_date"] and row["effective_date"])
        if not complete:
            held.append({"origin": row["artifact"], "state": "NY",
                         "reason": ("repeated_employer_event_identity_unresolved"
                                    if counts[_employer_group(company)] > 1 else
                                    "same_site_action_posting_identity_unresolved"
                                    if site_action_counts[site_action] > 1 else
                                    "incomplete_dashboard_row"),
                         "source_row": row["source_row_id"],
                         "source_row_sha256": row["row_sha256"],
                         "source_url": row["source_url"],
                         "notice_year": row["notice_date"][:4],
                         "raw_extra": _json(row)})
            continue
        identity = f"NY:dashboard-observation:{row['row_sha256']}"
        details = {"origin": row["artifact"], "source_row": row["source_row_id"],
                   "source_row_sha256": row["row_sha256"],
                   "source_artifact_sha256": row["artifact_sha256"],
                   "identity_basis": "single_employer_source_observation_not_filing_id",
                   "agency_posted_date": row["posted_date"],
                   "date_roles": {"Date of WARN Notice": "reported_notice",
                                  "Date Posted": "agency_posting",
                                  "Date Layoff/Closure Starts": "reported_action"},
                   "dashboard_index_not_identity": row["index"],
                   "event_type_text": row["event_type"],
                   "permanence_text": row["permanence"],
                   "reason_text": row["reason"],
                   "county_text": row["county"],
                   "raw_cells": row["raw_cells"]}
        rec = {"state": "NY", "employer_name": company,
               "location": row["address"].strip(),
               "notice_date": row["notice_date"],
               "notice_date_precision": "day", "notice_date_basis": "reported",
               "effective_date": row["effective_date"],
               "effective_date_precision": "day", "effective_date_basis": "reported",
               "employees_affected": int(workers_text), "layoff_type": "unknown",
               "is_temporary": None, "is_amendment": 0,
               "source_url": row["source_url"],
               "source_notice_id": row["source_row_id"],
               "source_identity": identity, "source_details": _json(details),
               "raw_extra": _json(row["raw_cells"]),
               "dedupe_key": hashlib.sha1(identity.encode()).hexdigest()}
        rec["raw_record_hash"] = _record_hash(rec)
        records.append(rec)
    if len(records) + len(held) != len(rows):
        raise ValueError("New York dashboard source row accounting mismatch")
    return records, held, {"source_rows": len(rows), "admitted": len(records),
                           "held": len(held)}
