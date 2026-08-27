"""The paper's five technical indicators (eq 13-20) plus the lagged-return input.

Every series here is CAUSAL: the value at row t uses only rows <= t. That property
is what makes the walk-forward loop safe to compute once up front instead of
recomputing inside the loop.

A note on the paper's MACD (eq 19-20). It writes

    MACD        = [0.075 * EMA of close] - [0.15 * EMA of close]
    Signal Line = 0.2  * EMA of MACD

Those coefficients are EMA smoothing constants, not weights: alpha = 2/(n+1) gives
0.0741 for n=26, 0.1538 for n=12 and 0.2 for n=9. So the paper's formulation IS the
standard MACD(12, 26, 9), just written in terms of alpha. Implemented as such.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

FEATURES = ["rsi", "mfi", "ema_ratio", "stoch_k", "macd_hist", "lag_return"]


def wilder_ema(x: pd.Series, period: int) -> pd.Series:
    """Wilder smoothing == EMA with alpha = 1/period."""
    return x.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Eq 13: RSI = 100 - 100/(1+RS), RS = avg gain / avg loss (Wilder)."""
    delta = close.diff()
    gain = wilder_ema(delta.clip(lower=0), period)
    loss = wilder_ema((-delta).clip(lower=0), period)
    rs = gain / loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    return out.where(loss != 0, 100.0)          # no losses in window -> RSI 100


def money_flow_index(high, low, close, volume, period: int = 14) -> pd.Series:
    """Eq 14-16: typical price * volume, split into positive/negative flow."""
    typical = (high + low + close) / 3.0
    flow = typical * volume
    direction = typical.diff()
    pos = flow.where(direction > 0, 0.0).rolling(period, min_periods=period).sum()
    neg = flow.where(direction < 0, 0.0).rolling(period, min_periods=period).sum()
    ratio = pos / neg.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + ratio)
    return out.where(neg != 0, 100.0)


def ema_ratio(close: pd.Series, period: int = 12) -> pd.Series:
    """Eq 17 as a scale-free ratio: close / EMA(close). Raw EMA is a price level and
    would drift with the stock over 26 years; the ratio is stationary."""
    ema = close.ewm(span=period, adjust=False, min_periods=period).mean()
    return close / ema - 1.0


def stochastic_k(high, low, close, period: int = 14) -> pd.Series:
    """Eq 18: %K = (close - lowest low) / (highest high - lowest low) * 100."""
    ll = low.rolling(period, min_periods=period).min()
    hh = high.rolling(period, min_periods=period).max()
    rng = hh - ll
    return ((close - ll) / rng.replace(0.0, np.nan) * 100.0).where(rng != 0, 50.0)


def macd_histogram(close: pd.Series, fast=12, slow=26, signal=9) -> pd.Series:
    """Eq 19-20, scale-free (divided by close). Histogram = MACD - signal."""
    macd = (close.ewm(span=fast, adjust=False).mean()
            - close.ewm(span=slow, adjust=False).mean())
    sig = macd.ewm(span=signal, adjust=False).mean()
    return (macd - sig) / close


def build_features(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Feature matrix indexed the same as `df`. Row t uses only data <= t."""
    f = cfg["features"]
    c, h, l, v = df["adj_close"], df["adj_high"], df["adj_low"], df["adj_volume"]
    out = pd.DataFrame({
        "date": df["date"],
        "rsi": rsi(c, f["rsi_period"]),
        "mfi": money_flow_index(h, l, c, v, f["mfi_period"]),
        "ema_ratio": ema_ratio(c, f["ema_period"]),
        "stoch_k": stochastic_k(h, l, c, f["stoch_period"]),
        "macd_hist": macd_histogram(c, f["macd_fast"], f["macd_slow"], f["macd_signal"]),
        "lag_return": c.pct_change(f["lagged_return"]),
    })
    return out.replace([np.inf, -np.inf], np.nan)


def next_session_return(df: pd.DataFrame) -> pd.Series:
    """y_t = adj_close_{t+1}/adj_close_t - 1, indexed at t.

    This is the ONLY forward-looking operation in the codebase. The walk-forward
    loop is what keeps it honest: to predict session i it may train only on rows
    d <= i-2, because y_{i-1} is the very quantity being predicted.
    """
    return df["adj_close"].shift(-1) / df["adj_close"] - 1.0
