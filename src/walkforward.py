"""The daily predict -> observe -> relearn loop for one ticker.

INDEXING (this is the whole correctness argument, so it is spelled out).

Rows are sessions 0..N-1. Features F[j] use bars <= j. Targets are

    y[j] = adj_close[j+1] / adj_close[j] - 1

i.e. the return REALISED ON session j+1. So predicting y[j]:

    * the input is F[j]            -- known at the close of session j
    * training pairs are (F[d], y[d]) for d <= j-1
      because y[j-1] is the return realised on session j, which IS known once
      session j has closed, while y[j] is precisely what we are predicting.

The assertion in the loop enforces `max(train_idx) <= j-1`. Nothing downstream
can widen that window.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import FEATURES, build_features, next_session_return
from .lssvm import LSSVM
from .pso import optimise


def retune_points(dates, cand, cadence: str) -> set[int]:
    """Indices in `cand` at which PSO should re-tune.

    Always includes the first prediction. `annual`/`quarterly` add a point each
    time the calendar year/quarter turns. Each tuning run is handed the training
    slice for THAT index, which by construction ends at j-1 — so a re-tune can
    never see a session at or after the prediction it will be used for.
    """
    if not cand:
        return set()
    out = {cand[0]}
    if cadence == "once":
        return out
    seen = None
    for j in cand:
        d = dates[j]
        key = d.year if cadence == "annual" else (d.year, (d.month - 1) // 3)
        if seen is None:
            seen = key
        elif key != seen:
            out.add(j)
            seen = key
    return out


def _train_slice(j: int, window: str, window_sessions: int) -> slice:
    """Training rows for predicting y[j]: everything up to and including j-1."""
    hi = j                                    # exclusive -> last usable index is j-1
    lo = 0 if window == "expanding" else max(0, hi - window_sessions)
    return slice(lo, hi)


def run_ticker(ticker: str, bars: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, dict]:
    """Walk one ticker forward. Returns (per-prediction frame, run info)."""
    bt = cfg["backtest"]
    feats = build_features(bars, cfg)
    y_all = next_session_return(bars)

    X = feats[FEATURES].to_numpy(dtype=float)
    y = y_all.to_numpy(dtype=float)
    dates = pd.DatetimeIndex(bars["date"])
    n = len(bars)

    # A row is usable as a TRAINING PAIR only if both its features and its target
    # are finite. Indicator warmup makes the first ~26 rows unusable.
    usable = np.isfinite(X).all(axis=1) & np.isfinite(y)

    start = pd.Timestamp(bt["start"])
    end = pd.Timestamp(bt["end"]) if bt.get("end") else dates[-1]

    # j indexes the TARGET; the prediction is for session dates[j+1].
    j_lo, j_hi = 0, n - 2
    cand = [j for j in range(j_lo, j_hi + 1)
            if start <= dates[j + 1] <= end and usable[j]]

    min_rows = int(bt["min_train_rows"])
    refit_every = max(1, int(bt["refit_every"]))
    window, wsess = bt["window"], int(bt["window_sessions"])

    # --- PSO tuning on a TRAILING window, re-run on the configured cadence ----
    # The slice handed to PSO is the same one the model trains on at that step,
    # so tuning never sees a session at or after the prediction it serves.
    cadence = cfg["pso"].get("retune", "once") if cfg["pso"]["enabled"] else "once"
    points = retune_points(dates, cand, cadence) if cfg["pso"]["enabled"] else set()
    C, gamma = 1.0, 1.0 / max(X.shape[1], 1)
    tunings = []

    rows = []
    model = None
    since_fit = 10**9
    for j in cand:
        sl = _train_slice(j, window, wsess)
        assert sl.stop <= j, f"train window leaks into the target ({sl.stop} > {j})"
        m = usable[sl]
        Xt, yt = X[sl][m], y[sl][m]
        if len(yt) < min_rows:
            continue                                   # staggered entry gate

        if j in points and len(yt) >= min_rows:
            assert sl.stop <= j, "tuning window must end before the target"
            C, gamma, fit = optimise(Xt, yt, cfg, seed=cfg["pso"]["seed"] + len(tunings))
            tunings.append({"date": str(dates[j].date()), "C": C, "gamma": gamma,
                            "n_train": int(len(yt)), "fitness": None if fit != fit else fit})
            model, since_fit = None, 10**9        # dials changed -> refit now

        if model is None or since_fit >= refit_every:
            try:
                model = LSSVM(C=C, gamma=gamma, kernel=cfg["model"]["kernel"]).fit(Xt, yt)
            except Exception:
                model, since_fit = None, 0
                continue
            since_fit = 0
        since_fit += 1

        try:
            pred = float(model.predict(X[j:j + 1])[0])
        except Exception:
            continue
        if not np.isfinite(pred):
            continue

        prev_actual = y[j - 1] if j >= 1 and np.isfinite(y[j - 1]) else 0.0
        rows.append({
            "date": dates[j + 1],                       # session the return is realised on
            "ticker": ticker,
            "pred_return": pred,
            "actual_return": float(y[j]),
            "n_train": int(len(yt)),
            "prev_actual_return": float(prev_actual),
            "gap_days": int((dates[j + 1] - dates[j]).days),
        })

    out = pd.DataFrame(rows)
    if len(out):
        out["source"] = "backtest"          # provenance: replayed, not made live
    info = {"ticker": ticker, "n_predictions": len(out), "C": C, "gamma": gamma,
            "kernel": cfg["model"]["kernel"], "retune": cadence,
            "n_tunings": len(tunings), "tunings": tunings,
            "pso_tuned_at": tunings[0]["date"] if tunings else None,
            "first_date": str(out["date"].min()) if len(out) else None,
            "last_date": str(out["date"].max()) if len(out) else None}
    return out, info
