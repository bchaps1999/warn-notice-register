"""warnlive command-line interface."""

from __future__ import annotations

import logging
import os
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
ENV_FILE = Path(".env")


def _load_env(path: Path = ENV_FILE) -> None:
    """Read .env into the environment, without overriding what is already set.

    Credentials live in a gitignored .env, and every command that needs one
    reads os.environ. A shell that has not sourced the file therefore fails
    at the point of use rather than at the point of configuration — which
    for a paid API means a run that queues two hundred rows, calls nothing,
    and reports two hundred failures. Reading it here makes the file mean
    what everyone assumes it means. Anything already exported wins, so an
    explicit override on the command line still does.
    """
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _exportable(registry) -> list[str]:
    """States whose held rows enter exports: active plus archive (fetch
    broken but data retained)."""
    return [c.postal for c in registry.all() if c.status in ("active", "archive")]


@click.group()
@click.option("-v", "--verbose", is_flag=True)
def cli(verbose: bool) -> None:
    """Consolidated WARN notice pipeline."""
    _load_env()
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


@cli.command("backfill-archives")
@click.argument("states", nargs=-1)
@click.option("--workdir", type=click.Path(path_type=Path), default=Path("workdir/backfill"))
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=db_mod.DEFAULT_DB_PATH)
@click.option("--data-dir", type=click.Path(path_type=Path), default=DEFAULT_DATA_DIR)
@click.option(
    "--refresh-raw",
    is_flag=True,
    help="Re-parse cached artifacts and update already-ingested rows' raw "
    "fields (for parsers that learned to read new columns); ingests nothing.",
)
@click.option(
    "--reingest",
    is_flag=True,
    help="Delete this state's archive-sourced rows and rebuild them from the "
    "cached artifacts. For parser fixes that change dedupe keys, which cannot "
    "be repaired in place. Live-scraped rows are never touched.",
)
def backfill_archives(
    states, workdir: Path, db_path: Path, data_dir: Path,
    refresh_raw: bool, reingest: bool,
) -> None:
    """Ingest pre-portal history from official state archives (Wayback
    captures of agency artifacts) for STATES (default: all with a fetcher).
    Only fills months the state currently has no notices in."""
    from warnlive.backfill import state_archives
    from warnlive.pipeline import now_utc
    from warnlive.store.dedupe import ingest

    registry = load_registry()
    conn = db_mod.connect(db_path)
    db_mod.init_db(conn)

    wanted = [s.upper() for s in states] if states else list(state_archives.FETCHERS)
    unknown = [s for s in wanted if s not in state_archives.FETCHERS]
    if unknown:
        raise click.UsageError(
            f"No archive fetcher for: {', '.join(unknown)} "
            f"(available: {', '.join(sorted(state_archives.FETCHERS))})"
        )

    for postal in wanted:
        records = state_archives.FETCHERS[postal](Path(workdir) / "cache")
        if refresh_raw:
            stats = state_archives.refresh_raw(conn, records)
            click.echo(
                f"{postal}: {len(records)} archive rows re-parsed, "
                f"{stats['updated']} raw records refreshed, "
                f"{stats['not_in_db']} not in db"
            )
            continue
        if reingest:
            dropped = state_archives.drop_archive_rows(conn, postal)
            click.echo(f"{postal}: dropped {dropped} archive-sourced rows")
        eligible = state_archives.gap_filter(conn, postal, records)
        stats = ingest(conn, eligible, observed_at=now_utc()[:10])
        click.echo(
            f"{postal}: {len(records)} archive rows, {len(eligible)} in empty "
            f"months, +{stats.new} new"
        )

    export_csvs(conn, Path(data_dir) / "exports", _exportable(registry))
    write_health(conn, registry, Path(data_dir) / "health")
    _compress_db(db_path)


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


