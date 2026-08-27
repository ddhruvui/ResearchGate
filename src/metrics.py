"""Scoring: direction accuracy and the naive baselines that make it interpretable.

Closeness-to-price is deliberately NOT the headline. "Tomorrow equals today" is
nearly right every day, so a low error says almost nothing. What matters is
whether the model calls DIRECTION better than the free alternatives:

    zero      predict a 0% return          -- the persistence baseline (error only)
    always_up predict up every session     -- exploits the equity drift
    last_dir  repeat the previous session's direction
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats


def _augment(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["actual_dir"] = np.sign(d["actual_return"])
    d["pred_dir"] = np.sign(d["pred_return"])
    d["base_up_dir"] = 1.0
    d["base_last_dir"] = np.sign(d["prev_actual_return"]).replace(0.0, 1.0)
    scored = d["actual_dir"] != 0                     # flat sessions are unscoreable
    d["scored"] = scored
    d["correct"] = (d["pred_dir"] == d["actual_dir"]) & scored
    d["base_up_correct"] = (d["base_up_dir"] == d["actual_dir"]) & scored
    d["base_last_correct"] = (d["base_last_dir"] == d["actual_dir"]) & scored
    return d


def _block(d: pd.DataFrame) -> dict:
    s = d[d["scored"]]
    n = len(s)
    if n == 0:
        return {"n": 0}
    acc = float(s["correct"].mean())
    # Two-sided exact binomial against a fair coin.
    p = float(stats.binomtest(int(s["correct"].sum()), n, 0.5).pvalue)
    return {
        "n": n,
        "direction_accuracy": acc,
        "p_value_vs_coin": p,
        "baseline_always_up": float(s["base_up_correct"].mean()),
        "baseline_last_dir": float(s["base_last_correct"].mean()),
        "edge_vs_always_up": acc - float(s["base_up_correct"].mean()),
        "mse_model": float(((d["pred_return"] - d["actual_return"]) ** 2).mean()),
        "mse_zero_baseline": float((d["actual_return"] ** 2).mean()),
        "mae_model": float((d["pred_return"] - d["actual_return"]).abs().mean()),
        "mae_zero_baseline": float(d["actual_return"].abs().mean()),
        "mean_actual_return": float(d["actual_return"].mean()),
    }


def score(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Return (augmented per-prediction frame, nested metrics dict)."""
    if df.empty:
        return df, {"overall": {"n": 0}}
    d = _augment(df)
    metrics = {"overall": _block(d)}

    d["year"] = pd.DatetimeIndex(d["date"]).year
    metrics["by_year"] = {int(y): _block(g) for y, g in d.groupby("year")}
    metrics["by_ticker"] = {t: _block(g) for t, g in d.groupby("ticker")}
    # Holiday/weekend gaps span more news than a midweek session; scored apart so a
    # calendar artefact is not mistaken for model failure.
    metrics["by_gap_days"] = {int(g_): _block(g) for g_, g in d.groupby("gap_days")}
    return d.drop(columns=["year"]), metrics
