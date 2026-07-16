"""uiforecast CLI."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import click

FORECAST_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = FORECAST_ROOT / "data"


@click.group()
def main() -> None:
    """Weekly UI claims nowcasting toolkit."""


@main.command("ingest-targets")
@click.option("--start", type=click.DateTime(["%Y-%m-%d"]), default="2014-01-01")
@click.option("--end", type=click.DateTime(["%Y-%m-%d"]), default=None)
def ingest_targets(start, end) -> None:
    """Fetch ICSA/ICNSA vintages from ALFRED; build targets + factors parquet."""
    import pandas as pd

    from uiforecast.ingest import alfred, seasonal

    start_d = start.date()
    end_d = end.date() if end else date.today()
    cache = DATA_DIR / "raw" / "alfred"
    vint_dir = DATA_DIR / "vintages"
    vint_dir.mkdir(parents=True, exist_ok=True)

    frames = {}
    for series in ("ICSA", "ICNSA"):
        click.echo(f"Fetching {series} vintages {start_d} .. {end_d}")
        long_df = alfred.build_vintage_long(
            series, start_d, end_d, cache, progress=True
        )
        long_df.to_parquet(vint_dir / f"{series.lower()}_vintages.parquet")
        frames[series] = alfred.advance_prints(long_df)
        click.echo(
            f"  {series}: {long_df['vintage'].nunique()} vintages, "
            f"{len(frames[series])} advance prints"
        )

    factors = seasonal.implied_factors(frames["ICSA"], frames["ICNSA"])
    factors.to_parquet(vint_dir / "factors.parquet")
    targets = pd.DataFrame(
        {
            "sa_advance": frames["ICSA"]["advance"],
            "nsa_advance": frames["ICNSA"]["advance"],
            "release_date": frames["ICSA"]["release_date"],
        }
    )
    targets.to_parquet(vint_dir / "targets.parquet")
    click.echo(f"Wrote {vint_dir}/targets.parquet ({len(targets)} weeks)")


@main.command("backtest")
@click.option("--start", type=click.DateTime(["%Y-%m-%d"]), default="2017-01-01",
              help="first target week")
@click.option("--end", type=click.DateTime(["%Y-%m-%d"]), default=None)
@click.option("--lag-q", "lag_qs", type=float, multiple=True, default=(0.5,),
              help="WARN visibility lag quantiles (repeatable)")
@click.option("--with-warn/--no-warn", default=True)
@click.option("--run-id", default=None)
def backtest(start, end, lag_qs, with_warn, run_id) -> None:
    """Rolling-origin backtest: baselines (+ AggADL when --with-warn)."""
    from datetime import date as _date

    import pandas as pd

    from uiforecast.eval.harness import (
        BacktestConfig,
        WarnAsOfAggregator,
        run_backtest,
    )
    from uiforecast.models.baselines import ARNSA, RandomWalkSA, SeasonalNaiveNSA
    from uiforecast.models.gate import AggADL, evaluate_gate
    from uiforecast.panel.asof import AsOfStore
    from uiforecast.panel.lags import LagModel
    from uiforecast.report.backtest_report import write_report

    vint = DATA_DIR / "vintages"
    icsa = pd.read_parquet(vint / "icsa_vintages.parquet")
    icnsa = pd.read_parquet(vint / "icnsa_vintages.parquet")
    factors = pd.read_parquet(vint / "factors.parquet")
    targets = pd.read_parquet(vint / "targets.parquet")

    start_d = start.date()
    end_d = end.date() if end else pd.Timestamp(targets.index.max()).date()
    run_id = run_id or f"bt_{start_d}_{end_d}_{'warn' if with_warn else 'base'}"

    results_by_q: dict[float, pd.DataFrame] = {}
    gate_by_q: dict[float, dict] = {}
    for lag_q in lag_qs:
        click.echo(f"=== lag_q={lag_q} ===")
        warn_agg = None
        if with_warn:
            lm = LagModel.from_yaml(FORECAST_ROOT / "config" / "state_lags.yaml")
            store = AsOfStore(
                sqlite_path=FORECAST_ROOT.parent / "data" / "warn.sqlite",
                lag_model=lm,
            )
            warn_agg = WarnAsOfAggregator(
                store, lag_q, panel_start=_date(2013, 1, 1),
                balanced_window=(start_d, end_d),
            )
            click.echo(f"balanced states ({len(warn_agg.states)}): {warn_agg.states}")
        factories = [RandomWalkSA, SeasonalNaiveNSA, ARNSA]
        if with_warn:
            factories.append(AggADL)
        cfg = BacktestConfig(
            origin_start=start_d, origin_end=end_d, lag_q=lag_q,
            model_factories=factories,
            db_path=DATA_DIR / "runs" / "forecast.sqlite",
            run_id=f"{run_id}_q{lag_q}",
        )
        results = run_backtest(cfg, icsa, icnsa, factors, targets, warn_agg)
        results_by_q[lag_q] = results
        n_err = results["error"].notna().sum()
        click.echo(f"rows={len(results)} errors={n_err}")
        if with_warn and "agg_adl" in results["model"].values:
            flow = (
                results[results["model"] == "agg_adl"]
                .set_index("target_week")["warn_eff_sm"]
            )
            gate_by_q[lag_q] = evaluate_gate(results, event_flow=flow)
    report = write_report(
        results_by_q, FORECAST_ROOT / "reports", run_id,
        gate_results=gate_by_q or None,
    )
    click.echo(f"report: {report}")


@main.command("targets")
@click.option("--tail", type=int, default=12)
def show_targets(tail: int) -> None:
    """Print recent advance prints and implied factors."""
    import pandas as pd

    vint_dir = DATA_DIR / "vintages"
    targets = pd.read_parquet(vint_dir / "targets.parquet")
    factors = pd.read_parquet(vint_dir / "factors.parquet")
    df = targets.join(factors[["regime", "factor_mult", "factor_add"]])
    click.echo(df.tail(tail).to_string())


if __name__ == "__main__":
    main()