@cli.command("edgar-refresh")
@click.option("--workdir", type=click.Path(path_type=Path), default=Path("workdir/backfill"))
def edgar_refresh(workdir: Path) -> None:
    """Rebuild the EDGAR (name, CIK, era, ticker) reference file from the
    quarterly full indexes (1993-present; cached, ~130 small files)."""
    from warnlive.enrich import edgar

    n = edgar.refresh(Path(workdir) / "cache")
    click.echo(f"{edgar.REFERENCE_PATH}: {n} (name, cik) rows")


@cli.command("edgar-sic-refresh")
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=db_mod.DEFAULT_DB_PATH)
def edgar_sic_refresh(db_path: Path) -> None:
    """Fetch SIC industry codes for every CIK our notices match (one
    submissions-API request per new CIK; incremental). Needs SEC_EDGAR_UA."""
    from warnlive.enrich import edgar

    conn = db_mod.connect(db_path)
    db_mod.init_db(conn)
    n = edgar.sic_refresh(conn)
    click.echo(f"{edgar.SIC_PATH}: {n} CIKs")


@cli.command("nonprofit-refresh")
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=db_mod.DEFAULT_DB_PATH)
def nonprofit_refresh(db_path: Path) -> None:
    """Match CIK-less employers to IRS EINs and NTEE activity codes by
    joining against the exempt-organization Business Master File."""
    from warnlive.enrich import nonprofits

    conn = db_mod.connect(db_path)
    db_mod.init_db(conn)
    n = nonprofits.refresh(conn)
    click.echo(f"{nonprofits.PATH}: {n} employers matched to an EIN")


@cli.command("identity-review")
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=db_mod.DEFAULT_DB_PATH)
@click.option("--top", "limit", default=3000,
              help="How many unidentified employers to collect candidates for.")
def identity_review(db_path: Path, limit: int) -> None:
    """Write the near-miss identity candidates the matcher refused, ranked
    by workers affected, for later adjudication. Decisions go back in
    data/reference/identity_overrides.csv and outrank automatic matching."""
    from warnlive.enrich import review

    conn = db_mod.connect(db_path)
    db_mod.init_db(conn)
    n = review.build(conn, limit=limit)
    click.echo(f"{review.REVIEW_PATH}: {n} candidate rows")


@cli.command("subsidiary-refresh")
@click.option("--limit", type=int, default=None,
              help="Crawl at most this many registrants, then stop (resumable).")
def subsidiary_refresh(limit: int | None) -> None:
    """Build the subsidiary -> parent index from SEC Exhibit 21 filings.
    Long-running and resumable; re-run until no registrants remain.
    Needs SEC_EDGAR_UA."""
    from warnlive.enrich import subsidiaries

    n = subsidiaries.refresh(limit=limit)
    click.echo(f"{subsidiaries.PATH}: {n} subsidiary names")


@cli.command("gleif-refresh")
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=db_mod.DEFAULT_DB_PATH)
@click.option("--top", "top_n", default=3000, help="How many top CIK-less employers to look up.")
def gleif_refresh(db_path: Path, top_n: int) -> None:
    """Resolve top CIK-less employers to Legal Entity Identifiers
    (incremental; misses are recorded and not retried)."""
    from warnlive.enrich import gleif

    conn = db_mod.connect(db_path)
    db_mod.init_db(conn)
    n = gleif.refresh(conn, top_n=top_n)
    click.echo(f"{gleif.PATH}: {n} matched")


@cli.command("places-refresh")
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=db_mod.DEFAULT_DB_PATH)
@click.option("--review-only", is_flag=True,
              help="Rebuild only the unresolved-location queue, against the "
                   "roster already on disk. The Census files change once a "
                   "year; what can be placed changes every time an alias is "
                   "added or a notice arrives.")
