"""LS-SVM, PSO and indicator sanity."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.config import load_config
from src.indicators import (build_features, macd_histogram, money_flow_index,
                            next_session_return, rsi, stochastic_k)
from src.lssvm import LSSVM


def test_lssvm_recovers_a_smooth_function():
    rng = np.random.default_rng(0)
    X = rng.uniform(-2, 2, size=(300, 2))
    y = np.sin(X[:, 0]) + 0.5 * X[:, 1]
    model = LSSVM(C=100.0, gamma=0.5).fit(X, y)
    Xt = rng.uniform(-2, 2, size=(100, 2))
    yt = np.sin(Xt[:, 0]) + 0.5 * Xt[:, 1]
    pred = model.predict(Xt)
    assert np.corrcoef(pred, yt)[0, 1] > 0.95


def test_lssvm_predict_before_fit_raises():
    with pytest.raises(RuntimeError):
        LSSVM().predict(np.zeros((1, 3)))


def test_lssvm_handles_constant_feature_column():
    rng = np.random.default_rng(1)
    X = rng.normal(size=(120, 3)); X[:, 2] = 4.0        # zero-variance column
    y = X[:, 0] * 0.3
    pred = LSSVM(C=10.0, gamma=0.3).fit(X, y).predict(X[:10])
    assert np.all(np.isfinite(pred))


def test_stochastic_k_bounds_and_rsi_bounds():
    n = 200
    rng = np.random.default_rng(2)
    c = pd.Series(100 + np.cumsum(rng.normal(0, 1, n)))
    h, l = c + 1.0, c - 1.0
    k = stochastic_k(h, l, c, 14).dropna()
    assert ((k >= -1e-9) & (k <= 100 + 1e-9)).all()
    r = rsi(c, 14).dropna()
    assert ((r >= -1e-9) & (r <= 100 + 1e-9)).all()


def test_mfi_bounds():
    n = 300
    rng = np.random.default_rng(3)
    c = pd.Series(50 + np.cumsum(rng.normal(0, 0.5, n)))
    h, l = c + 0.5, c - 0.5
    v = pd.Series(rng.uniform(1e5, 1e6, n))
    m = money_flow_index(h, l, c, v, 14).dropna()
    assert ((m >= -1e-9) & (m <= 100 + 1e-9)).all()


def test_indicators_are_causal():
    """Changing a LATER bar must not alter an EARLIER indicator value."""
    n = 400
    rng = np.random.default_rng(4)
    c = pd.Series(80 + np.cumsum(rng.normal(0, 0.7, n)))
    df = pd.DataFrame({"date": pd.bdate_range("2020-01-01", periods=n),
                       "adj_open": c, "adj_high": c + 1, "adj_low": c - 1,
                       "adj_close": c, "adj_volume": rng.uniform(1e5, 1e6, n)})
    cfg = load_config()
    a = build_features(df, cfg)
    d2 = df.copy(); d2.loc[300:, ["adj_open", "adj_high", "adj_low", "adj_close"]] *= 5.0
    b = build_features(d2, cfg)
    cols = [c_ for c_ in a.columns if c_ != "date"]
    pd.testing.assert_frame_equal(a.iloc[:300][cols], b.iloc[:300][cols])


def test_next_session_return_is_forward_by_exactly_one():
    df = pd.DataFrame({"date": pd.bdate_range("2021-01-04", periods=5),
                       "adj_close": [10.0, 11.0, 12.0, 12.0, 6.0]})
    y = next_session_return(df)
    assert y.iloc[0] == pytest.approx(0.1)
    assert y.iloc[3] == pytest.approx(-0.5)
    assert pd.isna(y.iloc[4])


def test_macd_matches_standard_12_26_9():
    """The paper's alphas (0.15/0.075/0.2) ARE spans 12/26/9; confirm the mapping."""
    for span, alpha in ((12, 0.15), (26, 0.075), (9, 0.2)):
        assert abs(2.0 / (span + 1) - alpha) < 0.008
    c = pd.Series(np.linspace(10, 20, 200))
    assert np.isfinite(macd_histogram(c).iloc[-1])


def test_mse_rewards_shrinkage_but_ic_does_not():
    """The reason pso.fitness defaults to rank_ic rather than the paper's MSE.

    Scaling every prediction down by 100x throws away all magnitude information
    while preserving ordering and sign. MSE IMPROVES under that transformation —
    so optimising MSE with weak signal drives the model toward predicting zero.
    Correlation-based scores are scale-invariant and cannot be gamed this way.
    """
    from src.pso import _score
    rng = np.random.default_rng(0)
    actual = rng.normal(0, 0.015, 400)
    pred = 0.3 * actual + rng.normal(0, 0.012, 400)
    shrunk = pred * 0.01

    assert _score(shrunk, actual, "mse") < _score(pred, actual, "mse")
    for kind in ("ic", "rank_ic", "direction"):
        assert _score(shrunk, actual, kind) == pytest.approx(
            _score(pred, actual, kind), rel=1e-9)


def test_score_rejects_degenerate_predictions():
    from src.pso import _score
    actual = np.random.default_rng(1).normal(0, 0.01, 100)
    flat = np.zeros(100)
    for kind in ("ic", "rank_ic"):
        assert not np.isfinite(_score(flat, actual, kind))
