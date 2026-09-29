"""The scrape command's post-ingest path, which CI runs every day."""

from pathlib import Path

from click.testing import CliRunner

from warnlive import cli, pipeline


def test_scrape_reapplies_quality_evidence_and_exports(monkeypatch, tmp_path):
    calls = {}

    def fake_run_states(conn, registry, configs, workdir, **_kwargs):
        return pipeline.RunReport(trigger="scheduled", started_at="2026-09-29T00:00:00Z")

    def fake_apply(conn, root, observed_at, *, states, require_volta):
        calls["apply"] = (observed_at, states, require_volta)
        return {"applied": 0}

    monkeypatch.setattr(pipeline, "run_states", fake_run_states)
    monkeypatch.setattr("warnlive.migrate.quality_evidence.apply", fake_apply)
    monkeypatch.setattr(
        "warnlive.migrate.quality_evidence.DEFAULT_ROOT", tmp_path,
    )
    monkeypatch.setattr(cli, "export_csvs", lambda *a, **k: calls.setdefault("export", True))
    monkeypatch.setattr(cli, "write_health", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_compress_db", lambda *a, **k: None)

    result = CliRunner().invoke(cli.cli, [
        "scrape", "ca", "--trigger", "scheduled",
        "--db", str(tmp_path / "warn.sqlite"),
        "--workdir", str(tmp_path / "work"),
        "--data-dir", str(tmp_path / "data"),
        "--run-report", str(tmp_path / "run.json"),
    ])
    assert result.exit_code == 0, result.output + repr(result.exception)
    observed_at, states, require_volta = calls["apply"]
    assert len(observed_at) == 10 and observed_at[4] == "-"
    assert states == {"CA"} and require_volta is False
    assert calls["export"] is True
    assert Path(tmp_path / "run.json").is_file()
