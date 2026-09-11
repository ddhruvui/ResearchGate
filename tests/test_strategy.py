"""The stop-loss paper trade: every branch of the exit rule, pinned by hand.

Each case is one hand-built bar where the answer is obvious by inspection, so a
regression shows up as a specific wrong branch rather than a drifting total.
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from src.strategy import START_CAPITAL, floor_cents, simulate


def _bars(rows, prev_close=100.0):
    """Build bars for `rows` = [(open, high, low, close), ...].

    Each test session is preceded by an ANCHOR bar closing at `prev_close`, so
    every predicted session prices its target off the same reference and the
    cases stay independent. Without the anchor, session k's close silently
    becomes session k+1's target base and the fixtures drift.

    Returns (frame, dates-of-the-test-sessions).
    """
    out, d, dates = [], pd.Timestamp("2024-01-01"), []
    for o, h, l, c in rows:
        out.append({"date": d, "adj_open": prev_close, "adj_high": prev_close,
                    "adj_low": prev_close, "adj_close": prev_close})
        d += pd.Timedelta(days=1)
        out.append({"date": d, "adj_open": o, "adj_high": h, "adj_low": l, "adj_close": c})
        dates.append(d)
        d += pd.Timedelta(days=1)
    return pd.DataFrame(out), dates


def _preds(dates, pred):
    return pd.DataFrame({"date": dates, "pred_return": [pred] * len(dates)})


def _one(bar, pred, stop=0.05):
    bars, dates = _bars([bar])
    rows, summ = simulate(bars, _preds(dates, pred), stop)
    assert len(rows) == 1
    return rows[0], summ


# --------------------------------------------------------------------- LONG side

def test_long_target_hit_exits_at_the_target_not_the_high():
    # target 101; the day trades up to 105 but the order fills at 101.
    r, _ = _one((100.0, 105.0, 99.0, 104.0), 0.01)
    assert r["exit_reason"] == "target"
    assert r["exit_price"] == pytest.approx(101.0)
    assert r["pct_change"] == pytest.approx(0.01)


def test_long_neither_level_trades_exits_at_the_close():
    # target 101 never touched (high 100.5), stop 95 never touched (low 97).
    r, _ = _one((100.0, 100.5, 97.0, 99.5), 0.01)
    assert r["exit_reason"] == "close"
    assert r["exit_price"] == pytest.approx(99.5)
    assert r["pct_change"] == pytest.approx(-0.005)


def test_long_stop_hit_exits_at_the_stop_price():
    r, _ = _one((100.0, 100.5, 90.0, 91.0), 0.01, stop=0.05)
    assert r["exit_reason"] == "stop"
    assert r["exit_price"] == pytest.approx(95.0)
    assert r["pct_change"] == pytest.approx(-0.05)


def test_long_stop_wins_when_both_levels_trade_the_same_day():
    # High 105 clears the 101 target AND low 90 breaks the 95 stop. Daily bars
    # cannot order them, so the stop is taken.
    r, _ = _one((100.0, 105.0, 90.0, 104.0), 0.01, stop=0.05)
    assert r["exit_reason"] == "stop"
    assert r["pct_change"] == pytest.approx(-0.05)


def test_long_gap_through_target_is_not_traded():
    # Model wanted 101; the session opens at 102, above it. No trade, no P&L.
    r, s = _one((102.0, 106.0, 101.5, 105.0), 0.01)
    assert r["traded"] is False
    assert r["exit_reason"] == "gapped_past_target"
    assert r["pct_change"] == 0.0
    assert r["capital_after"] == r["capital_before"] == START_CAPITAL
    assert s["nTraded"] == 0 and s["nSkipped"] == 1


def test_long_gap_through_target_would_have_lost_money_under_the_naive_rule():
    """Guards the bug this rule exists to avoid: 'high beat the target, sell at
    the target' means buying at 102 and selling at 101 on an UP day."""
    bar = (102.0, 106.0, 101.5, 105.0)
    r, _ = _one(bar, 0.01)
    naive = 101.0 / bar[0] - 1
    assert naive < 0                       # the naive rule loses on a winning day
    assert r["pct_change"] == 0.0          # ours sits the session out


# -------------------------------------------------------------------- SHORT side

def test_short_target_hit_exits_at_the_target():
    # pred -1% -> target 99; the day trades down to 95 but fills at 99.
    r, _ = _one((100.0, 100.5, 95.0, 96.0), -0.01)
    assert r["direction"] == "short"
    assert r["exit_reason"] == "target"
    assert r["exit_price"] == pytest.approx(99.0)
    assert r["pct_change"] == pytest.approx(0.01)