def places_refresh(db_path: Path, review_only: bool) -> None:
    """Rebuild the Census place and county roster used to resolve notice
    locations, and list the locations it cannot place."""
    from warnlive.enrich import places

    if not review_only:
        n = places.refresh()
        click.echo(f"{places.PATH}: {n} places and counties")
    elif not places.PATH.exists():
        raise click.UsageError(
            f"{places.PATH} does not exist; run without --review-only first"
        )
    conn = db_mod.connect(db_path)
    db_mod.init_db(conn)
    unresolved = places.review(conn)
    click.echo(f"{places.REVIEW_PATH}: {unresolved} unresolved locations")


@cli.group()
def adjudicate() -> None:
    """Ask a model to settle what the deterministic tiers refused.

    Every proposal is judged by the same code that refused in the first
    place — an alias must make the resolver place the string, a name must
    clear the EDGAR matcher's gates — so a wrong guess fails rather than
    landing as a confident match. What survives is appended to the
    reference files under data/reference with the model named as the
    decider; what does not is staged under data/health for a person.

    Runs by hand, never in CI: scheduled scrapes read reference files and
    contact no model. Needs the key named in adjudicate/providers.yaml
    (DEEPSEEK_API_KEY by default).
    """


def _llm_options(fn):
    """The options every adjudicate subcommand shares."""
    for option in reversed([
        click.option("--limit", type=int, default=None,
                     help="Stop after this many rows of real work."),
        click.option("--min-workers", default=0,
                     help="Ignore rows with fewer workers riding on them."),
        click.option("--dry-run", is_flag=True,
                     help="Re-judge the ledger through today's gates; no API calls."),
        click.option("--reask", is_flag=True,
                     help="Ask again even where an answer is already on file."),
        click.option("--budget", type=float, default=None,
                     help="Hard ceiling in USD, checked before each call."),
        click.option("--provider", default=None, help="Provider from providers.yaml."),
        click.option("--model", "model_alias", default=None, help="Model alias, e.g. flash."),
        click.option("--threshold", default=0.8, show_default=True,
                     help="Confidence at or above which a cleared proposal is written."),
        click.option("--write/--no-write", default=True,
                     help="Whether to write reference and staging files."),
        click.option("--thinking/--no-thinking", default=True,
                     help="Let the model reason before answering. Reasoning is "
                          "billed as output and ran ~3x the answer itself; "
                          "--no-thinking is cheaper and measurably worse on "
                          "the judgements the gates cannot check."),
    ]):
        fn = option(fn)
    return fn


def _client_for(provider, model_alias, budget, dry_run):
    """The client to call with, and the model name to replay answers under.

    A dry run has no client and still needs the model's name: the ledger is
    keyed by it, so replaying without it would match nothing and report an
    empty queue as though there were no work.
    """
    from warnlive.adjudicate.client import Client, resolve

    model = resolve(provider, model_alias)
    click.echo(f"model: {model}" + (" (dry run, no calls)" if dry_run else ""))
    if dry_run:
        return None, str(model)
    return Client(model, budget=budget), str(model)


def _report(tally, client) -> None:
    click.echo(tally.summary())
    if client is not None:
        click.echo(client.usage.summary())
    if tally.stopped:
        click.echo(f"stopped: {tally.stopped}")


@adjudicate.command("sweep")
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=db_mod.DEFAULT_DB_PATH)
@click.option("--sample", default=300, show_default=True,
              help="Employers per configuration, from the tune split.")
@click.option("--cut", default=0.9, show_default=True,
              help="Confidence cut the table is ranked at.")
@click.option("--budget-each", type=float, default=0.10, show_default=True,
              help="Ceiling per configuration, in USD.")
@click.option("--prompts", default=None,
              help="Comma-separated prompt names. Default: every file in "
                   "adjudicate/prompts.")
@click.option("--settings", is_flag=True,
              help="Sweep batch size, thinking and model on the given prompt "
                   "instead of comparing prompts.")
