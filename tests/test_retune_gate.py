"""The daily's PSO re-tune gate.

Both halves of this gate failed silently in Sep 2026: the backtest stamped
`pso_tuned_at` while the daily read `tuned_at`, so every ticker looked unstamped
and the quarterly cadence never fired; and the stamp itself was the FIRST tuning
of the backtest (2021) rather than the last, so naming it correctly would have
re-tuned all 105 tickers every single run. Nothing raised either way — the only
symptom was `retuned 0` for ever.
"""
from __future__ import annotations

import pandas as pd

from src.daily import needs_retune, stamped_at

Q3 = pd.Timestamp("2026-09-21")          # same quarter as a 2026-07-01 tuning
Q4 = pd.Timestamp("2026-10-01")          # the quarter turn after it
DIALS = {"C": 100.0, "gamma": 0.25}


def test_reads_the_backtest_stamp_name():
    assert stamped_at({"pso_tuned_at": "2026-07-01"}) == "2026-07-01"


def test_reads_the_daily_stamp_name():
    assert stamped_at({"tuned_at": "2026-07-01"}) == "2026-07-01"


def test_daily_stamp_wins_when_both_are_present():
    assert stamped_at({"tuned_at": "2026-08-01", "pso_tuned_at": "2026-07-01"}) == "2026-08-01"


def test_backtest_stamp_holds_the_quarter_shut():
    """The regression: a `pso_tuned_at`-only ticker must not look unstamped."""
    assert needs_retune({**DIALS, "pso_tuned_at": "2026-07-01"}, "quarterly", Q3) is False


def test_quarter_turn_reopens_it():
    assert needs_retune({**DIALS, "pso_tuned_at": "2026-07-01"}, "quarterly", Q4) is True


def test_missing_dials_always_retune():
    assert needs_retune({"C": None, "gamma": None}, "quarterly", Q3) is True
    assert needs_retune(None, "quarterly", Q3) is True


def test_unstamped_dials_retune_once_rather_than_never():
    assert needs_retune(DIALS, "quarterly", Q3) is True


def test_cadence_once_never_retunes_stamped_dials():
    assert needs_retune({**DIALS, "pso_tuned_at": "2021-01-04"}, "once", Q4) is False


def test_annual_cadence_tracks_the_year_not_the_quarter():
    assert needs_retune({**DIALS, "tuned_at": "2026-01-05"}, "annual", Q4) is False
    assert needs_retune({**DIALS, "tuned_at": "2025-12-31"}, "annual", Q4) is True
