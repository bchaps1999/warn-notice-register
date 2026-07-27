"""Whole-database checks, run after ingest and before anything is published.

Per-state verification (see harness.py) inspects one scrape against its own
source and stops a bad fetch from entering the database. It cannot see the
class of bug that has actually reached this project's published data twice:
a parser that ingests the right *number* of rows with the wrong values in
them. New York's archived pages once yielded a worker total of 1.7
quadrillion, and Wisconsin's logs stored a date serial where the location
belonged — both passed every per-state check, were committed, and were
found by eye on the site.

So this compares the database against a snapshot of itself from the last
published run. It answers one question: did anything move in a way real
layoff filings do not move? Notices are only ever added, a state's history
does not shrink, and no single WARN notice covers a hundred thousand
workers (the largest on record here is 27,500).

Thresholds are judgement calls, not laws. One that fires on legitimate data
should be widened, and the run that provoked it recorded in the comment —
a gate nobody trusts gets disabled, which is worse than no gate.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from warnlive.verify.harness import VerificationResult

SNAPSHOT_PATH = Path("data/health/snapshot.json")

# A state's notice count may dip a little — sources withdraw filings, and a
# re-keyed row can land under a new identity — but not by much.
STATE_SHRINK_MAX = 0.02
# Below this, a state is small enough that ordinary churn swamps any ratio.
SHRINK_FLOOR = 50
# No genuine notice is this large; anything above is a parse artifact.
MAX_NOTICE_WORKERS = 100_000
# A state's payroll of affected workers cannot multiply in a week.
WORKER_GROWTH_MAX = 5.0
WORKER_GROWTH_FLOOR = 1_000
# Field completeness shifting this far means the source or parser changed.
# Past the second threshold a field has essentially emptied or filled, which
# is a break rather than drift — Wisconsin's locations went to 100% null on a
# column-mapping bug and nothing stopped it being published.
NULL_RATE_SHIFT_MAX = 0.20
NULL_RATE_BREAK = 0.50
# Duplicate links should grow roughly with ingest, not independently of it.
DUP_GROWTH_RATE_MAX = 0.01
DUP_GROWTH_ABSOLUTE_MAX = 25

_METRIC_SQL = """
SELECT state,
       COUNT(*)                                        AS notices,
       COALESCE(SUM(employees_affected), 0)            AS workers,
       COALESCE(MAX(employees_affected), 0)            AS max_workers,
       SUM(notice_date IS NULL)                        AS undated,
       SUM(employees_affected IS NULL)                 AS no_jobs,
       SUM(location IS NULL OR location = '')          AS no_location,
       COUNT(DISTINCT employer_name)                   AS employers,
       MIN(COALESCE(notice_date, effective_date))      AS first,
       MAX(COALESCE(notice_date, effective_date))      AS last
FROM notices GROUP BY state
"""


def build_snapshot(conn: sqlite3.Connection) -> dict:
    """Per-state and national metrics describing the database as it stands."""
    states = {}
    for row in conn.execute(_METRIC_SQL):
        states[row["state"]] = {k: row[k] for k in row.keys() if k != "state"}
    links = conn.execute(
        "SELECT COUNT(*) AS c FROM notice_links WHERE kind = 'possible_duplicate'"
    ).fetchone()["c"]
    return {
        "notices": sum(s["notices"] for s in states.values()),
        "workers": sum(s["workers"] for s in states.values()),
        "possible_duplicates": links,
        "states": states,
    }


def _rate(part: int, whole: int) -> float:
    return part / whole if whole else 0.0


def check_regressions(conn: sqlite3.Connection, previous: dict | None) -> VerificationResult:
    """Compare the database against the last published snapshot."""
    result = VerificationResult(state="ALL")
    current = build_snapshot(conn)

    # Ceiling checks stand on their own — they need no history, and they are
    # the ones that catch a parser inventing numbers.
    worst = max(
        ((s["max_workers"], postal) for postal, s in current["states"].items()),
        default=(0, "—"),
    )
    result.add(
        "notice_size_ceiling",
        worst[0] <= MAX_NOTICE_WORKERS,
        f"largest single notice reports {worst[0]:,} workers ({worst[1]}); "
        f"ceiling {MAX_NOTICE_WORKERS:,}",
    )

    if previous is None:
        result.add(
            "snapshot_present", False,
            "no previous snapshot to compare against; only the ceiling checks ran",
            severity="warn",
        )
        return result

    result.add(
        "total_notices",
        current["notices"] >= previous["notices"],
        f"{previous['notices']:,} -> {current['notices']:,}",
    )

    shrank, grew, drifted, broke = [], [], [], []
    for postal, now in sorted(current["states"].items()):
        before = previous["states"].get(postal)
        if before is None:
            continue  # a newly collected state has nothing to regress against
        if (
            before["notices"] >= SHRINK_FLOOR
            and now["notices"] < before["notices"] * (1 - STATE_SHRINK_MAX)
        ):
            shrank.append(f"{postal} {before['notices']:,}->{now['notices']:,}")
        if (
            before["workers"] >= WORKER_GROWTH_FLOOR
            and now["workers"] > before["workers"] * WORKER_GROWTH_MAX
        ):
            grew.append(f"{postal} {before['workers']:,}->{now['workers']:,}")
        for field in ("undated", "no_jobs", "no_location"):
            shift = abs(
                _rate(now[field], now["notices"]) - _rate(before[field], before["notices"])
            )
            if shift > NULL_RATE_BREAK:
                broke.append(f"{postal} {field} {shift:.0%}")
            elif shift > NULL_RATE_SHIFT_MAX:
                drifted.append(f"{postal} {field} {shift:.0%}")

    result.add(
        "state_notice_counts", not shrank,
        f"shrank beyond {STATE_SHRINK_MAX:.0%}: {', '.join(shrank)}" if shrank
        else f"no state lost more than {STATE_SHRINK_MAX:.0%} of its notices",
    )
    result.add(
        "state_worker_totals", not grew,
        f"grew more than {WORKER_GROWTH_MAX:g}x: {', '.join(grew)}" if grew
        else f"no state's worker total grew more than {WORKER_GROWTH_MAX:g}x",
    )
    result.add(
        "field_emptied", not broke,
        f"a field emptied or filled: {', '.join(broke)}" if broke
        else f"no field's null rate moved more than {NULL_RATE_BREAK:.0%}",
    )
    result.add(
        "field_completeness", not drifted,
        f"null rates moved: {', '.join(drifted)}" if drifted
        else f"no field's null rate moved more than {NULL_RATE_SHIFT_MAX:.0%}",
        severity="warn",
    )

    added = max(current["notices"] - previous["notices"], 0)
    dup_growth = current["possible_duplicates"] - previous["possible_duplicates"]
    allowed = max(added * DUP_GROWTH_RATE_MAX, DUP_GROWTH_ABSOLUTE_MAX)
    result.add(
        "duplicate_links", dup_growth <= allowed,
        f"possible duplicates +{dup_growth} on {added:,} new notices "
        f"(allowed {allowed:.0f})",
        severity="warn",
    )
    return result


def load_snapshot(path: Path = SNAPSHOT_PATH) -> dict | None:
    if not path.exists():
        return None
    with open(path) as fh:
        return json.load(fh)


def write_snapshot(snapshot: dict, path: Path = SNAPSHOT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(snapshot, fh, indent=1, sort_keys=True)
        fh.write("\n")