def adjudicate_sweep(db_path, sample, cut, budget_each, prompts, settings) -> None:
    """Compare prompts and settings on the tune split, before trusting one.

    Never touches the test split: that half is scored once, by the winner,
    and is the only number worth quoting because nothing was changed in
    response to it."""
    from warnlive.adjudicate import industry as adj_industry
    from warnlive.adjudicate import sweep as sweep_mod

    conn = db_mod.connect(db_path)
    db_mod.init_db(conn)
    items = adj_industry.load_calibration(conn, sample=sample, split="tune")
    click.echo(f"{len(items)} employers from the tune split\n")

    names = ([p.strip() for p in prompts.split(",")] if prompts else
             sorted(p.stem for p in adj_industry.PROMPTS_DIR.glob("*.txt")))
    if settings:
        base = names[0]
        configs = [
            sweep_mod.Config(base),
            sweep_mod.Config(base, batch_size=10),
            sweep_mod.Config(base, thinking=False),
            sweep_mod.Config(base, model="pro"),
        ]
    else:
        configs = [sweep_mod.Config(n) for n in names]

    results = sweep_mod.run(items, configs, cut=cut, budget_each=budget_each)
    click.echo("\n" + sweep_mod.table(results, cut))
    sweep_mod.write(results, cut)
    click.echo(f"\nwritten to {sweep_mod.RESULTS_PATH}")
    total = sum(r.cost for r in results)
    click.echo(f"total spent: ${total:.4f}")


@adjudicate.command("places")
@_llm_options
@click.option("--auto-county", is_flag=True,
              help="Also write county-level answers automatically. Off by "
                   "default: nothing can check one, since the gate only "
                   "confirms the county exists and every real county does.")
def adjudicate_places(limit, min_workers, dry_run, reask, budget, provider,
                      model_alias, threshold, write, thinking, auto_county) -> None:
    """Resolve the location strings the Census gazetteer could not place.

    A proposal is written into the alias table and the resolver is run again
    on the original string: either it now names a real Census place or
    county, or the proposal is worth nothing. Strings that name no geography
    at all — workforce investment areas, "Various Cities" — are recorded as
    rejected so they stop returning to the review file."""
    from warnlive.adjudicate import places as adj_places
    from warnlive.adjudicate import queue as queue_mod

    items = adj_places.load_queue(min_workers=min_workers)
    click.echo(f"{adj_places.REVIEW_PATH}: {len(items)} unresolved locations queued")
    client, model_name = _client_for(provider, model_alias, budget, dry_run)
    worker = adj_places.Places(threshold=threshold, auto_county=auto_county)
    worker.thinking = thinking
    tally = queue_mod.run(
        worker, items, client=client, limit=limit, dry_run=dry_run,
        reask=reask, model=model_name,
    )
    _report(tally, client)
    if write and not dry_run and tally.rows:
        written, staged = adj_places.write(
            tally.rows, decided_by=model_name
        )
        click.echo(f"{adj_places.ALIAS_PATH}: +{written} decided")
        click.echo(f"{adj_places.STAGING_PATH}: {staged} staged for review")


@adjudicate.command("identity")
@_llm_options
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=db_mod.DEFAULT_DB_PATH)
@click.option("--corroborators", default=2, show_default=True,
              help="Independent witnesses a matched CIK must have.")
