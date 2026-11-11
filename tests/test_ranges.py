"""The 2-week swing table: the window arithmetic, the history, and the edge cases
the dashboard has to show honestly (short histories, stale bars, splits)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.data import load_adjusted
from src.ranges import (BIN, WINDOW, latest_window, mark_stale, period_stats, ticker_doc,
                        window_ranges)


def _bars(highs, lows, closes=None, start="2024-01-01"):
    """Business-day bars from explicit highs and lows (open and close sit between)."""
    n = len(highs)
    closes = closes if closes is not None else [(h + l) / 2 for h, l in zip(highs, lows)]
    return pd.DataFrame({
        "date": pd.bdate_range(start, periods=n),
        "adj_open": closes, "adj_high": highs, "adj_low": lows, "adj_close": closes,
    })


def test_window_takes_the_highest_high_and_lowest_low_of_ten_sessions():
    highs = [101, 102, 110, 103, 104, 105, 106, 107, 108, 109]
    lows = [99, 98, 97, 80, 99, 99, 99, 99, 99, 99]
    w = window_ranges(_bars(highs, lows))
    assert len(w) == 1
    assert w["high"].iloc[0] == 110 and w["low"].iloc[0] == 80
    assert w["range"].iloc[0] == pytest.approx(110 / 80 - 1)


def test_one_window_per_session_once_ten_are_in():
    b = _bars([100 + i for i in range(25)], [90 + i for i in range(25)])
    w = window_ranges(b)
    assert len(w) == 25 - WINDOW + 1
    assert w["date"].iloc[0] == b["date"].iloc[WINDOW - 1]      # ends on the 10th bar
    assert w["date"].iloc[-1] == b["date"].iloc[-1]


def test_latest_window_names_the_sessions_that_set_the_high_and_the_low():
    highs = [100, 100, 100, 120, 100, 100, 100, 100, 100, 100, 100, 100]
    lows = [95, 50, 95, 95, 95, 95, 95, 90, 95, 95, 95, 95]   # the 50 is outside the last 10
    b = _bars(highs, lows, closes=[96] * 11 + [115])
    now = latest_window(b)
    dates = [str(d.date()) for d in b["date"]]
    assert now["high"] == 120 and now["highDate"] == dates[3]
    assert now["low"] == 90 and now["lowDate"] == dates[7]
    assert now["range"] == pytest.approx(120 / 90 - 1)
    assert now["position"] == pytest.approx((115 - 90) / (120 - 90), abs=1e-4)
    assert now["from"] == dates[2] and now["date"] == dates[-1]


def test_fewer_than_ten_sessions_is_a_row_without_numbers():
    d = ticker_doc("NEW", _bars([10] * 9, [9] * 9), start="2021-01-04")
    assert d["now"] is None and d["periods"] == {}
    assert d["note"] == f"fewer than {WINDOW} sessions"
    assert d["lastBar"] is not None                 # still dated, so staleness can be judged


def test_period_counts_every_window_once_and_ranks_today_strictly():
    r = np.array([0.010, 0.012, 0.031, 0.049, 0.050, 0.051, 0.20])
    ends = pd.bdate_range("2024-01-01", periods=len(r)).to_numpy()
    s = period_stats(r, ends, now=0.050)
    assert s["n"] == 7 and sum(s["counts"]) == 7
    assert s["binWidth"] == BIN
    # 0.050 is exactly a bin edge: it belongs to the bin it starts, not the one below.
    assert s["counts"][int(round(0.050 / BIN))] == 2         # 0.050 and 0.051
    assert s["counts"][int(round(0.045 / BIN))] == 1         # 0.049
    assert len(s["counts"]) == int(round(0.20 / BIN)) + 1    # up to the widest, no padding
    assert s["below"] == pytest.approx(4 / 7, abs=1e-4)                # strictly narrower than now
    assert s["max"] == pytest.approx(0.20) and s["maxDate"] == str(pd.Timestamp(ends[-1]).date())
    assert s["p50"] == pytest.approx(0.049)
    assert s["p10"] <= s["p25"] <= s["p50"] <= s["p75"] <= s["p90"] <= s["p99"]


def test_past_year_is_the_last_252_windows_and_all_starts_at_the_backtest():
    n = 400
    b = _bars([100.0] * n, [99.0] * n, start="2020-06-01")
    b.loc[n - 1, "adj_high"] = 130.0                  # today's window is the widest ever
    d = ticker_doc("X", b, start="2021-01-04", year_sessions=252)
    y, a = d["periods"]["1y"], d["periods"]["all"]
    assert y["n"] == 252
    assert y["until"] == str(b["date"].iloc[-1].date())
    assert a["since"] >= "2021-01-04"
    assert a["n"] == int((window_ranges(b)["date"] >= "2021-01-04").sum())
    assert y["below"] == pytest.approx(251 / 252, abs=1e-4)      # wider than every other window


def test_today_never_ranks_wider_than_itself_after_rounding():
    # 1.1234567 / 1 - 1 rounds UP to 0.123457 at 6 dp; ranking with the rounded copy
    # would count today's own window as narrower and report "wider than 100%".
    n = 300
    b = _bars([1.0] * n, [1.0] * n)
    b.loc[n - 1, "adj_high"] = 1.1234567
    d = ticker_doc("X", b, start="2021-01-04")
    assert d["now"]["range"] > 0.1234567                       # the rounded copy is above
    assert d["periods"]["1y"]["below"] == pytest.approx(251 / 252, abs=1e-4)
    assert d["periods"]["1y"]["below"] < 1


def test_a_late_listing_counts_all_from_its_first_full_window():
    b = _bars([100.0] * 60, [99.0] * 60, start="2023-09-14")
    a = ticker_doc("ARM", b, start="2021-01-04")["periods"]["all"]
    assert a["since"] == str(b["date"].iloc[WINDOW - 1].date())
    assert a["n"] == 60 - WINDOW + 1


def test_a_stock_whose_bars_stop_early_is_marked_stale():
    docs = [{"ticker": "A", "lastBar": "2026-11-10"}, {"ticker": "B", "lastBar": "2026-11-06"},
            {"ticker": "C", "lastBar": None}]
    assert mark_stale(docs) == "2026-11-10"
    assert [d["stale"] for d in docs] == [False, True, False]


def test_no_nan_reaches_the_document():
    b = _bars([100.0] * 30, [99.0] * 30)
    b.loc[29, "adj_low"] = 0.0                       # a broken vendor low: range is infinite
    d = ticker_doc("BAD", b, start="2021-01-04")
    assert d["now"]["range"] is None
    for p in d["periods"].values():
        assert all(v is None or np.isfinite(v) for k, v in p.items()
                   if isinstance(v, float))


class _Store:
    """SourceStore stand-in serving one ticker's raw vendor rows."""
    def __init__(self, rows):
        self.rows = rows

    def get_json(self, key):
        return self.rows

    def try_get_json(self, key):
        return None


