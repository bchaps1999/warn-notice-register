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


def _exportable(registry) -> list[str]:
    """States whose held rows enter exports: active plus archive (fetch
    broken but data retained)."""
    return [c.postal for c in registry.all() if c.status in ("active", "archive")]


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
        export_csvs(conn, Path(data_dir) / "exports", _exportable(registry))
        write_health(conn, registry, Path(data_dir) / "health")
        _compress_db(db_path)

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
@click.argument("states", nargs=-1)
@click.option("--workdir", type=click.Path(path_type=Path), default=Path("workdir/backfill"))
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=db_mod.DEFAULT_DB_PATH)
@click.option("--data-dir", type=click.Path(path_type=Path), default=DEFAULT_DATA_DIR)
def backfill(states, workdir: Path, db_path: Path, data_dir: Path) -> None:
    """Ingest historical data from warn-github-flow branches for STATES
    (default: all active states)."""
    from warnlive.backfill import github_flow

    registry = load_registry()
    if states:
        configs = registry.for_run(states=list(states))
    else:
        configs = [c for c in registry.all() if c.status == "active"]

    raw_dir = Path(workdir) / "raw"
    downloaded = []
    for cfg in configs:
        if github_flow.download_state(cfg.postal, raw_dir) is not None:
            downloaded.append(cfg)
        else:
            click.echo(f"{cfg.postal.upper()}: no upstream history, skipped")
    if not downloaded:
        click.echo("Nothing to backfill.", err=True)
        sys.exit(1)

    conn = db_mod.connect(db_path)
    db_mod.init_db(conn)
    report = pipeline.run_states(
        conn, registry, downloaded, workdir,
        trigger="backfill", use_cache=True,
    )
    export_csvs(conn, Path(data_dir) / "exports", _exportable(registry))
    write_health(conn, registry, Path(data_dir) / "health")
    _compress_db(db_path)
    _print_report(report)


@cli.command("backfill-bln")
@click.argument("states", nargs=-1)
@click.option("--workdir", type=click.Path(path_type=Path), default=Path("workdir/backfill"))
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=db_mod.DEFAULT_DB_PATH)
@click.option("--data-dir", type=click.Path(path_type=Path), default=DEFAULT_DATA_DIR)
@click.option("--gaps", is_flag=True,
              help="Fill months where a state has zero notices (instead of only "
                   "rows older than its oldest notice).")
@click.option("--all-missing", is_flag=True,
              help="With --gaps: ingest every BLN row lacking a (state, employer, "
                   "date) match, even in months we already cover. Review "
                   "health/dupes_review.csv afterwards.")
def backfill_bln(states, workdir: Path, db_path: Path, data_dir: Path,
                 gaps: bool, all_missing: bool) -> None:
    """Deep-history backfill from BLN's accumulated integrated dataset,
    ingesting only rows older than each state's current oldest notice
    (or into coverage gaps with --gaps)."""
    from warnlive.backfill import bln_integrated
    from warnlive.store.dedupe import ingest

    if all_missing and not gaps:
        raise click.UsageError("--all-missing requires --gaps")

    registry = load_registry()
    conn = db_mod.connect(db_path)
    db_mod.init_db(conn)

    csv_path = bln_integrated.download(Path(workdir))
    if gaps:
        per_state = bln_integrated.gap_rows_by_state(
            csv_path, conn, registry, list(states) or None, all_missing=all_missing
        )
    else:
        per_state = bln_integrated.older_rows_by_state(
            csv_path, conn, registry, list(states) or None
        )
    if not per_state:
        click.echo("No eligible rows to ingest.")
        return
    from warnlive.pipeline import now_utc

    for postal in sorted(per_state):
        stats = ingest(conn, per_state[postal], observed_at=now_utc()[:10])
        click.echo(f"{postal}: +{stats.new} new, {stats.unchanged} already present")

    export_csvs(conn, Path(data_dir) / "exports", _exportable(registry))
    write_health(conn, registry, Path(data_dir) / "health")
    _compress_db(db_path)


@cli.command()
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=db_mod.DEFAULT_DB_PATH)
@click.option("--data-dir", type=click.Path(path_type=Path), default=DEFAULT_DATA_DIR)
def export(db_path: Path, data_dir: Path) -> None:
    """Rebuild CSV exports and the health report from the database."""
    registry = load_registry()
    conn = db_mod.connect(db_path)
    db_mod.init_db(conn)
    counts = export_csvs(conn, Path(data_dir) / "exports", _exportable(registry))
    write_health(conn, registry, Path(data_dir) / "health")
    _compress_db(db_path)
    for path, n in counts.items():
        click.echo(f"{path}: {n} rows")