def adjudicate_identity(limit, min_workers, dry_run, reask, budget, provider,
                        model_alias, threshold, write, thinking, db_path, corroborators) -> None:
    """Identify the employers the EDGAR matcher could not.

    A proposed registrant must clear the unmodified matcher and then be
    corroborated by evidence the proposal never saw — the filing calendar,
    a parent's Exhibit 21, the state-published industry, the IRS or GLEIF
    rosters. A subsidiary becomes a parent link rather than an identity,
    because First Transit is owned by FirstGroup and is not FirstGroup."""
    from warnlive.adjudicate import identity as adj_identity
    from warnlive.adjudicate import queue as queue_mod

    conn = db_mod.connect(db_path)
    db_mod.init_db(conn)
    items = adj_identity.load_queue(conn, min_workers=min_workers)
    click.echo(f"{len(items)} unidentified employers queued")
    client, model_name = _client_for(provider, model_alias, budget, dry_run)
    worker = adj_identity.Identity(
        threshold=threshold, min_corroborators=corroborators
    )
    worker.thinking = thinking
    tally = queue_mod.run(
        worker, items, client=client, limit=limit, dry_run=dry_run,
        reask=reask, model=model_name,
    )
    _report(tally, client)
    if write and not dry_run and tally.rows:
        ids, links, staged = adj_identity.write(
            tally.rows, decided_by=model_name
        )
        click.echo(f"{adj_identity.OVERRIDES_PATH}: +{ids} identities")
        click.echo(f"{adj_identity.SUBSIDIARY_OVERRIDES}: +{links} parent links")
        click.echo(f"{adj_identity.STAGING_PATH}: {staged} staged for review")


@adjudicate.command("confirm")
@_llm_options
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=db_mod.DEFAULT_DB_PATH)
def adjudicate_confirm(limit, min_workers, dry_run, reask, budget, provider,
                       model_alias, threshold, write, thinking, db_path) -> None:
    """Confirm the matches no independent record could corroborate.

    `adjudicate identity` writes a match only when two outside authorities
    agree with it, and stages the rest. For an obscure employer nothing else
    knows about, staged means abandoned. This asks the narrower question the
    staging leaves open — is this named registrant this named employer — with
    the roster's view of the company beside the notices' view of it.

    Runs only where deterministic corroboration failed, so it never overrides
    evidence; it speaks where there is none.
    """
    from warnlive.adjudicate import confirm as adj_confirm
    from warnlive.adjudicate import identity as adj_identity
    from warnlive.adjudicate import queue as queue_mod
    from warnlive.adjudicate.ledger import Ledger

    conn = db_mod.connect(db_path)
    db_mod.init_db(conn)
    client, model_name = _client_for(provider, model_alias, budget, dry_run)
    ledger = Ledger()
    items = adj_confirm.load_queue(conn, ledger, model_name, min_workers=min_workers)
    click.echo(f"{len(items)} uncorroborated matches to confirm")
    worker = adj_confirm.Confirm(threshold=threshold)
    worker.thinking = thinking
    tally = queue_mod.run(
        worker, items, client=client, ledger=ledger, limit=limit,
        dry_run=dry_run, reask=reask, model=model_name,
    )
    _report(tally, client)
    if write and not dry_run and tally.rows:
        ids, links, staged = adj_identity.write(tally.rows, decided_by=model_name)
        click.echo(f"{adj_identity.OVERRIDES_PATH}: +{ids} identities")
        click.echo(f"{adj_identity.STAGING_PATH}: {staged} staged for review")


@adjudicate.command("industry")
@_llm_options
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=db_mod.DEFAULT_DB_PATH)
@click.option("--calibrate", is_flag=True,
              help="Score against states' own published industries instead of "
                   "writing; prints precision at each confidence cut.")
@click.option("--prompt", "prompt_name", default=None,
              help="Which prompt in adjudicate/prompts to use. Its name is "
                   "the version answers are keyed by, so two variants never "
                   "blend and switching back replays rather than re-buys.")
@click.option("--split", type=click.Choice(["tune", "test"]), default="tune",
              show_default=True,
              help="Which half of the labelled employers to grade. Compare "
                   "prompts on 'tune'; score the winner once on 'test'. "
                   "Iterating against 'test' measures the fit to that sample, "
                   "not the prompt.")
@click.option("--sample", type=int, default=1000, show_default=True,
              help="With --calibrate: how many labelled employers to grade, "
                   "drawn at random (seeded). The labelled set is ranked by "
                   "workers, so grading its head would measure the easiest "
                   "employers and overstate the threshold.")
