"""Read-only date quality gate for notice-to-effective timing analyses.

Run with ``python -m warnlive.verify.timing DB_PATH --out-dir DIR``. The input
database is opened read-only. The command writes a grouped quality report and
separate provisional and source-supported day cohorts; it never edits the database.
Null precision is treated as provisionally day-granular because it is the
current default for observed day dates. Eligibility is lexical, not verified
against a source document. Explicit month, year, and unknown precision are excluded.
Source-supported eligibility additionally requires explicit day precision and
a reviewed source basis for both date roles. Missing metadata stays in the
provisional output only.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import quote


SUMMARY_FIELDS = (
    "state", "source", "date_evidence_rule", "notices", "missing_notice_date",
    "missing_effective_start", "missing_interval_end", "end_before_start",
    "quarantined_end_before_start",
    "precision_day", "precision_month", "precision_year", "precision_unknown",
    "precision_null", "paired_dates", "negative_notice_to_start",
    "provisional_day_eligible", "eligible_negative_intervals",
    "source_supported_start_eligible", "source_supported_end_eligible",
    "start_precision_null", "end_precision_null",
)
ELIGIBLE_FIELDS = (
    "id", "dedupe_key", "state", "source", "date_evidence_rule", "employer_name", "location",
    "notice_date", "effective_date", "effective_date_end", "days_notice_to_start",
    "precision", "eligibility_basis", "date_review_flag",
)
SOURCE_SUPPORTED_FIELDS = ELIGIBLE_FIELDS + (
    "days_notice_to_end", "notice_date_basis", "effective_date_precision", "effective_date_basis",
    "effective_date_end_precision", "effective_date_end_basis",
    "notice_date_role", "effective_date_role", "employee_separation_phases", "negative_interval",
)
ELIGIBILITY_FIELDS = (
    "id", "dedupe_key", "state", "source", "date_evidence_rule", "notice_date", "effective_date",
    "effective_date_end", "notice_precision", "notice_basis",
    "start_precision", "start_basis", "end_precision", "end_basis",
    "notice_date_role", "effective_date_role", "employee_separation_phases",
    "notice_date_status", "effective_start_date_status", "effective_end_date_status",
    "start_status", "end_status", "days_notice_to_start", "days_notice_to_end",
)
SUPPORTED_BASES = frozenset({"reported", "derived_from_reported_components"})


def _supported_day(value: str | None, precision: str | None, basis: str | None) -> str:
    if not value:
        return "date_missing"
    if precision is None:
        return "precision_unassessed"
    if precision != "day":
        return "date_not_day"
    if basis not in SUPPORTED_BASES:
        return "basis_unassessed" if basis is None else "basis_not_source_supported"
    parsed = _parse_day(value)
    if parsed is None or parsed.isoformat() != value:
        return "invalid_day"
    return "supported"


def has_unresolved_date_review(details: dict) -> bool:
    """A known unresolved date/range issue blocks strict timing eligibility."""
    for key in ("notice_date_status", "effective_date_status", "effective_date_end_status",
                "date_role_status"):
        value = details.get(key)
        if isinstance(value, str) and any(word in value for word in
                                          ("review", "conflict", "unresolved", "invalid", "quarantine")):
            return True
    return False


def _parse_day(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _date_details(source_details: str | None) -> dict:
    if not source_details:
        return {}
    try:
        details = json.loads(source_details)
    except (TypeError, ValueError):
        return {}
    return details if isinstance(details, dict) else {}


def audit(db_path: Path, out_dir: Path) -> tuple[int, int]:
    """Write the state/source metrics and provisional cohort; return row counts."""
    out_dir.mkdir(parents=True, exist_ok=True)
    uri_path = quote(db_path.resolve().as_posix(), safe="/:")
    uri = f"file:{uri_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(notices)")}
        required = {"id", "dedupe_key", "state", "notice_date", "effective_date"}
        missing = required - cols
        if missing:
            raise ValueError(f"notices table is missing required columns: {sorted(missing)}")
        select = [
            "id", "dedupe_key", "state", "employer_name", "location",
            "notice_date", "effective_date",
            "NULL AS effective_date_end" if "effective_date_end" not in cols else "effective_date_end",
            "NULL AS notice_date_precision" if "notice_date_precision" not in cols else "notice_date_precision",
            "NULL AS notice_date_basis" if "notice_date_basis" not in cols else "notice_date_basis",
            "NULL AS effective_date_precision" if "effective_date_precision" not in cols else "effective_date_precision",
            "NULL AS effective_date_basis" if "effective_date_basis" not in cols else "effective_date_basis",
            "NULL AS effective_date_end_precision" if "effective_date_end_precision" not in cols else "effective_date_end_precision",
            "NULL AS effective_date_end_basis" if "effective_date_end_basis" not in cols else "effective_date_end_basis",
            "NULL AS source_url" if "source_url" not in cols else "source_url",
            "NULL AS source_details" if "source_details" not in cols else "source_details",
        ]
        rows = conn.execute(
            "SELECT " + ",".join(select) + " FROM notices ORDER BY state, source_url, id"
        )
        buckets: dict[tuple[str, str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
        eligible: list[dict[str, object]] = []
        supported_start: list[dict[str, object]] = []
        supported_end: list[dict[str, object]] = []
        eligibility: list[dict[str, object]] = []
        for row in rows:
            state = row["state"] or ""
            source = row["source_url"] or "unknown"
            details = _date_details(row["source_details"])
            reviewed_repair = details.get("ny_reviewed_repair")
            reviewed_repair = reviewed_repair if isinstance(reviewed_repair, dict) else {}
            tx_workbook = details.get("tx_annual_workbook")
            tx_workbook = tx_workbook if isinstance(tx_workbook, dict) else {}
            notice_date_role = details.get("notice_date_role") or tx_workbook.get("notice_date_role")
            if not isinstance(notice_date_role, str):
                notice_date_role = ""
            effective_date_role = (details.get("effective_date_role") or
                                   reviewed_repair.get("effective_date_role") or
                                   tx_workbook.get("effective_date_role"))
            if not isinstance(effective_date_role, str):
                effective_date_role = ""
            phases = details.get("employee_separation_phases")
            if phases is None:
                phases = reviewed_repair.get("employee_separation_phases")
            phases_json = json.dumps(phases, sort_keys=True, separators=(",", ":")) if phases is not None else ""
            rule = details.get("date_evidence_rule")
            if not isinstance(rule, str) or not rule:
                rule = "ny_reviewed_filing" if "ny_reviewed_repair" in details else "unassessed"
            metrics = buckets[(state, source, rule)]
            metrics["notices"] += 1
            notice = _parse_day(row["notice_date"])
            start = _parse_day(row["effective_date"])
            end = _parse_day(row["effective_date_end"])
            raw_precision = row["notice_date_precision"]
            precision = raw_precision
            interval = details.get("effective_date_interpretation") == "interval"
            review_flag = details.get("effective_date_end_status")
            if row["effective_date_precision"] is None:
                metrics["start_precision_null"] += 1
            if row["effective_date_end_precision"] is None:
                metrics["end_precision_null"] += 1
            if not row["notice_date"]:
                metrics["missing_notice_date"] += 1
            if not row["effective_date"]:
                metrics["missing_effective_start"] += 1
            if interval and not row["effective_date_end"]:
                metrics["missing_interval_end"] += 1
            if precision == "day":
                metrics["precision_day"] += 1
            elif precision == "month":
                metrics["precision_month"] += 1
            elif precision == "year":
                metrics["precision_year"] += 1
            elif precision == "unknown":
                metrics["precision_unknown"] += 1
            else:
                metrics["precision_null"] += 1
            if start and end and end < start:
                metrics["end_before_start"] += 1
            if review_flag == "before_start_review":
                metrics["quarantined_end_before_start"] += 1
            if notice and start:
                metrics["paired_dates"] += 1
                elapsed = (start - notice).days
                if elapsed < 0:
                    metrics["negative_notice_to_start"] += 1
                # Null is the current schema default for observed dates. Include
                # it provisionally, while explicit month/unknown tags remain
                # excluded. This is lexical eligibility, not source verification.
                if precision in {"day", None}:
                    metrics["provisional_day_eligible"] += 1
                    if elapsed < 0:
                        metrics["eligible_negative_intervals"] += 1
                    eligible.append({
                        "id": row["id"], "dedupe_key": row["dedupe_key"],
                        "state": state, "source": source, "date_evidence_rule": rule,
                        "employer_name": row["employer_name"], "location": row["location"],
                        "notice_date": row["notice_date"],
                        "effective_date": row["effective_date"],
                        "effective_date_end": row["effective_date_end"],
                        "days_notice_to_start": elapsed,
                        "precision": precision or "null",
                        "eligibility_basis": (
                            "explicit_day_precision" if precision == "day"
                            else "provisional_null_precision"
                        ),
                        "date_review_flag": review_flag or "",
                    })

            notice_status = _supported_day(
                row["notice_date"], row["notice_date_precision"], row["notice_date_basis"]
            )
            start_status = _supported_day(
                row["effective_date"], row["effective_date_precision"], row["effective_date_basis"]
            )
            end_status = _supported_day(
                row["effective_date_end"], row["effective_date_end_precision"], row["effective_date_end_basis"]
            )
            raw_start_status, raw_end_status = start_status, end_status
            if notice_status != "supported":
                start_status = f"notice_{notice_status}"
                end_status = f"notice_{notice_status}"
            elif has_unresolved_date_review(details) or (start and end and end < start):
                start_status = "date_review_hold"
                end_status = "date_review_hold"
            start_days = (start - notice).days if notice and start else None
            end_days = (end - notice).days if notice and end else None
            eligibility.append({
                "id": row["id"], "dedupe_key": row["dedupe_key"], "state": state,
                "source": source, "date_evidence_rule": rule,
                "notice_date": row["notice_date"] or "",
                "effective_date": row["effective_date"] or "",
                "effective_date_end": row["effective_date_end"] or "",
                "notice_precision": row["notice_date_precision"] or "",
                "notice_basis": row["notice_date_basis"] or "",
                "start_precision": row["effective_date_precision"] or "",
                "start_basis": row["effective_date_basis"] or "",
                "end_precision": row["effective_date_end_precision"] or "",
                "end_basis": row["effective_date_end_basis"] or "",
                "notice_date_role": notice_date_role,
                "effective_date_role": effective_date_role,
                "employee_separation_phases": phases_json,
                "notice_date_status": notice_status,
                "effective_start_date_status": raw_start_status,
                "effective_end_date_status": raw_end_status,
                "start_status": start_status, "end_status": end_status,
                "days_notice_to_start": start_days if start_status == "supported" else "",
                "days_notice_to_end": end_days if end_status == "supported" else "",
            })
            common = {
                "id": row["id"], "dedupe_key": row["dedupe_key"], "state": state,
                "source": source, "date_evidence_rule": rule,
                "employer_name": row["employer_name"],
                "location": row["location"], "notice_date": row["notice_date"],
                "effective_date": row["effective_date"],
                "effective_date_end": row["effective_date_end"],
                "precision": row["notice_date_precision"],
                "eligibility_basis": "source_supported_day",
                "date_review_flag": review_flag or "",
                "notice_date_basis": row["notice_date_basis"],
                "effective_date_precision": row["effective_date_precision"],
                "effective_date_basis": row["effective_date_basis"],
                "effective_date_end_precision": row["effective_date_end_precision"],
                "effective_date_end_basis": row["effective_date_end_basis"],
                "notice_date_role": notice_date_role,
                "effective_date_role": effective_date_role,
                "employee_separation_phases": phases_json,
            }
            if start_status == "supported" and start_days is not None:
                metrics["source_supported_start_eligible"] += 1
                supported_start.append({**common, "days_notice_to_start": start_days,
                                        "days_notice_to_end": "",
                                        "negative_interval": int(start_days < 0)})
            if end_status == "supported" and end_days is not None:
                metrics["source_supported_end_eligible"] += 1
                supported_end.append({**common, "days_notice_to_start": "",
                                      "days_notice_to_end": end_days,
                                      "negative_interval": int(end_days < 0)})

        with (out_dir / "date_quality_by_state_source.csv").open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=SUMMARY_FIELDS)
            writer.writeheader()
            for (state, source, rule), counts in sorted(buckets.items()):
                writer.writerow({"state": state, "source": source,
                                 "date_evidence_rule": rule,
                                 **{field: counts.get(field, 0) for field in SUMMARY_FIELDS[3:]}})
        with (out_dir / "provisional_day_timing_cohort.csv").open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=ELIGIBLE_FIELDS)
            writer.writeheader()
            writer.writerows(eligible)
        for name, values in (("source_supported_start_timing_cohort.csv", supported_start),
                             ("source_supported_end_timing_cohort.csv", supported_end)):
            with (out_dir / name).open("w", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=SOURCE_SUPPORTED_FIELDS)
                writer.writeheader()
                writer.writerows(values)
        with (out_dir / "date_eligibility_by_notice.csv").open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=ELIGIBILITY_FIELDS)
            writer.writeheader()
            writer.writerows(eligibility)
        return sum(x["notices"] for x in buckets.values()), len(eligible)
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db", type=Path, help="SQLite database opened read-only")
    parser.add_argument("--out-dir", type=Path, required=True,
                        help="directory for CSV report and exact-day cohort")
    args = parser.parse_args()
    notices, eligible = audit(args.db, args.out_dir)
    print(f"Audited {notices:,} notices; {eligible:,} provisionally day-granular paired rows.")
    print(f"Wrote {args.out_dir / 'date_quality_by_state_source.csv'}")
    print(f"Wrote {args.out_dir / 'provisional_day_timing_cohort.csv'}")
    print(f"Wrote {args.out_dir / 'source_supported_start_timing_cohort.csv'}")
    print(f"Wrote {args.out_dir / 'source_supported_end_timing_cohort.csv'}")
    print(f"Wrote {args.out_dir / 'date_eligibility_by_notice.csv'}")


if __name__ == "__main__":
    main()
