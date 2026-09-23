"""Evidence-only comparison of official Louisiana tables with preserved BLN rows."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory

from warnlive.migrate.la_source import extract as extract_la
from warnlive.migrate.source_bundle import extract as extract_bundle


def reconcile(la_dir: Path, bln_csv: Path) -> dict:
    """Find exact date/worker candidates; never infer notice equivalence."""
    official = extract_la(la_dir)
    by_signature: dict[tuple[str, str], list[dict]] = defaultdict(list)
    with Path(bln_csv).open(newline="") as fh:
        for ordinal, row in enumerate(csv.DictReader(fh), start=1):
            if (row.get("postal_code") or "").upper() != "LA":
                continue
            key = (row.get("notice_date") or "", row.get("jobs") or "")
            raw = json.dumps(row, sort_keys=True, ensure_ascii=False)
            by_signature[key].append({
                "source_row": ordinal,  # DictReader data-row ordinal, not CSV line
                "source_row_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                "hash_id": row.get("hash_id"),
                "company": row.get("company"),
                "location": row.get("location"),
                "notice_date": row.get("notice_date"),
                "effective_date": row.get("effective_date"),
                "jobs": row.get("jobs"),
                "is_superseded": row.get("is_superseded") == "True",
                "is_amendment": row.get("is_amendment") == "True",
                "likely_ancestor": row.get("likely_ancestor") or None,
            })
    buckets = Counter()
    rows = []
    for row in official:
        item = {"official": row}
        if row["kind"] == "notice":
            candidates = by_signature[(row["notice_date"] or "", str(row["workers_total"]))]
            active = [candidate for candidate in candidates if not candidate["is_superseded"]]
            item["exact_date_worker_candidates"] = [
                {**candidate,
                 "effective_start_agrees": (
                     candidate["effective_date"] == row["effective_date_start"]
                 )}
                for candidate in candidates
            ]
            item["review_bucket"] = (
                "no_active_exact_signature" if not active
                else "one_active_exact_signature" if len(active) == 1
                else "multiple_active_exact_signatures"
            )
            buckets[item["review_bucket"]] += 1
        else:
            item["review_bucket"] = "source_annotation"
            buckets["source_annotation"] += 1
        rows.append(item)
    return {
        "format": "warn-la-reconciliation-v1",
        "inputs": {
            "la_manifest_sha256": hashlib.sha256(
                (Path(la_dir) / "manifest.json").read_bytes()
            ).hexdigest(),
            "bln_sha256": hashlib.sha256(Path(bln_csv).read_bytes()).hexdigest(),
        },
        "summary": dict(sorted(buckets.items())),
        "rows": rows,
        "decision_policy": "evidence_only_no_automatic_identity_or_ingest",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(f"reconciliation report already exists: {args.out}")
    with TemporaryDirectory(prefix="warn-la-reconcile-") as temp:
        source = Path(temp) / "sources"
        extract_bundle(args.bundle, source)
        report = reconcile(source / "agency/la", source / "backfill/bln_integrated.csv")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report["summary"], sort_keys=True))


if __name__ == "__main__":
    main()
