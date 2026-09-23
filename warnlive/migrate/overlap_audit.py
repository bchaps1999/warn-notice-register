"""Read-only field-level audit of historical raw rows sharing candidate keys.

Key equality means a source row was *considered* during replay, not that its
values survived.  This report keeps disagreements visible without choosing
which capture is right or changing the candidate database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory

from warnlive.migrate.source_bundle import extract as extract_bundle
from warnlive.normalize.engine import normalize_file
from warnlive.registry import load_registry

FIELDS = (
    "employer_name", "location", "notice_date", "effective_date",
    "effective_date_end", "employees_affected",
)


def differences(source: dict, candidate: dict) -> dict:
    """Only compare canonical facts, not raw-extra/version hashes."""
    return {field: {"source": source.get(field), "candidate": candidate.get(field)}
            for field in FIELDS if source.get(field) != candidate.get(field)}


def audit(bundle: Path, candidate_db: Path) -> tuple[dict, list[dict]]:
    """Verify the frozen bundle and compare every historical raw source row."""
    if not candidate_db.is_file():
        raise FileNotFoundError(candidate_db)
    registry = load_registry()
    conn = sqlite3.connect(f"file:{candidate_db.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    total = matched = unmatched = parse_failures = differing = 0
    field_counts: Counter[str] = Counter()
    state_counts: dict[str, Counter[str]] = defaultdict(Counter)
    ledger = []
    try:
        with TemporaryDirectory(prefix="warn-overlap-audit-") as temp:
            source = Path(temp) / "sources"
            extract_bundle(bundle, source)
            raw_dir = source / "backfill/raw"
            for path in sorted(raw_dir.glob("*.csv")):
                postal = path.stem
                if postal not in registry or postal in {"ga", "sc"}:
                    continue  # Their raw identity policy is handled separately.
                state = postal.upper()
                normalized = normalize_file(postal, raw_dir, registry[postal].source_url)
                total += normalized.raw_rows
                parse_failures += normalized.failed_rows
                state_counts[state]["raw_rows"] += normalized.raw_rows
                state_counts[state]["parse_failures"] += normalized.failed_rows
                source_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
                for rec in normalized.records:
                    raw_extra = rec.get("raw_extra") or ""
                    pointer = {
                        "origin": f"backfill/raw/{path.name}",
                        "source_file_sha256": source_sha256, "state": state,
                        "prepared_row": rec.get("prepared_row"),
                        "source_row_sha256": hashlib.sha256(raw_extra.encode()).hexdigest(),
                        "dedupe_key": rec["dedupe_key"], "raw_extra": raw_extra,
                    }
                    hit = conn.execute(
                        "SELECT employer_name,location,notice_date,effective_date,"
                        "effective_date_end,employees_affected FROM notices WHERE dedupe_key=?",
                        (rec["dedupe_key"],),
                    ).fetchone()
                    if hit is None:
                        unmatched += 1
                        state_counts[state]["unmatched_key"] += 1
                        ledger.append({**pointer, "reason": "unmatched_key"})
                        continue
                    matched += 1
                    state_counts[state]["matched_key"] += 1
                    delta = differences(rec, dict(hit))
                    if not delta:
                        continue
                    differing += 1
                    state_counts[state]["differing_rows"] += 1
                    for field in delta:
                        field_counts[field] += 1
                        state_counts[state][field] += 1
                    ledger.append({**pointer, "reason": "field_disagreement",
                                   "differences": delta})
    finally:
        conn.close()
    if total != matched + unmatched + parse_failures:
        raise ValueError("historical raw row accounting does not balance")
    ledger.sort(key=lambda r: (r["origin"], r["prepared_row"] or 0,
                               r["source_row_sha256"]))
    report = {
        "format": "warn-historical-overlap-audit-v1",
        "bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
        "candidate_notice_fingerprint": _notice_fingerprint(candidate_db),
        "scope": "backfill/raw CSVs except GA and SC; final canonical notice values",
        "interpretation": "field disagreement is review evidence, not an error or source preference",
        "raw_rows": total, "parse_failures": parse_failures,
        "matched_key_rows": matched, "unmatched_key_rows": unmatched,
        "differing_rows": differing, "ledger_rows": len(ledger),
        "field_counts": dict(sorted(field_counts.items())),
        "states": {state: dict(sorted(counts.items()))
                   for state, counts in sorted(state_counts.items())},
    }
    return report, ledger


def _notice_fingerprint(candidate_db: Path) -> str:
    # Uses the rebuild's stable-content fingerprint, not SQLite file bytes.
    from warnlive.migrate.offline_rebuild import _fingerprints

    conn = sqlite3.connect(f"file:{candidate_db.resolve()}?mode=ro", uri=True)
    try:
        return _fingerprints(conn)["notices"]
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    args = parser.parse_args()
    if args.report.resolve() == args.ledger.resolve():
        raise ValueError("report and ledger paths must differ")
    if args.report.exists() or args.ledger.exists():
        raise FileExistsError("audit outputs already exist")
    report, ledger = audit(args.bundle, args.db)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.ledger.parent.mkdir(parents=True, exist_ok=True)
    # The result is deterministic and never overwrites an earlier audit.
    with args.ledger.open("xb") as fh:
        for row in ledger:
            fh.write((json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n").encode())
    report["ledger"] = {"path": str(args.ledger), "rows": len(ledger),
                        "sha256": hashlib.sha256(args.ledger.read_bytes()).hexdigest()}
    with args.report.open("x") as fh:
        fh.write(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    print(json.dumps({key: report[key] for key in
                      ("matched_key_rows", "unmatched_key_rows", "differing_rows", "field_counts")},
                     sort_keys=True))


if __name__ == "__main__":
    main()
