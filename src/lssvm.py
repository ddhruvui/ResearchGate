"""Least Squares Support Vector Machine (paper section II, eq 1-10).

LS-SVM replaces the SVM's inequality constraints with equalities and its hinge loss
with squared error, which collapses the quadratic program to ONE linear system
(the paper's eq 6):

    [ 0    1^T     ] [ b ]   [ 0 ]
    [ 1    K + gI  ] [ a ] = [ y ]        with  g = 1/C

Solve once, predict with f(x) = sum_i a_i K(x, x_i) + b.

Kernel choice. The paper says "In this work, MLP kernel is used" (eq 10,
tanh(k x'z + th)). That kernel is NOT positive semi-definite for general (k, th),
so the system above is not guaranteed to be well posed. RBF (eq 9) is the default
here; set model.kernel: mlp for paper fidelity. See README.
"""
from __future__ import annotations

import numpy as np


def rbf_kernel(A: np.ndarray, B: np.ndarray, gamma: float) -> np.ndarray:
    a2 = np.einsum("ij,ij->i", A, A)[:, None]
    b2 = np.einsum("ij,ij->i", B, B)[None, :]
    d2 = np.maximum(a2 + b2 - 2.0 * (A @ B.T), 0.0)
    return np.exp(-gamma * d2)


def mlp_kernel(A: np.ndarray, B: np.ndarray, gamma: float, theta: float = -1.0) -> np.ndarray:
    return np.tanh(gamma * (A @ B.T) + theta)


class LSSVM:
    """LS-SVM regressor with feature standardisation fitted on TRAIN data only."""

    def __init__(self, C: float = 1.0, gamma: float = 0.1, kernel: str = "rbf",
                 theta: float = -1.0):
        self.C = float(C)
        self.gamma = float(gamma)
        self.kernel = kernel
        self.theta = float(theta)
        self._fitted = False

    # -- kernel dispatch -------------------------------------------------
    def _K(self, A: np.ndarray, B: np.ndarray) -> np.ndarray:
        if self.kernel == "rbf":
            return rbf_kernel(A, B, self.gamma)
        if self.kernel == "mlp":
            return mlp_kernel(A, B, self.gamma, self.theta)
        raise ValueError(f"unknown kernel {self.kernel!r}")

    # -- fit / predict ---------------------------------------------------
    def fit(self, X: np.ndarray, y: np.ndarray) -> "LSSVM":
        X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=float).ravel()
        if X.ndim != 2 or len(X) != len(y):
            raise ValueError("X must be 2-D and aligned with y")

        # Standardise on the training window only. Zero-variance columns get
        # scale 1 so they collapse to a constant rather than exploding.
        self._mu = X.mean(axis=0)
        sd = X.std(axis=0)
        self._sd = np.where(sd > 1e-12, sd, 1.0)
        Xs = (X - self._mu) / self._sd

        # Targets are daily returns (~1e-2). Scaling them to unit variance keeps
        # the regularisation path comparable across tickers.
        self._ymu = y.mean()
        ysd = y.std()
        self._ysd = ysd if ysd > 1e-12 else 1.0
        ys = (y - self._ymu) / self._ysd

        n = len(ys)
        K = self._K(Xs, Xs)
        A = np.empty((n + 1, n + 1), dtype=float)
        A[0, 0] = 0.0
        A[0, 1:] = 1.0
        A[1:, 0] = 1.0
        A[1:, 1:] = K + np.eye(n) / self.C
        rhs = np.empty(n + 1, dtype=float)
        rhs[0] = 0.0
        rhs[1:] = ys

        # numpy's LU (LAPACK gesv) rather than scipy.linalg.solve: the bordered
        # matrix is symmetric INDEFINITE, and scipy's symmetric path (sysv) is
        # ~80x slower here for a result identical to 1e-14 — it links a slower
        # LAPACK than numpy on some platforms. Measured 8.4 s vs 0.11 s at n=1260.
        try:
            sol = np.linalg.solve(A, rhs)
        except np.linalg.LinAlgError:
            # Singular systems (degenerate windows, non-PSD mlp kernel) land here.
            sol = np.linalg.lstsq(A, rhs, rcond=None)[0]
        if not np.all(np.isfinite(sol)):
            sol = np.linalg.lstsq(A, rhs, rcond=None)[0]

        self._b = float(sol[0])
        self._alpha = sol[1:]
        self._Xtr = Xs
        self._fitted = True
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        if not self._fitted:
            raise RuntimeError("LSSVM.predict called before fit")
        X = np.atleast_2d(np.asarray(X, dtype=float))
        Xs = (X - self._mu) / self._sd
        pred = self._K(Xs, self._Xtr) @ self._alpha + self._b
        return pred * self._ysd + self._ymu
