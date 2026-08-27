"""Next-trading-session lookup.

Knowing WHEN the market is open is public information published years ahead; it is
not a lookahead on price. Used only to label the live prediction with the session it
applies to (e.g. a Friday close predicts TUESDAY when Monday is Labor Day).
"""
from __future__ import annotations

import pandas as pd

from .storage import SourceStore


def holiday_set(source: SourceStore) -> set[pd.Timestamp]:
    try:
        cal = source.get_json("data/calendar/US.json")
    except Exception:
        return set()
    out = set()
    for rec in (cal.get("ExchangeHolidays") or {}).values():
        d = rec.get("Date") if isinstance(rec, dict) else None
        if d:
            out.add(pd.Timestamp(d).normalize())
    return out


def next_session(after: pd.Timestamp, holidays: set[pd.Timestamp]) -> pd.Timestamp:
    """First weekday strictly after `after` that is not an exchange holiday."""
    d = pd.Timestamp(after).normalize() + pd.Timedelta(days=1)
    for _ in range(30):
        if d.weekday() < 5 and d not in holidays:
            return d
        d += pd.Timedelta(days=1)
    return d
