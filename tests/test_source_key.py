"""data.source_keys lets a ticker's EOD file live at a non-default key on the source volume."""
from __future__ import annotations

from src.data import load_adjusted, source_key

_ROWS = [{"date": "2026-09-14", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5,
          "adjusted_close": 1.5, "volume": 100},
         {"date": "2026-09-15", "open": 1.5, "high": 2.5, "low": 1.0, "close": 2.0,
          "adjusted_close": 2.0, "volume": 200}]


class _Store:
    def __init__(self, blobs):
        self.blobs, self.asked = blobs, []

    def get_json(self, key):
        self.asked.append(key)
        return self.blobs[key]

    def try_get_json(self, key):
        self.asked.append(key)
        return self.blobs.get(key)


def _cfg(**over):
    return {"data": {"source_prefix": "data/ohlcv", "splits_prefix": "data/splits", **over}}


def test_default_key_is_prefix_plus_ticker():
    assert source_key(_cfg(), "AAPL") == "data/ohlcv/AAPL.json"
    assert source_key(_cfg(source_keys=None), "AAPL") == "data/ohlcv/AAPL.json"


def test_override_wins_only_for_the_named_ticker():
    cfg = _cfg(source_keys={"SPY": "data/watchlist/market/SPY.US.json"})
    assert source_key(cfg, "SPY") == "data/watchlist/market/SPY.US.json"
    assert source_key(cfg, "AAPL") == "data/ohlcv/AAPL.json"


def test_load_adjusted_reads_the_override_key():
    cfg = _cfg(source_keys={"SPY": "data/watchlist/market/SPY.US.json"})
    store = _Store({"data/watchlist/market/SPY.US.json": _ROWS})
    df = load_adjusted(store, "SPY", cfg)
    assert len(df) == 2 and str(df["date"].iloc[-1].date()) == "2026-09-15"
    assert store.asked[0] == "data/watchlist/market/SPY.US.json"
    assert "data/ohlcv/SPY.json" not in store.asked
