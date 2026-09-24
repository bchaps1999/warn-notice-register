"""Scheduled scrape reports must expose partial state failures."""

import json

from click.testing import CliRunner

from warnlive.cli import cli
from warnlive.pipeline import RunReport, StateOutcome
from warnlive.store import db
from warnlive.verify.run_report import (
    check_kansas_database_holds, check_publication_gate, check_run_report,
    write_run_report,
)


def _report(second_verdict="ok"):
    return RunReport(
        trigger="scheduled", started_at="2026-09-24T12:00:00Z",
        outcomes=[StateOutcome(state="CT", verdict="ok", new=1),
                  StateOutcome(state="MA", verdict=second_verdict,
                               error="fetch failed" if second_verdict == "failed" else None)],
    )


def test_partial_failure_is_recorded_and_fails_gate(tmp_path):
    path = tmp_path / "current-run.json"
    write_run_report(_report("failed"), ["ct", "ma"], path)
    payload = json.loads(path.read_text())
    assert payload["outcomes"][0]["new"] == 1
    assert payload["outcomes"][1]["error"] == "fetch failed"
    result = CliRunner().invoke(cli, ["check-run-report", str(path)])
    assert result.exit_code == 1
    assert "MA" in result.output


def test_complete_run_passes_and_missing_or_mismatched_report_fails(tmp_path):
    path = tmp_path / "current-run.json"
    assert check_run_report(path)[0] is False
    write_run_report(_report(), ["ct", "ma"], path)
    assert CliRunner().invoke(cli, ["check-run-report", str(path)]).exit_code == 0
    payload = json.loads(path.read_text())
    payload["selected"] = ["CT", "NY"]
    path.write_text(json.dumps(payload))
    assert check_run_report(path)[0] is False


def test_publication_gate_blocks_current_source_conflict_only(tmp_path):
    path = tmp_path / "current-run.json"
    report = _report("failed")
    report.outcomes[1].checks = {"admission": {
        "publication_blocked": True,
        "reason": "current_held_event_already_published",
    }}
    write_run_report(report, ["ct", "ma"], path)
    assert check_publication_gate(path)[0] is False
    assert CliRunner().invoke(cli, ["check-publication-gate", str(path)]).exit_code == 1
    report.outcomes[1].checks = None
    write_run_report(report, ["ct", "ma"], path)
    assert check_publication_gate(path)[0] is True


def test_daily_publication_gate_checks_accumulated_kansas_holds(tmp_path):
    path = tmp_path / "current-run.json"
    write_run_report(
        RunReport(trigger="scheduled", started_at="2026-09-24T12:00:00Z",
                  outcomes=[StateOutcome(state="CT", verdict="ok")]),
        ["ct"], path,
    )
    data_dir = tmp_path / "custom-data"
    db_path = data_dir / "warn.sqlite"
    conn = db.connect(db_path)
    db.init_db(conn)
    conn.execute(
        "INSERT INTO notices (dedupe_key, state, employer_name, notice_date, "
        "source_identity, first_seen) VALUES (?, ?, ?, ?, ?, ?)",
        ("ks-older", "KS", "Acme", "2026-01-01", "KS:999001", "2026-01-01"),
    )
    conn.commit()
    conn.close()
    policy = data_dir / "ks_portal_holds.json"
    policy.write_text(json.dumps({
        "source": "kansasworks_warn_portal", "held_ids": ["KS:999001"],
        "held_signatures": [["acme", "2026-01-01"]],
    }))
    ok, detail = check_publication_gate(path, db_path)
    assert ok is False
    assert "KS:999001" in detail
    result = CliRunner().invoke(cli, [
        "check-publication-gate", str(path), "--db", str(db_path),
        "--data-dir", str(data_dir),
    ])
    assert result.exit_code == 1
    assert "KS:999001" in result.output
    assert check_kansas_database_holds(db_path)[0] is False
    standalone = CliRunner().invoke(cli, [
        "check-kansas-holds", "--db", str(db_path), "--data-dir", str(data_dir),
    ])
    assert standalone.exit_code == 1
    assert "KS:999001" in standalone.output
    conn = db.connect(db_path)
    conn.execute("DELETE FROM notices WHERE state='KS'")
    conn.commit()
    conn.close()
    assert check_publication_gate(path, db_path)[0] is True
    assert check_kansas_database_holds(db_path)[0] is True
