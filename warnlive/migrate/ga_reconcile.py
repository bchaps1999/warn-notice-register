"""Read-only Georgia source-identity evidence for a frozen rebuild candidate.

An agency WARN ID identifies a filing, but does not decide whether different
rows are amendments or phases.  BLN fact matches are nominations, not IDs.
This module never admits, merges, or updates notices.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory

from warnlive.backfill.bln_integrated import to_canonical
from warnlive.migrate.source_bundle import extract as extract_bundle
from warnlive.normalize.engine import _fold, normalize_file
from warnlive.registry import load_registry


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _row_hash(row: dict) -> str:
    return _sha(json.dumps(row, sort_keys=True, ensure_ascii=False).encode())


def _partition(entries: list[dict], expected: int, name: str) -> dict[str, int]:
    counts = Counter(item["disposition"] for item in entries)
    if sum(counts.values()) != expected:
        raise ValueError(f"GA {name} rows not fully accounted")
    return dict(sorted(counts.items()))


def _facts(row: dict) -> tuple:
    return (_fold(row.get("employer_name")), row.get("effective_date"),
            row.get("employees_affected"))


def _name_date(row: dict) -> tuple:
    return _facts(row)[:2]


def classify_bln(
    rec: dict, by_facts: dict, by_name_date: dict,
) -> tuple[str, list[dict]]:
    """A strict fact match may still have a different site or filing identity."""
    facts = by_facts.get(_facts(rec), [])
    if facts:
        same_place = [item for item in facts if
                      _fold(item.get("location")) == _fold(rec.get("location"))]
        return ("same_facts_and_place" if same_place else "same_facts_place_disagrees",
                facts)
    weak = by_name_date.get(_name_date(rec), [])
    return ("same_name_date_workers_disagree", weak) if weak else ("no_counterpart", [])


def _bln_match_basis(rec: dict, candidate: dict) -> str:
    """Keep a mixed-location fact group honest at the candidate level."""
    if _facts(rec) == _facts(candidate):
        return ("same_facts_and_place" if
                _fold(rec.get("location")) == _fold(candidate.get("location"))
                else "same_facts_place_disagrees")
    if _name_date(rec) == _name_date(candidate):
        return "same_name_date_workers_disagree"
    raise ValueError("BLN candidate lacks a correspondence basis")


def _candidate_snapshot(conn: sqlite3.Connection) -> tuple[dict, dict, dict, str, int]:
    """Bind the report to candidate content, not unstable SQLite row IDs."""
    columns = ("dedupe_key", "source_identity", "source_notice_id", "employer_name",
               "location", "notice_date", "effective_date", "effective_date_end",
               "notice_date_precision", "notice_date_basis", "source_details",
               "employees_affected", "layoff_type", "is_temporary", "is_amendment",
               "site_address", "source_url", "is_amended", "current_version")
    query = (f"SELECT {','.join('n.' + col for col in columns)}, v.fields_json "
             "FROM notices n JOIN notice_versions v ON v.notice_id=n.id "
             "AND v.version=n.current_version WHERE n.state='GA' ORDER BY n.dedupe_key")
    by_identity: dict[str, list[dict]] = defaultdict(list)
    by_raw: dict[str, list[dict]] = defaultdict(list)
    by_key: dict[str, list[dict]] = defaultdict(list)
    fingerprint_rows = []
    for row in conn.execute(query):
        values = dict(zip(columns, row[:len(columns)]))
        try:
            fields = json.loads(row[-1])
            extra = fields.get("raw_extra")
            raw = json.loads(extra) if isinstance(extra, str) else extra
        except (ValueError, TypeError):
            raw = None
        raw_hash = _row_hash(raw) if isinstance(raw, dict) else None
        item = {**values, "raw_row_sha256": raw_hash,
                "current_version_fields_sha256": _sha(row[-1].encode())}
        fingerprint_rows.append(item)
        by_key[item["dedupe_key"]].append(item)
        if item["source_identity"]:
            by_identity[item["source_identity"]].append(item)
        if raw_hash:
            by_raw[raw_hash].append(item)
    return (by_identity, by_raw, by_key,
            _sha(json.dumps(fingerprint_rows, sort_keys=True,
                            ensure_ascii=False, separators=(",", ":")).encode()),
            len(fingerprint_rows))


def reconcile(bundle: Path, candidate_db: Path) -> dict:
    """Read verified frozen sources and an isolated read-only candidate DB."""
    bundle, candidate_db = Path(bundle), Path(candidate_db)
    cfg = load_registry()["ga"]
    with TemporaryDirectory(prefix="warn-ga-reconcile-") as temp:
        source = Path(temp) / "sources"
        extract_bundle(bundle, source)
        raw_path = source / "raw/ga.csv"
        historical_path = source / "cache/ga/ga_historical.csv"
        bln_path = source / "backfill/bln_integrated.csv"
        normalized = normalize_file("ga", source / "raw", cfg.source_url)
        conn = sqlite3.connect(f"file:{candidate_db.resolve()}?mode=ro", uri=True)
        try:
            by_identity, by_raw, by_key, candidate_hash, candidate_count = _candidate_snapshot(conn)
        finally:
            conn.close()

        with raw_path.open(newline="", encoding="utf-8-sig") as fh:
            raw_rows = list(csv.DictReader(fh))
        if len(raw_rows) != normalized.raw_rows:
            raise ValueError("GA prepared/raw row ordinals diverge")
        normalized_by_row = {rec["prepared_row"]: rec for rec in normalized.records}
        failure_by_row = {item["prepared_row"]: item for item in normalized.failures}
        historical_by_id: dict[str, list[dict]] = defaultdict(list)
        historical_entries = []
        with historical_path.open(newline="", encoding="utf-8-sig") as fh:
            historical_rows = list(csv.DictReader(fh))
        for ordinal, row in enumerate(historical_rows, 1):
            filing_id = (row.get("ID") or "").strip()
            item = {
                "source_row": ordinal, "source_row_sha256": _row_hash(row),
                "ga_warn_id": filing_id or None,
                "company_name": row.get("Company Name"),
                "separation_date": row.get("Separation Date"),
            }
            historical_entries.append(item)
            if filing_id:
                historical_by_id[filing_id].append(item)

        bln_by_facts: dict[tuple, list[dict]] = defaultdict(list)
        bln_by_name_date: dict[tuple, list[dict]] = defaultdict(list)
        bln_entries = []
        bln_source_rows = 0
        with bln_path.open(newline="", encoding="utf-8-sig") as fh:
            for ordinal, row in enumerate(csv.DictReader(fh), 1):
                if row.get("postal_code") != "GA":
                    continue
                bln_source_rows += 1
                base = {"source_row": ordinal, "source_row_sha256": _row_hash(row),
                        "hash_id": row.get("hash_id")}
                if row.get("is_superseded") == "True":
                    bln_entries.append({**base, "disposition": "superseded"})
                    continue
                try:
                    rec = to_canonical(row, cfg.source_url)
                except (ValueError, KeyError, TypeError) as exc:
                    bln_entries.append({**base, "disposition": "parse_failure",
                                        "error": str(exc)})
                    continue
                bln_entries.append({**base, "disposition": "active_parsed"})
                item = {
                    "source_row": ordinal, "source_row_sha256": base["source_row_sha256"],
                    "hash_id": row.get("hash_id"),
                    "employer_name": rec["employer_name"], "location": rec["location"],
                    "effective_date": rec["effective_date"],
                    "employees_affected": rec["employees_affected"],
                }
                bln_by_facts[_facts(item)].append(item)
                bln_by_name_date[_name_date(item)].append(item)

        counts: Counter[str] = Counter()
        raw_ids: dict[str, list[int]] = defaultdict(list)
        raw_keys: dict[str, list[int]] = defaultdict(list)
        bln_to_raw: dict[int, list[int]] = defaultdict(list)
        rows = []
        for ordinal, raw in enumerate(raw_rows, 1):
            filing_id = (raw.get("GA WARN ID") or "").strip()
            if filing_id:
                raw_ids[filing_id].append(ordinal)
            raw_hash = _row_hash(raw)
            rec = normalized_by_row.get(ordinal)
            if rec:
                raw_keys[rec["dedupe_key"]].append(ordinal)
            identity = f"GA:{filing_id}" if filing_id else None
            candidate_matches = by_identity.get(identity, []) if identity else by_raw.get(raw_hash, [])
            # A candidate with the right explicit ID but differing raw evidence
            # is a conflict, never an unquestioned admission.
            candidate_status = (
                "not_admitted" if not candidate_matches else
                "multiple_candidate_notices" if len(candidate_matches) > 1 else
                "identity_raw_disagrees" if candidate_matches[0]["raw_row_sha256"] != raw_hash else
                "exact_raw"
            )
            historical = historical_by_id.get(filing_id, []) if filing_id else []
            historical_conflict = bool(historical and all(
                _fold(item["company_name"]) != _fold(raw.get("Company Name"))
                for item in historical
            ))
            if rec:
                bln_bucket, bln_candidates = classify_bln(
                    rec, bln_by_facts, bln_by_name_date)
                bln_candidates = [{**candidate,
                                   "match_basis": _bln_match_basis(rec, candidate)}
                                  for candidate in bln_candidates]
            else:
                bln_bucket, bln_candidates = "raw_parse_failure", []
            counts[f"candidate_{candidate_status}"] += 1
            counts[f"bln_{bln_bucket}"] += 1
            if historical_conflict:
                counts["historical_employer_disagrees"] += 1
            if len(bln_candidates) > 1:
                counts["multiple_bln_candidates"] += 1
            for candidate in bln_candidates:
                bln_to_raw[candidate["source_row"]].append(ordinal)
            rows.append({
                "source_row": ordinal, "source_row_sha256": raw_hash,
                "ga_warn_id": filing_id or None, "source_identity": identity,
                "source_company_name": raw.get("Company Name"),
                "source_first_separation_date": raw.get("First Date of Separation"),
                "normalized": ({key: rec.get(key) for key in (
                    "employer_name", "location", "notice_date", "effective_date",
                    "effective_date_end", "employees_affected", "dedupe_key")}
                    if rec else None),
                "parse_error": failure_by_row.get(ordinal, {}).get("error"),
                "candidate_status": candidate_status,
                "candidate_notices": candidate_matches,
                "same_key_candidate_notices": by_key.get(rec["dedupe_key"], []) if rec else [],
                "historical_rows": historical,
                "historical_employer_disagrees": historical_conflict,
                "bln_bucket": bln_bucket, "bln_candidates": bln_candidates,
            })
        for row in rows:
            key = row["normalized"]["dedupe_key"] if row["normalized"] else None
            row["same_key_source_rows"] = raw_keys.get(key, []) if key else []
        for item in historical_entries:
            filing_id = item["ga_warn_id"]
            item["disposition"] = (
                "missing_id" if not filing_id else
                "id_in_raw" if filing_id in raw_ids else "id_not_in_raw"
            )
        for item in bln_entries:
            if item["disposition"] == "active_parsed":
                nominations = bln_to_raw.get(item["source_row"], [])
                item["disposition"] = (
                    "active_parsed_nominated" if nominations else
                    "active_parsed_unmatched"
                )
                item["raw_candidate_rows"] = nominations
        historical_dispositions = _partition(
            historical_entries, len(historical_rows), "historical",
        )
        bln_dispositions = _partition(bln_entries, bln_source_rows, "BLN")
        summary = {
            "raw_rows": len(rows), "normalized_rows": len(normalized.records),
            "raw_parse_failures": normalized.failed_rows,
            "explicit_id_rows": sum(bool(item["ga_warn_id"]) for item in rows),
            "idless_rows": sum(not item["ga_warn_id"] for item in rows),
            "duplicate_explicit_ids": sum(len(group) > 1 for group in raw_ids.values()),
            "historical_rows": len(historical_rows),
            "historical_dispositions": historical_dispositions,
            "historical_ids_without_raw": len(set(historical_by_id) - set(raw_ids)),
            "bln_rows": bln_source_rows,
            "bln_dispositions": bln_dispositions,
            "candidate_notices": candidate_count,
            "idless_unadmitted_conflicting_key_rows": sum(
                not row["ga_warn_id"] and row["candidate_status"] == "not_admitted"
                and len({rows[other - 1]["source_row_sha256"]
                         for other in row["same_key_source_rows"]}) > 1
                for row in rows),
            "distinct_unadmitted_idless_keys": len({
                row["normalized"]["dedupe_key"] for row in rows
                if not row["ga_warn_id"] and row["candidate_status"] == "not_admitted"
                and row["normalized"]
            }),
            "bln_rows_nominated_by_multiple_raw_rows": sum(
                len(source_rows) > 1 for source_rows in bln_to_raw.values()),
            "bln_rows_nominated_by_multiple_explicit_ids": sum(
                len({rows[ordinal - 1]["ga_warn_id"] for ordinal in source_rows
                     if rows[ordinal - 1]["ga_warn_id"]}) > 1
                for source_rows in bln_to_raw.values()),
            "historical_employer_disagrees": counts["historical_employer_disagrees"],
            **dict(sorted(counts.items())),
        }
        return {
            "format": "warn-ga-identity-evidence-v1",
            "decision_policy": "evidence_only_no_automatic_identity_or_ingest",
            "bucket_semantics": "strongest_matching_candidate_only; inspect each candidate match_basis",
            "inputs": {
                "bundle_sha256": _sha(bundle.read_bytes()),
                "raw_ga_sha256": _sha(raw_path.read_bytes()),
                "historical_ga_sha256": _sha(historical_path.read_bytes()),
                "bln_sha256": _sha(bln_path.read_bytes()),
                "candidate_ga_fingerprint": candidate_hash,
            },
            "summary": summary,
            "rows": rows,
            "historical_rows": historical_entries,
            "bln_rows": bln_entries,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    report = reconcile(args.bundle, args.db)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x") as fh:
        fh.write(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    print(json.dumps(report["summary"], sort_keys=True))


if __name__ == "__main__":
    main()