@cli.command("repair-dates")
@click.argument("states", nargs=-1, required=True)
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=db_mod.DEFAULT_DB_PATH)
@click.option("--dry-run", is_flag=True, help="Report what would change without writing.")
def repair_dates(states, db_path: Path, dry_run: bool) -> None:
    """Re-derive null notice_dates for STATES from stored raw fields.

    For each null-notice_date row, re-runs the state's (fixed) transformer
    date logic against the raw_extra preserved in its current version, then
    updates notice_date AND dedupe_key together (the key hashes the date, so
    fixing one without the other would duplicate the state on next scrape).
    If the recomputed key collides with an existing notice, the row is left
    untouched and linked as a possible_duplicate instead (link, never merge).
    """
    import json as json_mod

    from warnlive.normalize.engine import _dedupe_key, _record_hash, get_transformer_class
    from warnlive.pipeline import now_utc

    conn = db_mod.connect(db_path)
    db_mod.init_db(conn)
    now = now_utc()

    for state in states:
        postal = state.upper()
        cls = get_transformer_class(postal)
        transformer = cls.__new__(cls)  # date logic only; skip raw-file loading
        date_field = cls.fields.get("notice_date")
        if date_field is None:
            click.echo(f"{postal}: transformer maps no notice_date; skipping")
            continue

        rows = conn.execute(
            """SELECT n.id, n.dedupe_key, n.state, n.employer_name, n.location,
                      n.current_version, v.fields_json
               FROM notices n JOIN notice_versions v
                 ON v.notice_id = n.id AND v.version = n.current_version
               WHERE n.state = ? AND n.notice_date IS NULL""",
            (postal,),
        ).fetchall()

        repaired = collided = unparsed = 0
        for row in rows:
            fields = json_mod.loads(row["fields_json"])
            try:
                raw = json_mod.loads(fields.get("raw_extra") or "{}")
            except (TypeError, ValueError):
                raw = {}
            try:
                value = raw[date_field] if isinstance(date_field, str) else date_field(raw)
                parsed = transformer.transform_date(value) if value else None
            except Exception:  # noqa: BLE001 — unparseable raw stays null
                parsed = None
            if parsed is None:
                unparsed += 1
                continue
            # transform_date returns a datetime, date, or preformatted string
            notice_date = (parsed if isinstance(parsed, str) else parsed.isoformat())[:10]

            new_key = _dedupe_key(
                {
                    "state": row["state"],
                    "employer_name": row["employer_name"],
                    "notice_date": notice_date,
                    "location": row["location"],
                }
            )
            existing = conn.execute(
                "SELECT id FROM notices WHERE dedupe_key = ? AND id != ?",
                (new_key, row["id"]),
            ).fetchone()
            if existing is not None:
                collided += 1
                if not dry_run:
                    conn.execute(
                        """INSERT OR IGNORE INTO notice_links
                             (notice_id, related_id, kind, score, method, detail, created_at)
                           VALUES (?, ?, 'possible_duplicate', 0.9, 'date-repair',
                                   'repaired date collides with existing key', ?)""",
                        (row["id"], existing["id"], now),
                    )
                continue

            repaired += 1
            if dry_run:
                continue
            fields["notice_date"] = notice_date
            fields["dedupe_key"] = new_key
            fields["raw_record_hash"] = _record_hash(fields)
            conn.execute(
                """UPDATE notices SET notice_date = ?, dedupe_key = ?,
                     current_version = current_version + 1, is_amended = 1 WHERE id = ?""",
                (notice_date, new_key, row["id"]),
            )
            conn.execute(
                """INSERT INTO notice_versions
                     (notice_id, version, raw_record_hash, fields_json, observed_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    row["id"],
                    row["current_version"] + 1,
                    fields["raw_record_hash"],
                    json_mod.dumps(fields, sort_keys=True, ensure_ascii=False),
                    now,
                ),
            )
        if not dry_run:
            conn.commit()
        label = "would repair" if dry_run else "repaired"
        click.echo(
            f"{postal}: {label} {repaired}, collisions linked {collided}, "
            f"unparseable {unparsed} (of {len(rows)} null-date rows)"
        )


@cli.command()
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=db_mod.DEFAULT_DB_PATH)
@click.option("--data-dir", type=click.Path(path_type=Path), default=DEFAULT_DATA_DIR)
def dupes(db_path: Path, data_dir: Path) -> None:
    """Detect revision/duplicate links between notices (link, never merge).

    Rebuilds the notice_links table, exports notice_links.csv, and writes
    gray-zone candidate pairs to health/dupes_review.csv for human review.
    """
    from warnlive.store import links as links_mod

    conn = db_mod.connect(db_path)
    db_mod.init_db(conn)
    stats = links_mod.rebuild(
        conn, review_path=Path(data_dir) / "health" / "dupes_review.csv"
    )
    n = links_mod.export_links_csv(conn, Path(data_dir) / "exports" / "notice_links.csv")
    _compress_db(db_path)
    click.echo(f"{stats['links']} links ({n} exported), {stats['review']} pairs for review")
    for key, count in stats["by_kind_method"].items():
        click.echo(f"  {key}: {count}")


@cli.command()
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=db_mod.DEFAULT_DB_PATH)
@click.option("--gh-issues", is_flag=True,
              help="Open a GitHub issue per newly-failing active state and close on recovery (needs gh CLI or GH_TOKEN in CI).")
def report(db_path: Path, gh_issues: bool) -> None:
    """Print per-state health; optionally sync GitHub issues."""
    import json as json_mod
    import subprocess

    registry = load_registry()
    conn = db_mod.connect(db_path)
    db_mod.init_db(conn)
    status = write_health(conn, registry, DEFAULT_DATA_DIR / "health")

    failing = {
        postal: s
        for postal, s in status.items()
        if s["registry_status"] == "active" and s["consecutive_failures"] >= 1
    }
    for postal, s in sorted(status.items()):
        if s["latest_verdict"]:
            click.echo(
                f"{postal}: {s['latest_verdict']} (streak {s['consecutive_failures']})"
            )
    if not gh_issues:
        return

    def gh(*args: str) -> str:
        return subprocess.run(
            ["gh", *args], check=True, capture_output=True, text=True
        ).stdout

    open_issues = {}
    for issue in json_mod.loads(
        gh("issue", "list", "--label", "state-health", "--state", "open",
           "--json", "number,title")
    ):
        open_issues[issue["title"]] = issue["number"]

    for postal, s in failing.items():
        title = f"[health] {postal} failing"
        if title in open_issues:
            continue
        body = (
            f"State {postal} ({s['name']}) has failed {s['consecutive_failures']} "
            f"consecutive run(s).\n\nLatest error: {s['latest_error'] or 'see checks'}\n\n"
            f"Checks: ```json\n{json_mod.dumps(s['latest_checks'], indent=2)}\n```\n"
            + ("\nRecommend flipping status to `broken` in states.yaml.\n"
               if s["recommend_broken"] else "")
        )
        gh("issue", "create", "--title", title, "--body", body, "--label", "state-health")
        click.echo(f"opened issue: {title}")

    for title, number in open_issues.items():
        postal = title.removeprefix("[health] ").removesuffix(" failing")
        s = status.get(postal)
        if s and s["latest_verdict"] in ("ok", "degraded"):
            gh("issue", "close", str(number), "--comment",
               f"{postal} recovered: latest run verdict is {s['latest_verdict']}.")
            click.echo(f"closed issue: {title}")


@cli.command("build-site")
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=db_mod.DEFAULT_DB_PATH)
@click.option("--out", "out_dir", type=click.Path(path_type=Path), default=Path("site/public/data"))
def build_site(db_path: Path, out_dir: Path) -> None:
    """Emit the static JSON dataset consumed by the site/ SPA."""
    from warnlive.store.site_export import build_site as build

    registry = load_registry()
    conn = db_mod.connect(db_path)
    db_mod.init_db(conn)
    counts = build(conn, registry, out_dir)
    for name, size in counts.items():
        click.echo(f"{name}: {size:,} bytes")


def _compress_db(db_path: Path) -> None:
    """Refresh the committed gzip copy of the database (the raw sqlite file
    exceeds GitHub's file-size comfort zone and is gitignored)."""
    import gzip
    import shutil

    if not Path(db_path).exists():
        return
    with open(db_path, "rb") as src, gzip.open(f"{db_path}.gz", "wb", compresslevel=9) as dst:
        shutil.copyfileobj(src, dst)


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
