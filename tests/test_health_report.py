from dataclasses import replace
from datetime import date
import json

from click.testing import CliRunner

from warnlive import cli as cli_module
from warnlive.registry import Registry, load_registry
from warnlive.store import db
from warnlive.verify.report import build_status, write_health


def test_active_collector_success_age_is_distinct_from_source_freshness(tmp_path):
    conn = db.connect(tmp_path / "health.sqlite")
    db.init_db(conn)
    daily = replace(load_registry()["ct"], cadence="daily")
    quiet = replace(load_registry()["pa"], cadence="weekly")
    archive = replace(load_registry()["ga"], cadence=None)
    registry = Registry({c.postal: c for c in (daily, quiet, archive)})
    conn.execute("INSERT INTO runs (started_at, trigger) VALUES ('2026-09-01', 'test')")
    conn.execute(
        "INSERT INTO state_runs (run_id, state, verdict, finished_at) "
        "VALUES (1, 'CT', 'ok', '2026-09-18T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO state_runs (run_id, state, verdict, finished_at) "
        "VALUES (1, 'GA', 'ok', '2026-01-01T00:00:00Z')"
    )
    conn.commit()

    status = build_status(conn, registry, today=date(2026, 9, 24))
    assert status["CT"]["last_success_age_days"] == 6
    assert status["CT"]["last_success_overdue"] is True
    assert status["PA"]["last_success_age_days"] is None
    assert status["PA"]["last_success_overdue"] is True
    assert status["GA"]["last_success_overdue"] is False
    assert status["CT"]["recommend_broken"] is False

    # The Markdown labels the collector clock, not a lack of new notices.
    write_health(conn, registry, tmp_path / "health", today=date(2026, 9, 24))
    report = (tmp_path / "health" / "health.md").read_text()
    assert "last successful collection overdue" in report
    assert "no successful collection recorded" in report


def test_overdue_collection_issue_opens_and_closes_with_stale_ok_verdict(tmp_path, monkeypatch):
    db_path = tmp_path / "health.sqlite"
    conn = db.connect(db_path)
    db.init_db(conn)
    conn.close()
    state = {
        "name": "Connecticut", "registry_status": "active",
        "latest_verdict": "ok", "consecutive_failures": 0,
        "chronically_degraded": False, "last_success_overdue": True,
        "last_success": "2026-09-18T00:00:00Z", "last_success_max_age_days": 3,
    }
    monkeypatch.setattr(cli_module, "write_health", lambda *_: {"CT": state})
    commands = []
    listed_issues = []

    class Completed:
        def __init__(self, stdout=""):
            self.stdout = stdout

    def fake_run(args, **_kwargs):
        commands.append(args)
        if args[1:3] == ["issue", "list"]:
            return Completed(json.dumps(listed_issues))
        return Completed()

    monkeypatch.setattr("subprocess.run", fake_run)
    runner = CliRunner()
    result = runner.invoke(cli_module.cli, ["report", "--db", str(db_path), "--gh-issues"])
    assert result.exit_code == 0, result.output
    assert any(args[1:4] == ["issue", "create", "--title"]
               and args[4] == "[health] CT collection overdue" for args in commands)

    listed_issues.append({"title": "[health] CT collection overdue", "number": 42})
    state["last_success_overdue"] = False
    commands.clear()
    result = runner.invoke(cli_module.cli, ["report", "--db", str(db_path), "--gh-issues"])
    assert result.exit_code == 0, result.output
    assert any(args[1:4] == ["issue", "close", "42"] for args in commands)
