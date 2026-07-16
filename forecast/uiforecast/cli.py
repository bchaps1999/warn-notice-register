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
