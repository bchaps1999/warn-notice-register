"""Persist and check the outcome of one scrape invocation."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from urllib.parse import quote

from warnlive.normalize.admission import ks_event_signature, load_ks_hold_policy
from warnlive.pipeline import RunReport


def write_run_report(report: RunReport, selected: list[str], path: Path) -> None:
    """Write the selected states and their outcomes after the run completes."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "trigger": report.trigger,
        "started_at": report.started_at,
        "selected": [state.upper() for state in selected],
        "outcomes": [asdict(outcome) for outcome in report.outcomes],
    }
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n")
    temporary.replace(path)


def check_run_report(path: Path) -> tuple[bool, str]:
    """Reject a missing, incomplete, or partially failed current run."""
    try:
        payload = json.loads(Path(path).read_text())
        selected = payload["selected"]
        outcomes = payload["outcomes"]
        if not isinstance(selected, list) or not selected:
            raise ValueError("selected states missing")
        if not isinstance(outcomes, list) or len(outcomes) != len(selected):
            raise ValueError("selected states and outcomes disagree")
        if [row["state"] for row in outcomes] != selected:
            raise ValueError("outcomes do not match selected states")
        if not isinstance(payload.get("started_at"), str) or not payload["started_at"]:
            raise ValueError("run timestamp missing")
        failed = [row["state"] for row in outcomes if row.get("verdict") == "failed"]
        unknown = [row["state"] for row in outcomes
                   if row.get("verdict") not in {"ok", "degraded", "failed"}]
        if unknown:
            return False, f"unverified state outcomes: {', '.join(unknown)}"
        if failed:
            return False, f"failed states: {', '.join(failed)}"
        return True, f"all {len(selected)} selected states completed without failure"
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return False, f"run report missing or invalid: {exc}"


def check_publication_gate(
    path: Path, db_path: Path | None = None,
    durable_policy_path: Path | None = None,
) -> tuple[bool, str]:
    """Stop publication when a source review invalidates an existing notice."""
    try:
        payload = json.loads(Path(path).read_text())
        selected, outcomes = payload["selected"], payload["outcomes"]
        if not selected or len(selected) != len(outcomes) or [
            row["state"] for row in outcomes
        ] != selected:
            raise ValueError("selected states and outcomes disagree")
        blocked = [row["state"] for row in outcomes
                   if (row.get("checks") or {}).get("admission", {}).get("publication_blocked")]
        if blocked:
            return False, f"publication blocked by unresolved source admission: {', '.join(blocked)}"
        if db_path is not None:
            return check_kansas_database_holds(db_path, durable_policy_path)
        return True, "no publication-blocking admission conflict"
    except (OSError, ValueError, TypeError, KeyError, AttributeError, sqlite3.Error) as exc:
        return False, f"publication gate could not validate inputs: {exc}"


def check_kansas_database_holds(
    db_path: Path, durable_policy_path: Path | None = None,
) -> tuple[bool, str]:
    """Check the published database independently of a scrape run report."""
    try:
        db_path = Path(db_path)
        if not db_path.is_file():
            raise ValueError(f"database missing: {db_path}")
        held_ids, held_signatures = load_ks_hold_policy()
        durable_path = (
            Path(durable_policy_path) if durable_policy_path is not None
            else db_path.parent / "ks_portal_holds.json"
        )
        if durable_path.exists():
            durable_ids, durable_signatures = load_ks_hold_policy(durable_path)
            held_ids |= durable_ids
            held_signatures |= durable_signatures
        uri = f"file:{quote(str(db_path.resolve()))}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            conflicts = sorted(
                row[0] or "(missing source ID)"
                for row in conn.execute(
                    "SELECT source_identity, employer_name, notice_date "
                    "FROM notices WHERE state = 'KS'"
                )
                if row[0] in held_ids
                or ks_event_signature(row[1], row[2]) in held_signatures
            )
        if conflicts:
            return False, (
                "publication blocked by Kansas held events in database: "
                + ", ".join(conflicts[:12])
            )
        return True, "no publication-blocking Kansas database conflict"
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        return False, f"Kansas database hold check could not validate inputs: {exc}"