def adjudicate_industry(limit, min_workers, dry_run, reask, budget, provider,
                        model_alias, threshold, write, thinking, db_path, calibrate, sample,
                        split, prompt_name) -> None:
    """Assign a NAICS sector to employers no basis reached.

    Nothing can verify a sector the way the resolver verifies a place, so
    run --calibrate first: it classifies employers whose industry a state
    already published, with the label hidden, and reports precision at each
    confidence cut. Pick --threshold off that curve rather than guessing."""
    from warnlive.adjudicate import industry as adj_industry
    from warnlive.adjudicate import queue as queue_mod
    from warnlive.adjudicate.ledger import Ledger

    conn = db_mod.connect(db_path)
    db_mod.init_db(conn)
    if calibrate:
        items = adj_industry.load_calibration(conn, min_workers=min_workers,
                                              sample=sample, split=split)
    else:
        items = adj_industry.load_queue(conn, min_workers=min_workers)
    click.echo(
        f"{len(items)} employers "
        + (f"with a published industry ({split} split)" if calibrate else "queued")
    )
    client, model_name = _client_for(provider, model_alias, budget, dry_run)
    worker = adj_industry.Industry(
        threshold=threshold,
        **({'prompt': prompt_name} if prompt_name else {}),
    )
    worker.thinking = thinking
    led = Ledger()
    tally = queue_mod.run(
        worker, items, client=client, ledger=led, limit=limit,
        dry_run=dry_run, reask=reask, model=model_name,
    )
    _report(tally, client)

    if calibrate:
        curve = adj_industry.score(items, worker, led, model_name)
        click.echo("\n  cut   answered  coverage  precision  worker-precision")
        for r in curve:
            click.echo(
                f"  {r['threshold']:.2f}  {r['answered']:8d}  {r['coverage']:8.1%}"
                f"  {r['precision']:9.1%}  {r['worker_precision']:16.1%}"
            )
        click.echo(f"\nwritten to {adj_industry.CALIBRATION_PATH}")
        confused = adj_industry.confusions(items, worker, led, model_name)
        if confused:
            click.echo("\nmost confused sectors (published -> predicted):")
            for (truth, pred), n in confused:
                click.echo(f"  {n:5d}  {truth} -> {pred}")
        return

    if write and not dry_run and tally.rows:
        accepted, staged = adj_industry.write(
            tally.rows, decided_by=model_name
        )
        click.echo(f"{adj_industry.OVERRIDES_PATH}: +{accepted} sectors")
        click.echo(f"{adj_industry.STAGING_PATH}: {staged} staged for review")


@cli.command("wikidata-refresh")
def wikidata_refresh() -> None:
    """Fetch all Wikidata entities carrying an SEC CIK (P5531) with parent
    company and industry labels; one bulk SPARQL query."""
    from warnlive.enrich import wikidata

    classes = wikidata.refresh_org_classes()
    click.echo(f"{wikidata.ORG_CLASSES_PATH}: {classes} organization classes")
    n = wikidata.refresh()
    click.echo(f"{wikidata.ORGS_PATH}: {n} CIK-keyed entities")


@cli.command("wikidata-labels")
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=db_mod.DEFAULT_DB_PATH)
@click.option("--top", "top_n", default=1500, help="How many top CIK-less employers to look up.")
@click.option(
    "--retry-misses",
    is_flag=True,
    help="Re-probe names previously recorded as misses (needed after the "
    "match gates change; a miss records the gates, not the name).",
)
def wikidata_labels(db_path: Path, top_n: int, retry_misses: bool) -> None:
    """Resolve top CIK-less employers to Wikidata via exact-unique label
    matching (incremental; misses are recorded and not retried)."""
    from warnlive.enrich import wikidata

    conn = db_mod.connect(db_path)
    db_mod.init_db(conn)
    n = wikidata.label_refresh(conn, top_n=top_n, retry_misses=retry_misses)
    click.echo(f"{wikidata.LABELS_PATH}: {n} matched")


