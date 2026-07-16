"""Markdown backtest report with score tables, DM tests, calibration."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from uiforecast.eval.dm import dm_test
from uiforecast.eval.metrics import interval_coverage


def score_table(results: pd.DataFrame) -> pd.DataFrame:
    ok = results[results["error"].isna() & ~results["is_covid"]]
    rows = []
    for model, grp in ok.groupby("model"):
        pits = grp["pit"].values
        rows.append(
            {
                "model": model,
                "n": len(grp),
                "rmse": np.sqrt(grp["sq_err"].mean()),
                "mae": grp["abs_err"].mean(),
                "crps": grp["crps"].mean(),
                "log_score": grp["log_score"].mean(),
                "bracket_ls": grp["bracket_ls"].mean(),
                "cov50": interval_coverage(pits, 0.5),
                "cov90": interval_coverage(pits, 0.9),
            }
        )
    return pd.DataFrame(rows).set_index("model").sort_values("crps")


def dm_table(results: pd.DataFrame, baseline: str) -> pd.DataFrame:
    ok = results[results["error"].isna() & ~results["is_covid"]]
    piv_sq = ok.pivot_table(index="target_week", columns="model", values="sq_err")
    piv_crps = ok.pivot_table(index="target_week", columns="model", values="crps")
    rows = []
    for model in piv_sq.columns:
        if model == baseline:
            continue
        both_sq = piv_sq[[model, baseline]].dropna()
        both_crps = piv_crps[[model, baseline]].dropna()
        _, p_sq = dm_test(both_sq[model].values, both_sq[baseline].values)
        _, p_crps = dm_test(both_crps[model].values, both_crps[baseline].values)
        rows.append(
            {
                "model": model,
                "vs": baseline,
                "rmse_ratio": float(
                    np.sqrt(both_sq[model].mean() / both_sq[baseline].mean())
                ),
                "dm_p_sq": p_sq,
                "crps_ratio": float(
                    both_crps[model].mean() / both_crps[baseline].mean()
                ),
                "dm_p_crps": p_crps,
            }
        )
    return pd.DataFrame(rows).set_index("model")


def pit_histogram(results: pd.DataFrame, out_path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ok = results[results["error"].isna() & ~results["is_covid"]]
    models = sorted(ok["model"].unique())
    fig, axes = plt.subplots(1, len(models), figsize=(4 * len(models), 3), squeeze=False)
    for ax, model in zip(axes[0], models):
        ax.hist(ok.loc[ok["model"] == model, "pit"], bins=10, range=(0, 1),
                edgecolor="white")
        ax.axhline(len(ok[ok["model"] == model]) / 10, ls="--", c="gray")
        ax.set_title(model)
        ax.set_xlabel("PIT")
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def write_report(
    results_by_lagq: dict[float, pd.DataFrame],
    out_dir: Path,
    run_id: str,
    gate_results: dict | None = None,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"# Backtest report — run `{run_id}`", ""]
    for lag_q, results in sorted(results_by_lagq.items()):
        ok = results[results["error"].isna()]
        n_covid = int(results["is_covid"].sum() and ok["is_covid"].sum())
        lines += [f"## WARN visibility lag quantile q={lag_q}", ""]
        span = (
            f"{ok['target_week'].min():%Y-%m-%d} .. {ok['target_week'].max():%Y-%m-%d}"
            if len(ok)
            else "(no results)"
        )
        lines += [
            f"Origins: {span}; COVID weeks scored separately ({n_covid} rows).", "",
            "### Scores (non-COVID)", "",
            score_table(results).round(3).to_markdown(), "",
        ]
        for base in ("rw_sa", "ar_nsa"):
            if base in results["model"].values:
                lines += [
                    f"### DM tests vs `{base}` (H1: model better; p<0.10 significant)",
                    "",
                    dm_table(results, base).round(4).to_markdown(),
                    "",
                ]
        png = out_dir / f"pit_{run_id}_q{lag_q}.png"
        pit_histogram(results, png)
        lines += [f"![PIT q={lag_q}]({png.name})", ""]

        covid = results[results["error"].isna() & results["is_covid"]]
        if len(covid):
            lines += [
                "### COVID appendix (excluded from headline)", "",
                score_table(covid.assign(is_covid=False)).round(3).to_markdown(), "",
            ]
    if gate_results:
        lines += ["## Gate decision", ""]
        for lag_q, g in sorted(gate_results.items()):
            lines += [f"### q={lag_q}", "", "```", _fmt_gate(g), "```", ""]
        passes = [g["pass"] for g in gate_results.values()]
        ratios_lt_1 = [g["full"]["ratio"] < 1 for g in gate_results.values()]
        sign_stable = all(ratios_lt_1) or not any(ratios_lt_1)
        verdict = all(passes) and sign_stable
        lines += [
            f"**GATE {'PASS' if verdict else 'FAIL'}** — pass at all lag "
            f"quantiles: {all(passes)}; sign stable: {sign_stable}.",
            "",
        ]
    path = out_dir / f"backtest_{run_id}.md"
    path.write_text("\n".join(lines))
    return path


def _fmt_gate(g: dict) -> str:
    out = []
    for key in ("full", "event"):
        if key in g:
            s = g[key]
            out.append(
                f"{key:>6}: n={s['n']:4d} rmse_warn={s['rmse_warn']:.0f} "
                f"rmse_base={s['rmse_base']:.0f} ratio={s['ratio']:.4f} "
                f"dm_p={s['dm_p']:.4f}"
            )
    out.append(f"pass_full={g['pass_full']} pass_event={g['pass_event']} pass={g['pass']}")
    return "\n".join(out)