def test_a_split_inside_the_window_is_not_a_swing():
    # 2:1 split after session 5: raw prices halve, adjusted_close is continuous.
    rows = []
    for i, day in enumerate(pd.bdate_range("2026-10-26", periods=10)):
        raw = 200.0 if i < 5 else 100.0
        adj = 100.0
        rows.append({"date": str(day.date()), "open": raw, "high": raw * 1.01, "low": raw * 0.99,
                     "close": raw, "adjusted_close": adj, "volume": 1000})
    cfg = {"data": {"source_prefix": "data/ohlcv", "splits_prefix": "data/splits"}}
    bars = load_adjusted(_Store(rows), "SPLT", cfg)
    now = latest_window(bars)
    assert now["range"] == pytest.approx(1.01 / 0.99 - 1)    # 2%, not 100%


# ------------------------------------------------------------------ publishing

def test_publisher_makes_one_replaceable_doc_per_stock_and_keeps_the_meta():
    from src.publish_mongo import ranges_docs
    payload = {"meta": {"asOf": "2026-11-10"},
               "tickers": [{"ticker": "AAPL", "now": {"range": 0.09}}, {"ticker": "MS", "now": None},
                           {"now": {}}]}
    docs = ranges_docs(payload, "pso_lssvm_v1")
    assert [d["_id"] for d in docs] == ["pso_lssvm_v1:AAPL", "pso_lssvm_v1:MS"]
    assert all(d["run_id"] == "pso_lssvm_v1" for d in docs)
    assert ranges_docs(None, "r") == [] and ranges_docs({"tickers": None}, "r") == []
