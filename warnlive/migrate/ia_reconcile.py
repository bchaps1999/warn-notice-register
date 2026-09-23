"""Evidence-only correspondence of frozen Iowa raw and BLN transcriptions.

Neither BLN's content hash nor a matching employer/date/worker signature is a
filing identity.  This report nominates source-row correspondences for review;
it never imports, merges, or corrects a notice.
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
from warnlive.migrate.ia_source import extract as extract_ia, extract_historical
from warnlive.migrate.source_bundle import extract as extract_bundle
from warnlive.normalize.engine import _fold, normalize_file
from warnlive.registry import load_registry


def _facts(rec: dict) -> tuple:
    return (_fold(rec.get("employer_name")), rec.get("notice_date"),
            rec.get("effective_date"), rec.get("employees_affected"))


def _dates_workers(rec: dict) -> tuple:
    return (rec.get("notice_date"), rec.get("effective_date"),
            rec.get("employees_affected"))


def _candidate(raw: dict, admitted: set[tuple[str, str]]) -> dict:
    return {
        "prepared_row": raw["prepared_row"],
        "source_row_sha256": raw["source_row_sha256"],
        "source_notice_id": raw["source_notice_id"],
        "dedupe_key": raw["dedupe_key"],
        "admitted_to_candidate": (
            raw["dedupe_key"], raw["source_notice_id"]
        ) in admitted,
        "employer_name": raw["employer_name"],
        "location": raw["location"],
        "notice_date": raw["notice_date"],
        "effective_date": raw["effective_date"],
        "employees_affected": raw["employees_affected"],
    }


def _match_basis(rec: dict, candidate: dict) -> str:
    """Explain each candidate, since a bucket can contain mixed-place hits."""
    if (rec.get("source_notice_id") and
            rec["source_notice_id"] == candidate.get("source_notice_id")):
        return "same_transcription_id"
    if _facts(rec) == _facts(candidate):
        return ("same_facts_and_place" if
                _fold(rec.get("location")) == _fold(candidate.get("location"))
                else "same_facts_place_disagrees")
    if _dates_workers(rec) == _dates_workers(candidate):
        return "same_dates_workers_only"
    raise ValueError("candidate lacks a correspondence basis")


def classify(
    rec: dict, by_id: dict, by_facts: dict, by_dates_workers: dict,
) -> tuple[str, list[dict]]:
    """Nominate counterparts; never infer whether two rows are one filing."""
    direct = by_id.get(rec.get("source_notice_id"), [])
    if direct:
        return "same_transcription_id", direct
    facts = by_facts.get(_facts(rec), [])
    if facts:
        if any(_fold(row["location"]) == _fold(rec.get("location")) for row in facts):
            return "same_facts_and_place", facts
        return "same_facts_place_disagrees", facts
    dates = by_dates_workers.get(_dates_workers(rec), [])
    if dates:
        return "same_dates_workers_only", dates
    return "no_raw_counterpart", []


def _source_correspondence(
    observations: list[dict], by_facts: dict, by_dates_workers: dict,
    bln_by_facts: dict, bln_by_dates_workers: dict,
    admitted: set[tuple[str, str]],
) -> dict:
    counts: Counter[str] = Counter()
    years: dict[str, Counter[str]] = defaultdict(Counter)
    rows = []
    for item in observations:
        rec = {
            "employer_name": item["company_text"],
            "location": item["city_text"],
            "notice_date": item["notice_date"],
            "effective_date": item["effective_date"],
            "employees_affected": item["workers_reported"],
        }
        raw_bucket, raw_hits = classify(rec, {}, by_facts, by_dates_workers)
        bln_bucket, bln_hits = classify(rec, {}, bln_by_facts, bln_by_dates_workers)
        year = item["notice_date"][:4] if item["notice_date"] else "invalid"
        counts["rows"] += 1
        counts[f"raw_{raw_bucket}"] += 1
        counts[f"bln_{bln_bucket}"] += 1
        years[year]["rows"] += 1
        years[year][f"raw_{raw_bucket}"] += 1
        years[year][f"bln_{bln_bucket}"] += 1
        rows.append({
            "source_row": item["source_row"],
            "source_row_sha256": item["source_row_sha256"],
            "official": item,
            "raw_bucket": raw_bucket,
            "raw_candidates": [{**_candidate(row, admitted),
                                "match_basis": _match_basis(rec, row)}
                               for row in raw_hits],
            "bln_bucket": bln_bucket,
            "bln_candidates": [{"match_basis": _match_basis(rec, row),
                                "bln_row": row["bln_row"],
                                "bln_row_sha256": row["bln_row_sha256"],
                                "bln_hash_id": row["bln_hash_id"],
                                "employer_name": row["employer_name"],
                                "location": row["location"],
                                "notice_date": row["notice_date"],
                                "effective_date": row["effective_date"],
                                "employees_affected": row["employees_affected"]}
                               for row in bln_hits],
        })
    return {"counts": dict(sorted(counts.items())),
            "years": {year: dict(sorted(values.items()))
                      for year, values in sorted(years.items())},
            "rows": rows}


def _official_overlap(current: list[dict], historical: list[dict]) -> dict:
    """Link repeated official observations; never collapse them as events."""
    def signature(row: dict) -> tuple:
        return (_fold(row["company_text"]), row["notice_date"],
                row["effective_date"], row["workers_reported"],
                _fold(row["city_text"]))

    index: dict[tuple, list[dict]] = defaultdict(list)
    for row in current:
        index[signature(row)].append(row)
    counts: Counter[str] = Counter()
    rows = []
    for row in historical:
        matches = index[signature(row)]
        bucket = ("no_current_counterpart" if not matches else
                  "one_current_counterpart" if len(matches) == 1 else
                  "multiple_current_counterparts")
        counts[bucket] += 1
        rows.append({
            "historical_source_row": row["source_row"],
            "historical_source_row_sha256": row["source_row_sha256"],
            "bucket": bucket,
            "current_candidates": [{"source_row": item["source_row"],
                                    "source_row_sha256": item["source_row_sha256"]}
                                   for item in matches],
        })
    return {"counts": dict(sorted(counts.items())), "rows": rows}


def _candidate_ia_snapshot(conn: sqlite3.Connection) -> tuple[set[tuple[str, str]], str]:
    """Bind correspondence flags to the exact Iowa candidate content reviewed."""
    columns = ("dedupe_key", "source_notice_id", "employer_name", "location",
               "notice_date", "effective_date", "effective_date_end",
               "employees_affected", "source_identity", "source_details",
               "site_address", "is_amendment")
    rows = conn.execute(
        f"SELECT {','.join(columns)} FROM notices WHERE state='IA' ORDER BY dedupe_key"
    ).fetchall()
    admitted = {(row[0], row[1]) for row in rows}
    fingerprint = hashlib.sha256(json.dumps(
        rows, ensure_ascii=False, separators=(",", ":")
    ).encode()).hexdigest()
    return admitted, fingerprint


def reconcile(bundle: Path, candidate_db: Path) -> dict:
    registry = load_registry()
    cfg = registry["ia"]
    with TemporaryDirectory(prefix="warn-ia-reconcile-") as temp:
        source = Path(temp) / "sources"
        extract_bundle(bundle, source)
        raw_path = source / "raw/ia.csv"
        bln_path = source / "backfill/bln_integrated.csv"
        norm = normalize_file("ia", source / "raw", cfg.source_url)
        conn = sqlite3.connect(f"file:{candidate_db.resolve()}?mode=ro", uri=True)
        try:
            admitted, candidate_fingerprint = _candidate_ia_snapshot(conn)
        finally:
            conn.close()
        raw_rows = []
        by_id: dict[str, list[dict]] = defaultdict(list)
        by_facts: dict[tuple, list[dict]] = defaultdict(list)
        by_dates_workers: dict[tuple, list[dict]] = defaultdict(list)
        for rec in norm.records:
            extra = rec.get("raw_extra") or ""
            item = {**rec, "source_row_sha256": hashlib.sha256(extra.encode()).hexdigest()}
            raw_rows.append(item)
            if item.get("source_notice_id"):
                by_id[item["source_notice_id"]].append(item)
            by_facts[_facts(item)].append(item)
            by_dates_workers[_dates_workers(item)].append(item)

        counts: Counter[str] = Counter()
        rows = []
        active_bln: list[dict] = []
        referenced_raw_rows: set[int] = set()
        with bln_path.open(newline="") as fh:
            for ordinal, original in enumerate(csv.DictReader(fh), start=1):
                if (original.get("postal_code") or "").upper() != "IA":
                    continue
                raw_text = json.dumps(original, sort_keys=True, ensure_ascii=False)
                base = {
                    "bln_row": ordinal,
                    "bln_row_sha256": hashlib.sha256(raw_text.encode()).hexdigest(),
                    "bln_hash_id": original.get("hash_id"),
                    "is_superseded": original.get("is_superseded") == "True",
                    "is_amendment": original.get("is_amendment") == "True",
                    "likely_ancestor": original.get("likely_ancestor") or None,
                    "raw_extra": raw_text,
                }
                if base["is_superseded"]:
                    base.update(review_bucket="superseded", raw_candidates=[])
                else:
                    try:
                        rec = to_canonical(original, cfg.source_url)
                    except (ValueError, TypeError, KeyError) as exc:
                        base.update(review_bucket="parse_failure", error=str(exc),
                                    raw_candidates=[])
                    else:
                        active_bln.append({**rec, "bln_row": ordinal,
                                           "bln_row_sha256": base["bln_row_sha256"],
                                           "bln_hash_id": base["bln_hash_id"]})
                        bucket, candidates = classify(rec, by_id, by_facts, by_dates_workers)
                        referenced_raw_rows.update(row["prepared_row"] for row in candidates)
                        base.update(
                            review_bucket=bucket,
                            normalized={field: rec.get(field) for field in (
                                "employer_name", "location", "notice_date",
                                "effective_date", "employees_affected", "dedupe_key",
                            )},
                            raw_candidates=[{**_candidate(row, admitted),
                                             "match_basis": _match_basis(rec, row)}
                                            for row in candidates],
                        )
                counts[base["review_bucket"]] += 1
                rows.append(base)
        if norm.raw_rows != len(raw_rows) + norm.failed_rows:
            raise ValueError("Iowa raw normalization does not account for every row")
        raw_without_bln = [
            _candidate(row, admitted) for row in raw_rows
            if row["prepared_row"] not in referenced_raw_rows
        ]
        result = {
            "format": "warn-ia-correspondence-v1",
            "decision_policy": "evidence_only_no_automatic_identity_or_ingest",
            "bucket_semantics": "strongest_matching_candidate_only; inspect each candidate match_basis",
            "inputs": {
                "bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
                "raw_ia_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
                "bln_sha256": hashlib.sha256(bln_path.read_bytes()).hexdigest(),
                "candidate_ia_fingerprint": candidate_fingerprint,
            },
            "raw": {"rows": norm.raw_rows, "normalized": len(raw_rows),
                    "parse_failures": norm.failed_rows,
                    "candidate_notices": len(admitted),
                    "rows_nominated_by_active_bln": len(referenced_raw_rows),
                    "rows_without_active_bln_candidate": len(raw_without_bln)},
            "bln": {"rows": len(rows), "review_buckets": dict(sorted(counts.items()))},
            "raw_rows_without_active_bln_candidate": raw_without_bln,
            "rows": rows,
        }
        official_dir = source / "agency/ia"
        if official_dir.is_dir():
            official_rows = extract_ia(official_dir)
            historical_rows = extract_historical(official_dir)
            bln_by_facts: dict[tuple, list[dict]] = defaultdict(list)
            bln_by_dates_workers: dict[tuple, list[dict]] = defaultdict(list)
            for rec in active_bln:
                bln_by_facts[_facts(rec)].append(rec)
                bln_by_dates_workers[_dates_workers(rec)].append(rec)
            indexes = (by_facts, by_dates_workers, bln_by_facts,
                       bln_by_dates_workers, admitted)
            result["official"] = {
                "source_xlsx_sha256": official_rows[0]["source_xlsx_sha256"],
                **_source_correspondence(official_rows, *indexes),
            }
            result["historical_official"] = {
                "source_pdf_sha256": historical_rows[0]["source_pdf_sha256"],
                **_source_correspondence(historical_rows, *indexes),
            }
            result["official_overlap"] = _official_overlap(official_rows, historical_rows)
        return result


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
    print(json.dumps({"raw": report["raw"], "bln": report["bln"],
                      "official": report.get("official", {}).get("counts"),
                      "historical_official": report.get("historical_official", {}).get("counts"),
                      "official_overlap": report.get("official_overlap", {}).get("counts")},
                     sort_keys=True))


if __name__ == "__main__":
    main()
