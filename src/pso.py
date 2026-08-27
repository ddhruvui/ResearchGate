"""Particle Swarm Optimisation of the LS-SVM free parameters (paper section III).

Velocity and position updates are the paper's eq 11 and 12, with the standard
Clerc-Kennedy constriction coefficients. Particles live in LOG10 space over
(C, gamma) because both span orders of magnitude.

On the paper's third parameter: it lists C, epsilon and gamma as the tuned set.
Epsilon is the width of Vapnik's insensitive tube, which belongs to standard SVR
and does NOT appear anywhere in the LS-SVM formulation the paper derives (eq 2-6
use squared error). There is nothing for it to control, so it is not tuned here.

Fitness is measured on an inner validation split taken from the END of the
training window — never from the test window. The metric is configurable
(pso.fitness); see `_score` for why the paper's MSE is a poor choice and why
rank IC is the default.
"""
from __future__ import annotations

import numpy as np

from .lssvm import LSSVM


def _rank(a: np.ndarray) -> np.ndarray:
    order = a.argsort()
    r = np.empty(len(a), dtype=float)
    r[order] = np.arange(len(a), dtype=float)
    return r


def _score(pred: np.ndarray, actual: np.ndarray, kind: str) -> float:
    """Lower is better (PSO minimises), so correlation-style scores are negated.

    WHY NOT MSE BY DEFAULT. Expanding E[(p-a)^2] = E[a^2] - 2E[pa] + E[p^2]:
    when signal is weak the cross term is tiny, so the cheapest way to cut MSE is
    to shrink E[p^2] — i.e. predict ~0. Under no signal the MSE-optimal forecast
    IS zero. Measured on the first full backtest: predictions shrank to 21% of
    real move sizes and correlated 0.024 with them, while MSE looked "good" at
    1.05x the zero baseline. PSO optimised exactly what it was asked to.

    IC (correlation) is SCALE-INVARIANT, so shrinking cannot improve it, and it
    is dense — every validation day contributes — unlike directional accuracy,
    which is binary and would have PSO chasing noise over ~252 days.
    """
    if kind == "mse":                                   # the paper's criterion
        return float(np.mean((pred - actual) ** 2))
    if np.std(pred) < 1e-15 or np.std(actual) < 1e-15:
        return np.inf                                   # degenerate: no ranking
    if kind == "ic":
        return -float(np.corrcoef(pred, actual)[0, 1])
    if kind == "rank_ic":
        return -float(np.corrcoef(_rank(pred), _rank(actual))[0, 1])
    if kind == "direction":
        m = actual != 0
        if not m.any():
            return np.inf
        return -float(np.mean(np.sign(pred[m]) == np.sign(actual[m])))
    raise ValueError(f"unknown pso.fitness {kind!r}")


def _fitness(params: np.ndarray, Xtr, ytr, Xva, yva, kernel: str,
             kind: str = "rank_ic") -> float:
    C, gamma = 10.0 ** params[0], 10.0 ** params[1]
    try:
        model = LSSVM(C=C, gamma=gamma, kernel=kernel).fit(Xtr, ytr)
        pred = model.predict(Xva)
    except Exception:
        return np.inf
    if not np.all(np.isfinite(pred)):
        return np.inf
    v = _score(pred, yva, kind)
    return np.inf if not np.isfinite(v) else v


def optimise(X: np.ndarray, y: np.ndarray, cfg: dict,
             seed: int | None = None) -> tuple[float, float, float]:
    """Return (C, gamma, best_fitness) for the given training window."""
    p = cfg["pso"]
    m = cfg["model"]
    rng = np.random.default_rng(seed if seed is not None else p["seed"])

    n = len(y)
    n_va = max(20, int(round(n * p["valid_fraction"])))
    if n <= n_va + 20:                       # too short to tune; fall back to defaults
        return 1.0, 1.0 / max(X.shape[1], 1), float("nan")
    Xtr, ytr = X[:-n_va], y[:-n_va]
    Xva, yva = X[-n_va:], y[-n_va:]

    kind = p.get("fitness", "rank_ic")
    lo = np.array([m["log10_C_bounds"][0], m["log10_gamma_bounds"][0]], dtype=float)
    hi = np.array([m["log10_C_bounds"][1], m["log10_gamma_bounds"][1]], dtype=float)
    span = hi - lo

    size = int(p["swarm_size"])
    pos = rng.uniform(lo, hi, size=(size, 2))
    vel = rng.uniform(-span, span, size=(size, 2)) * 0.1

    pbest = pos.copy()
    pbest_val = np.array([_fitness(x, Xtr, ytr, Xva, yva, m["kernel"], kind) for x in pos])
    g = int(np.argmin(pbest_val))
    gbest, gbest_val = pbest[g].copy(), float(pbest_val[g])

    w, c1, c2 = float(p["inertia"]), float(p["c1"]), float(p["c2"])
    for _ in range(int(p["iterations"])):
        q = rng.random((size, 2))
        r = rng.random((size, 2))
        vel = w * vel + c1 * q * (pbest - pos) + c2 * r * (gbest - pos)   # eq 11
        vel = np.clip(vel, -span * 0.5, span * 0.5)
        pos = np.clip(pos + vel, lo, hi)                                  # eq 12
        vals = np.array([_fitness(x, Xtr, ytr, Xva, yva, m["kernel"], kind) for x in pos])
        better = vals < pbest_val
        pbest[better], pbest_val[better] = pos[better], vals[better]
        g = int(np.argmin(pbest_val))
        if pbest_val[g] < gbest_val:
            gbest, gbest_val = pbest[g].copy(), float(pbest_val[g])

    return float(10.0 ** gbest[0]), float(10.0 ** gbest[1]), gbest_val