def test_short_neither_level_trades_exits_at_the_close():
    r, _ = _one((100.0, 102.0, 99.5, 101.0), -0.01, stop=0.05)
    assert r["exit_reason"] == "close"
    assert r["pct_change"] == pytest.approx(-0.01)


def test_short_stop_hit_exits_at_the_stop_price():
    r, _ = _one((100.0, 110.0, 99.5, 109.0), -0.01, stop=0.05)
    assert r["exit_reason"] == "stop"
    assert r["exit_price"] == pytest.approx(105.0)
    assert r["pct_change"] == pytest.approx(-0.05)


def test_short_gap_through_target_is_not_traded():
    r, _ = _one((98.0, 98.5, 90.0, 91.0), -0.01)
    assert r["traded"] is False
    assert r["exit_reason"] == "gapped_past_target"


def test_zero_prediction_is_treated_as_a_short():
    r, _ = _one((100.0, 101.0, 99.0, 99.5), 0.0)
    assert r["direction"] == "short"


# ------------------------------------------------------------------------ money

def test_capital_compounds_and_is_floored_to_cents():
    # Two +1% target days at 5%: 10000 -> 10100 -> 10201.
    bars, dates = _bars([(100.0, 105.0, 99.0, 104.0), (100.0, 105.0, 99.0, 104.0)])
    rows, summ = simulate(bars, _preds(dates, 0.01), 0.05)
    assert rows[0]["capital_after"] == pytest.approx(10_100.00)
    assert rows[1]["capital_after"] == pytest.approx(10_201.00)
    assert summ["end"] == pytest.approx(10_201.00)
    assert summ["totalReturn"] == pytest.approx(0.0201)
    # every balance is a whole number of cents
    assert all(abs(r["capital_after"] * 100 - round(r["capital_after"] * 100)) < 1e-6
               for r in rows)


def test_floor_cents_rounds_down_and_survives_binary_dust():
    assert floor_cents(10_050.999) == 10_050.99
    assert floor_cents(10_050.001) == 10_050.00
    assert floor_cents(0.1 + 0.2) == 0.30          # 0.30000000000000004 must not floor to 0.29
    assert floor_cents(-1.234) == -1.24            # losses floor away from zero too


def test_fractional_shares_deploy_the_whole_balance():
    r, _ = _one((3.0, 4.0, 2.9, 3.9), 0.01)        # $10k / $3 is not a whole share count
    assert r["shares"] == pytest.approx(START_CAPITAL / 3.0)
    assert r["shares"] * r["entry"] == pytest.approx(START_CAPITAL)


def test_on_a_day_that_stops_out_the_tighter_stop_loses_less():
    bar = (100.0, 100.5, 88.0, 89.0)               # breaks all three stops
    p2, p5, p8 = [_one(bar, 0.01, stop=s)[0]["pct_change"] for s in (0.02, 0.05, 0.08)]
    assert (p2, p5, p8) == pytest.approx((-0.02, -0.05, -0.08))
    assert p2 > p5 > p8                            # each level exits exactly where set


def test_summary_counts_add_up():
    bars, dates = _bars([(100.0, 105.0, 99.0, 104.0),   # target
                         (100.0, 100.5, 90.0, 91.0),    # stop
                         (100.0, 100.5, 99.0, 99.8),    # close
                         (102.0, 106.0, 101.5, 105.0)]) # gapped
    rows, s = simulate(bars, _preds(dates, 0.01), 0.05)
    assert s["nSessions"] == 4
    assert s["nTraded"] == 3 and s["nSkipped"] == 1
    assert (s["nStopped"], s["nTarget"], s["nClose"]) == (1, 1, 1)
    assert s["nTraded"] == s["nStopped"] + s["nTarget"] + s["nClose"]


def test_the_first_bar_has_no_prior_close_so_it_is_never_traded():
    bars, _ = _bars([(100.0, 105.0, 99.0, 104.0)])
    preds = pd.DataFrame({"date": [bars["date"].iloc[0]], "pred_return": [0.01]})
    rows, _ = simulate(bars, preds, 0.05)
    assert rows == []


def test_predictions_for_unknown_sessions_are_ignored():
    bars, _ = _bars([(100.0, 105.0, 99.0, 104.0)])
    preds = pd.DataFrame({"date": [pd.Timestamp("2030-06-01")], "pred_return": [0.01]})
    rows, _ = simulate(bars, preds, 0.05)
    assert rows == []
