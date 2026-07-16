"""warnlive command-line interface."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import click

from warnlive import pipeline
from warnlive.registry import load_registry
from warnlive.store import db as db_mod
from warnlive.store.export import export_csvs
from warnlive.verify.report import write_health

DEFAULT_WORKDIR = Path("workdir")
DEFAULT_DATA_DIR = Path("data")


@click.group()
@click.option("-v", "--verbose", is_flag=True)
def cli(verbose: bool) -> None:
    """Consolidated WARN notice pipeline."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # Upstream scrapers log noisily at DEBUG
    if not verbose:
        for noisy in ("urllib3", "selenium", "pdfminer"):
            logging.getLogger(noisy).setLevel(logging.WARNING)


@cli.command("init-db")
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=db_mod.DEFAULT_DB_PATH)
def init_db(db_path: Path) -> None:
    """Create (or check) the SQLite schema."""
    conn = db_mod.connect(db_path)
    db_mod.init_db(conn)
    click.echo(f"Database ready at {db_path}")


@cli.command()
@click.argument("states", nargs=-1)
@click.option("--cadence", type=click.Choice(["daily", "weekly"]), default=None,
              help="Run all active states for this cadence (weekly = full sweep).")
@click.option("--include-unverified", is_flag=True,
              help="Also run states still marked unverified.")
@click.option("--smoke", is_flag=True, help="Fetch+normalize+verify only; no DB writes.")
@click.option("--use-cache", is_flag=True,
              help="Reuse existing raw CSVs in the workdir instead of fetching.")
@click.option("--workdir", type=click.Path(path_type=Path), default=DEFAULT_WORKDIR)
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=db_mod.DEFAULT_DB_PATH)
@click.option("--data-dir", type=click.Path(path_type=Path), default=DEFAULT_DATA_DIR)
@click.option("--trigger", default="manual", help="Run trigger label (manual/scheduled/backfill).")
def scrape(states, cadence, include_unverified, smoke, use_cache, workdir, db_path, data_dir, trigger):
    """Scrape STATES (or all active states for --cadence), verify, ingest, export."""
    registry = load_registry()
    configs = registry.for_run(
        states=list(states) or None,
        cadence=cadence,
        include_unverified=include_unverified,
    )
    if not configs:
        click.echo("No states selected.", err=True)
        sys.exit(2)

    conn = None
    if not smoke:
        conn = db_mod.connect(db_path)
        db_mod.init_db(conn)

    report = pipeline.run_states(
        conn, registry, configs, workdir,
        trigger=trigger, smoke=smoke, use_cache=use_cache,
    )

    if conn is not None:
        active = [c.postal for c in registry.all() if c.status == "active"]
        export_csvs(conn, Path(data_dir) / "exports", active)
        write_health(conn, registry, Path(data_dir) / "health")

    _print_report(report)
    # Exit nonzero only if EVERY state failed (systemic problem);
    # individual failures are health-report business, not run failures.
    if report.outcomes and all(o.verdict == "failed" for o in report.outcomes):
        sys.exit(1)


@cli.command()
@click.argument("state")
@click.option("--workdir", type=click.Path(path_type=Path), default=DEFAULT_WORKDIR)
@click.option("--use-cache", is_flag=True)
def verify(state: str, workdir: Path, use_cache: bool) -> None:
    """Live-verify one state (no DB writes) and print its check results."""
    registry = load_registry()
    configs = registry.for_run(states=[state])
    report = pipeline.run_states(
        None, registry, configs, workdir, smoke=True, use_cache=use_cache
    )
    outcome = report.outcomes[0]
    click.echo(f"\n{outcome.state}: {outcome.verdict.upper()}")
    if outcome.error:
        click.echo(f"  error: {outcome.error}")
    for check in (outcome.checks or {}).get("checks", []):
        icon = {"pass": "✓", "warn": "~", "fail": "✗"}[check["outcome"]]
        click.echo(f"  {icon} {check['name']}: {check['detail']}")
    sys.exit(0 if outcome.verdict != "failed" else 1)


@cli.command()
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=db_mod.DEFAULT_DB_PATH)
@click.option("--data-dir", type=click.Path(path_type=Path), default=DEFAULT_DATA_DIR)
def export(db_path: Path, data_dir: Path) -> None:
    """Rebuild CSV exports and the health report from the database."""
    registry = load_registry()
    conn = db_mod.connect(db_path)
    db_mod.init_db(conn)
    active = [c.postal for c in registry.all() if c.status == "active"]
    counts = export_csvs(conn, Path(data_dir) / "exports", active)
    write_health(conn, registry, Path(data_dir) / "health")
    for path, n in counts.items():
        click.echo(f"{path}: {n} rows")


def _print_report(report: pipeline.RunReport) -> None:
    click.echo("")
    for o in sorted(report.outcomes, key=lambda o: o.state):
        click.echo(
            f"{o.state}  {o.verdict:9s} raw={o.raw_rows:<6d} normalized={o.normalized_rows:<6d} "
            f"new={o.new:<5d} updated={o.updated:<4d}"
            + (f" error={o.error}" if o.error else "")
        )
    counts = {}
    for o in report.outcomes:
        counts[o.verdict] = counts.get(o.verdict, 0) + 1
    click.echo("\nSummary: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))


if __name__ == "__main__":
    cli()
