"""The load-bearing test: no future information may reach a prediction.

Two independent checks:

  1. Slice arithmetic — the training window for target j must end at j-1.
  2. Future perturbation — corrupt every bar AFTER a cut date, re-run, and every
     prediction dated on or before the cut must be bit-identical. If any future
     value could reach the model this fails loudly.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.config import load_config
from src.walkforward import _train_slice, run_ticker


def _synthetic(n=1400, seed=0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2016-01-04", periods=n)
    ret = rng.normal(0.0004, 0.015, n)
    close = 50.0 * np.exp(np.cumsum(ret))
    high = close * (1 + np.abs(rng.normal(0, 0.006, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.006, n)))
    return pd.DataFrame({
        "date": dates, "adj_open": close * (1 + rng.normal(0, 0.003, n)),
        "adj_high": np.maximum(high, close), "adj_low": np.minimum(low, close),
        "adj_close": close, "adj_volume": rng.uniform(1e6, 9e6, n),
    })


def _cfg():
    c = load_config()
    c["pso"]["enabled"] = False          # deterministic and fast
    c["backtest"]["start"] = "2020-01-02"
    c["backtest"]["window"] = "rolling"
    c["backtest"]["window_sessions"] = 400
    c["backtest"]["min_train_rows"] = 200
    return c


def test_train_slice_stops_before_target():
    for j in (250, 900, 1399):
        for window, w in (("rolling", 400), ("expanding", 400)):
            sl = _train_slice(j, window, w)
            assert sl.stop <= j, "training window must end strictly before the target"
            assert sl.start >= 0


def test_future_perturbation_cannot_change_past_predictions():
    cfg = _cfg()
    bars = _synthetic()
    base, _ = run_ticker("TEST", bars, cfg)
    assert len(base) > 100, "fixture should produce a meaningful number of predictions"

    cut = base["date"].iloc[len(base) // 2]

    poisoned = bars.copy()
    fut = poisoned["date"] > cut
    assert fut.sum() > 50
    for col in ("adj_open", "adj_high", "adj_low", "adj_close"):
        poisoned.loc[fut, col] *= 3.7          # violent, unmistakable corruption
    poisoned.loc[fut, "adj_volume"] *= 11.0

    after, _ = run_ticker("TEST", poisoned, cfg)

    a = base[base["date"] <= cut].reset_index(drop=True)
    b = after[after["date"] <= cut].reset_index(drop=True)
    assert len(a) == len(b) and len(a) > 0
    pd.testing.assert_series_equal(a["pred_return"], b["pred_return"], check_exact=False,
                                   rtol=1e-12, atol=1e-14)


def test_actual_return_matches_the_labelled_session():
    """The row dated D must carry the return realised ON D, not the day before."""
    cfg = _cfg()
    bars = _synthetic()
    preds, _ = run_ticker("TEST", bars, cfg)
    close = bars.set_index("date")["adj_close"]
    idx = {d: i for i, d in enumerate(bars["date"])}
    for _, r in preds.sample(25, random_state=1).iterrows():
        i = idx[r["date"]]
        expect = close.iloc[i] / close.iloc[i - 1] - 1
        assert r["actual_return"] == pytest.approx(expect, rel=1e-12)