@cli.command("clean-text")
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=db_mod.DEFAULT_DB_PATH)
@click.option("--dry-run", is_flag=True, help="Report what would change without writing.")
def clean_text(db_path: Path, dry_run: bool) -> None:
    """Re-apply display-text hygiene (markup stripping, entity unescaping)
    to employer_name/location of every notice, migrating dedupe keys in
    lockstep. Collisions become links, never merges."""
    from warnlive.normalize.engine import _clean_text as clean
    from warnlive.normalize.engine import _dedupe_key
    from warnlive.pipeline import now_utc

    conn = db_mod.connect(db_path)
    db_mod.init_db(conn)
    now = now_utc()

    changed = collided = 0
    rows = conn.execute(
        "SELECT id, state, employer_name, location, notice_date, dedupe_key "
        "FROM notices"
    ).fetchall()
    for row in rows:
        employer = clean(row["employer_name"])
        location = clean(row["location"])
        if employer == row["employer_name"] and location == row["location"]:
            continue
        if employer is None:
            continue  # never blank out a name during cleanup
        new_key = _dedupe_key(
            {
                "state": row["state"],
                "employer_name": employer,
                "notice_date": row["notice_date"],
                "location": location,
            }
        )
        existing = conn.execute(
            "SELECT id FROM notices WHERE dedupe_key = ? AND id != ?",
            (new_key, row["id"]),
        ).fetchone()
        if existing is not None:
            # A clean twin already owns the new key: keep this row's old key
            # (future re-ingests of the dirty raw row still match it), clean
            # the display fields anyway, and link the pair.
            collided += 1
            new_key = row["dedupe_key"]
            if not dry_run:
                conn.execute(
                    """INSERT OR IGNORE INTO notice_links
                         (notice_id, related_id, kind, score, method, detail, created_at)
                       VALUES (?, ?, 'possible_duplicate', 0.9, 'text-cleanup',
                               'cleaned name collides with existing key', ?)""",
                    (row["id"], existing["id"], now),
                )
        else:
            changed += 1
        if not dry_run:
            conn.execute(
                "UPDATE notices SET employer_name = ?, location = ?, dedupe_key = ? "
                "WHERE id = ?",
                (employer, location, new_key, row["id"]),
            )
    if not dry_run:
        conn.commit()
        _compress_db(db_path)
    label = "would clean" if dry_run else "cleaned"
    click.echo(f"{label} {changed} rows, collisions linked {collided}")


@cli.command("check-regressions")
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=db_mod.DEFAULT_DB_PATH)
@click.option("--data-dir", type=click.Path(path_type=Path), default=DEFAULT_DATA_DIR)
@click.option("--update-snapshot", is_flag=True,
              help="Record the current database as the baseline (do this only "
                   "in the same commit as the data it describes).")
def check_regressions(db_path: Path, data_dir: Path, update_snapshot: bool) -> None:
    """Compare the whole database against the last published snapshot.

    Per-state checks guard a scrape against its own source; this guards the
    database against itself, catching parsers that ingest the right number
    of rows with wrong values in them. Exits nonzero on failure so a
    scheduled run stops before committing or publishing.
    """
    from warnlive.verify import regression

    conn = db_mod.connect(db_path)
    db_mod.init_db(conn)
    snapshot_path = Path(data_dir) / "health" / "snapshot.json"
    result = regression.check_regressions(conn, regression.load_snapshot(snapshot_path))

    icons = {"pass": "✓", "warn": "~", "fail": "✗"}
    for check in result.checks:
        click.echo(f"  {icons[check.outcome]} {check.name}: {check.detail}")
    click.echo(result.verdict.upper())

    if result.verdict == "failed":
        sys.exit(1)
    if update_snapshot:
        regression.write_snapshot(regression.build_snapshot(conn), snapshot_path)
        click.echo(f"snapshot updated: {snapshot_path}")


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
