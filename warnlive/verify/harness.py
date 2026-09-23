"""Per-state verification: the concrete meaning of "independently verified".

Runs on every scrape (scheduled or manual). `fail` checks block ingest;
`warn` checks degrade the verdict but data still flows.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from warnlive.normalize.engine import NormalizeResult
from warnlive.registry import StateConfig

MIN_YEAR = 1988
MAX_FUTURE_DAYS = 30
EMPLOYER_COVERAGE_MIN = 0.95
PARSE_FAILURE_MAX = 0.10
DATE_SANITY_MAX_BAD = 0.02
DUP_KEY_RATE_MAX = 0.20


@dataclass
class Check:
    name: str
    outcome: str  # pass | warn | fail
    detail: str


@dataclass
class VerificationResult:
    state: str
    checks: list[Check] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        outcomes = {c.outcome for c in self.checks}
        if "fail" in outcomes:
            return "failed"
        if "warn" in outcomes:
            return "degraded"
        return "ok"

    def add(self, name: str, ok: bool, detail: str, severity: str = "fail") -> None:
        self.checks.append(Check(name, "pass" if ok else severity, detail))

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "checks": [
                {"name": c.name, "outcome": c.outcome, "detail": c.detail}
                for c in self.checks
            ],
        }


def verify_state(
    cfg: StateConfig,
    raw_path: Path | None,
    norm: NormalizeResult | None,
    fetch_error: str | None = None,
    today: date | None = None,
) -> VerificationResult:
    result = VerificationResult(state=cfg.postal.upper())
    today = today or date.today()

    # fetch_ok
    if fetch_error is not None:
        result.add("fetch_ok", False, fetch_error)
        return result
    if raw_path is None or not Path(raw_path).exists() or Path(raw_path).stat().st_size == 0:
        result.add("fetch_ok", False, f"raw file missing or empty: {raw_path}")
        return result
    result.add("fetch_ok", True, str(raw_path))

    if norm is None:
        result.add("normalize_ok", False, "normalization did not run")
        return result

    # row_count
    result.add(
        "row_count",
        norm.raw_rows >= cfg.min_rows,
        f"{norm.raw_rows} raw rows (min {cfg.min_rows})",
    )

    # schema_drift
    header = _read_header(raw_path)
    if cfg.expected_columns is None:
        result.add(
            "schema_drift",
            False,
            f"no expected_columns snapshot yet; observed: {header}",
            severity="warn",
        )
    else:
        result.add(
            "schema_drift",
            header == cfg.expected_columns,
            "header matches snapshot" if header == cfg.expected_columns else f"header drifted: {header}",
            severity="warn",
        )

    # map_coverage: parse failures + employer coverage
    result.add(
        "parse_failures",
        norm.failure_rate <= PARSE_FAILURE_MAX,
        f"{norm.failed_rows}/{norm.raw_rows} rows failed to normalize"
        + (f"; e.g. {norm.failure_examples[0]}" if norm.failure_examples else ""),
    )
    n = len(norm.records)
    if n:
        with_employer = sum(1 for r in norm.records if r["employer_name"])
        result.add(
            "employer_coverage",
            with_employer / n >= EMPLOYER_COVERAGE_MIN,
            f"{with_employer}/{n} records have an employer name",
        )
    else:
        result.add("employer_coverage", False, "no records normalized")

    # date_sanity
    dates = [_parse_iso(r["notice_date"]) for r in norm.records]
    dates = [d for d in dates if d is not None]
    if n and not dates:
        # Zero parseable notice dates would otherwise skip date_sanity AND
        # freshness, and a transformer that broke every date could still
        # verdict ok. Some sources (GA, PA) never publish a notice date and
        # carry only effective dates — those still have *a* date per record,
        # so the fail is reserved for a batch with no dates of either kind.
        effective = [
            d for d in (_parse_iso(r["effective_date"]) for r in norm.records)
            if d is not None
        ]
        result.add(
            "date_sanity",
            bool(effective),
            f"none of {n} records has a parseable notice_date"
            + ("" if effective else " or effective_date"),
            severity="fail" if not effective else "warn",
        )
    if dates:
        horizon = today + timedelta(days=MAX_FUTURE_DAYS)
        bad = sum(1 for d in dates if d.year < MIN_YEAR or d > horizon)
        result.add(
            "date_sanity",
            bad / len(dates) <= DATE_SANITY_MAX_BAD,
            f"{bad}/{len(dates)} notice_dates outside {MIN_YEAR}..{horizon}",
        )

        # freshness
        if cfg.staleness_days is not None:
            newest = max(dates)
            age = (today - newest).days
            result.add(
                "freshness",
                age <= cfg.staleness_days,
                f"newest notice_date {newest} is {age}d old (max {cfg.staleness_days}d)",
                severity="warn",
            )

    # dedupe_sanity
    if n:
        unique_keys = len({r["dedupe_key"] for r in norm.records})
        dup_rate = 1 - unique_keys / n
        result.add(
            "dedupe_sanity",
            dup_rate <= DUP_KEY_RATE_MAX,
            f"{n - unique_keys}/{n} records share a dedupe key",
            severity="warn",
        )

        if cfg.postal == "sc":
            # Correcting SC's date roles leaves historical notice_date null.
            # The old key then collapses independent layoffs at the same
            # employer/county. Do not ingest until event identity is migrated.
            by_key: dict[str, set[tuple]] = {}
            for rec in norm.records:
                by_key.setdefault(rec["dedupe_key"], set()).add(
                    (rec["effective_date"], rec["employees_affected"])
                )
            conflicts = sum(len(events) > 1 for events in by_key.values())
            result.add(
                "sc_event_identity", conflicts == 0,
                f"{conflicts} dedupe keys cover differing event dates/counts; "
                "source-aware reconciliation required",
            )
        if cfg.postal == "ga":
            by_key_ids: dict[str, set[str]] = {}
            for rec in norm.records:
                identity = rec.get("source_identity")
                if identity:
                    by_key_ids.setdefault(rec["dedupe_key"], set()).add(identity)
            conflicts = sum(len(ids) > 1 for ids in by_key_ids.values())
            result.add(
                "ga_filing_identity", conflicts == 0,
                f"{conflicts} dedupe keys cover different GA WARN IDs; "
                "source-aware reconciliation required",
            )
        if cfg.postal == "ks":
            missing = sum(not rec.get("source_identity") for rec in norm.records)
            by_key_rows: dict[str, set[str]] = {}
            for rec in norm.records:
                by_key_rows.setdefault(rec["dedupe_key"], set()).add(
                    rec["raw_record_hash"]
                )
            conflicts = sum(len(rows) > 1 for rows in by_key_rows.values())
            result.add(
                "ks_record_identity", missing == 0 and conflicts == 0,
                f"{missing} rows without a verified Kansas record ID; "
                f"{conflicts} record IDs cover differing rows",
            )
        if cfg.postal == "ia":
            # A filing key can contain distinct phases, sites, and revisions.
            # None of those may safely overwrite one another as versions of
            # a single current notice until source-row reconciliation exists.
            by_key_rows: dict[str, set[tuple]] = {}
            for rec in norm.records:
                try:
                    raw = json.loads(rec.get("raw_extra") or "{}")
                except (TypeError, ValueError):
                    raw = {}
                signature = (
                    (raw.get("Address Line 1") or "").strip().casefold(),
                    rec.get("effective_date"),
                    rec.get("employees_affected"),
                    rec.get("layoff_type"),
                    rec.get("is_amendment"),
                )
                by_key_rows.setdefault(rec["dedupe_key"], set()).add(signature)
            conflicts = sum(len(rows) > 1 for rows in by_key_rows.values())
            result.add(
                "ia_phase_identity", conflicts == 0,
                f"{conflicts} dedupe keys cover differing source rows; "
                "phase/amendment reconciliation required",
            )

    return result


def _read_header(path: Path) -> list[str]:
    with open(path, newline="") as fh:
        try:
            return next(csv.reader(fh))
        except StopIteration:
            return []


def _parse_iso(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None
