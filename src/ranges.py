"""Per-stock 2-week swing: how far each stock moves from its lowest low to its
highest high over 10 sessions, and how that compares with its own history.

    python -m src.ranges                                # bars -> latest/ranges.json
    python -m src.ranges --dry-run --local-out /tmp/rg  # compute, keep a local copy only

This is a READER of the market data, the way src.strategy is a reader of the run.
It never touches a prediction, and nothing else in the pipeline reads what it
writes: delete its output and the rest is unaffected.

THE NUMBER (one stock, the window ending on session j)

    high[j]  = max(adj_high[j-9 .. j])
    low[j]   = min(adj_low[j-9 .. j])
    range[j] = high[j] / low[j] - 1            "from the bottom to the top"

Adjusted prices, so a split inside the window is not a fake 50% swing. Counted in
sessions, not calendar days, so a holiday week is still 10 sessions.

THE HISTORY (per stock, two periods)

    1y    the windows ending on the stock's last 252 sessions
    all   every window ending on or after backtest.start (2021-01-04), or from the
          stock's first full window if it listed later

Each period carries percentiles, a histogram in 0.5% bins, and where the latest
window ranks. Consecutive windows share 9 of their 10 sessions, so n windows hold
only about n/10 separate 2-week stretches; the page says so, because the tails of
a one-year histogram rest on about 25 of them.

WHY IT IS RECOMPUTED, NOT APPENDED. Every number here is a pure function of the
bars, and the past-year set drops its oldest window every day, so there is nothing
to append to. Recomputing from the bars on every run costs seconds, heals a missed
day by itself, and picks up a corrected vendor bar or a new stock without a
backfill. Graded predictions are append-only because they are a record made in
advance; this is not.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .config import Env, load_config, load_tickers
from .data import load_adjusted
from .storage import LayeredSource, ResultStore

WINDOW = 10                 # sessions in one "2-week" window
YEAR_SESSIONS = 252         # the 1y period: windows ending on the last 252 sessions
BIN = 0.005                 # histogram bin width, as a fraction (0.5%)
QUANTILES = {"p10": 0.10, "p25": 0.25, "p50": 0.50, "p75": 0.75, "p90": 0.90, "p99": 0.99}


def _f(x, dp: int = 6):
    """A JSON-safe float: rounded, and None for NaN/inf so no NaN reaches Mongo or a browser."""
    if x is None:
        return None
    x = float(x)
    return round(x, dp) if math.isfinite(x) else None


def _day(ts) -> str:
    return str(pd.Timestamp(ts).date())


def window_ranges(bars: pd.DataFrame, n: int = WINDOW) -> pd.DataFrame:
    """One row per complete window: the date it ends on and its high, low and range.

    `bars` is load_adjusted's frame (date-sorted). Fewer than `n` bars -> empty.
    """
    if len(bars) < n:
        return pd.DataFrame({"date": pd.Series(dtype="datetime64[ns]"),
                             "high": pd.Series(dtype=float), "low": pd.Series(dtype=float),
                             "range": pd.Series(dtype=float)})
    h = bars["adj_high"].to_numpy(dtype=float)
    lo = bars["adj_low"].to_numpy(dtype=float)
    win = np.lib.stride_tricks.sliding_window_view
    hi_w, lo_w = win(h, n).max(axis=1), win(lo, n).min(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        rng = np.where(lo_w > 0, hi_w / lo_w - 1.0, np.nan)    # NaN is dropped by period_stats
    return pd.DataFrame({"date": pd.to_datetime(bars["date"]).to_numpy()[n - 1:],
                         "high": hi_w, "low": lo_w, "range": rng})


def latest_window(bars: pd.DataFrame, n: int = WINDOW) -> dict | None:
    """The most recent window in full: which session set the high and the low, and
    where the last close sits between them. None with fewer than `n` bars."""
    if len(bars) < n:
        return None
    w = bars.iloc[-n:]
    h = w["adj_high"].to_numpy(dtype=float)
    lo = w["adj_low"].to_numpy(dtype=float)
    dates = pd.to_datetime(w["date"]).to_numpy()
    ih, il = int(np.argmax(h)), int(np.argmin(lo))     # first session to reach it
    high, low = float(h[ih]), float(lo[il])
    close = float(w["adj_close"].iloc[-1])
    return {
        "date": _day(dates[-1]),
        "from": _day(dates[0]),
        "high": _f(high, 4), "highDate": _day(dates[ih]),
        "low": _f(low, 4), "lowDate": _day(dates[il]),
        "close": _f(close, 4),
        "range": _f(high / low - 1.0) if low > 0 else None,     # a broken vendor low
        # 0 = closed at the window's low, 1 = at its high.
        "position": _f((close - low) / (high - low), 4) if high > low else None,
    }


def period_stats(ranges: np.ndarray, ends: np.ndarray, now: float | None) -> dict | None:
    """Percentiles, a histogram and today's rank over one set of windows.

    counts[k] holds the windows with k*BIN <= range < (k+1)*BIN. `below` is the
    share of windows strictly narrower than `now` ("wider than 97% of them").
    """
    ranges = np.asarray(ranges, dtype=float)
    ok = np.isfinite(ranges)
    ranges, ends = ranges[ok], np.asarray(ends)[ok]
    if not len(ranges):
        return None
    idx = np.floor(ranges / BIN + 1e-9).astype(int)
    counts = np.bincount(np.clip(idx, 0, None))
    qs = np.quantile(ranges, list(QUANTILES.values()))
    imax = int(np.argmax(ranges))
    out = {
        "n": int(len(ranges)),
        "since": _day(ends[0]),
        "until": _day(ends[-1]),
        **{k: _f(v) for k, v in zip(QUANTILES, qs)},
        "max": _f(ranges[imax]), "maxDate": _day(ends[imax]),
        "binWidth": BIN,
        "counts": [int(c) for c in counts],
        "below": _f(float((ranges < now).mean()), 4) if now is not None else None,
    }
    return out


def ticker_doc(ticker: str, bars: pd.DataFrame, start: str,
               year_sessions: int = YEAR_SESSIONS) -> dict:
    """Everything the dashboard shows for one stock, from its adjusted bars."""
    now = latest_window(bars)
    if now is None:
        return {"ticker": ticker, "now": None, "periods": {},
                "lastBar": _day(bars["date"].iloc[-1]) if len(bars) else None,
                "note": f"fewer than {WINDOW} sessions"}
    w = window_ranges(bars)
    r, ends = w["range"].to_numpy(), w["date"].to_numpy()
    since = ends >= np.datetime64(pd.Timestamp(start))
    # Rank with the UNROUNDED latest window. It is the last window in `r`, and the
    # rounded copy in `now` can land a hair above it, which would make today
    # "wider than 100%" of a set it belongs to.
    cur = float(r[-1]) if np.isfinite(r[-1]) else None
    periods = {
        "1y": period_stats(r[-year_sessions:], ends[-year_sessions:], cur),
        "all": period_stats(r[since], ends[since], cur),
    }
    return {"ticker": ticker, "now": now, "lastBar": now["date"],
            "periods": {k: v for k, v in periods.items() if v is not None}}


def mark_stale(docs: list[dict]) -> str | None:
    """Flag every stock whose newest bar is older than the newest bar in the universe
    (missing data, a halt), so the page shows it rather than quietly lagging. Returns
    that newest session."""
    dates = [d["lastBar"] for d in docs if d.get("lastBar")]
    if not dates:
        return None
    latest = max(dates)
    for d in docs:
        d["stale"] = bool(d.get("lastBar")) and d["lastBar"] < latest
    return latest


# ----------------------------------------------------------------------- driver

def _one(src, ticker: str, cfg: dict) -> dict:
    try:
        bars = load_adjusted(src, ticker, cfg)
        if bars.empty:
            return {"ticker": ticker, "now": None, "periods": {}, "lastBar": None, "note": "no bars"}
        return ticker_doc(ticker, bars, str(cfg["backtest"]["start"]))
    except Exception:
        return {"ticker": ticker, "error": traceback.format_exc(limit=4)}


def compute(config: str | None = None, limit: int = 0, workers: int = 0,
            dry_run: bool = False, local_out: str | None = None) -> int:
    """Read every stock's bars, write latest/ranges.json. Returns a process exit code.

    Callable from merge.py so a sharded rebuild refreshes the swing table in the
    same pass that publishes it.
    """
    cfg = load_config(config)
    env = Env.load()
    src = LayeredSource(env)                  # boto3 clients are safe to share across threads
    base = f"{cfg['output']['prefix']}/{cfg['run_id']}"
    tickers = load_tickers(cfg)
    if limit:
        tickers = tickers[:limit]
    workers = max(1, workers or 16)           # I/O bound: one S3 GET per stock, then milliseconds

    print(f"read  volume : {env.source_volume}  (READ-ONLY)", flush=True)
    if not dry_run:
        print(f"write to     : {ResultStore(env).url(base)}/latest/ranges.json", flush=True)
    print(f"tickers      : {len(tickers)}   window {WINDOW} sessions   "
          f"periods 1y={YEAR_SESSIONS} sessions, all since {cfg['backtest']['start']}", flush=True)

    t0 = time.time()
    docs: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(_one, src, t, cfg) for t in tickers]
        for k, f in enumerate(as_completed(futs), 1):
            docs.append(f.result())
            if k % 50 == 0:
                print(f"  [{k}/{len(tickers)}] {time.time()-t0:.0f}s", flush=True)

    errs = [d for d in docs if "error" in d]
    for e in errs[:3]:
        print(f"  !! {e['ticker']}: {e['error'].splitlines()[-1]}", file=sys.stderr)
    docs = sorted([d for d in docs if "error" not in d], key=lambda d: d["ticker"])
    if not any(d.get("now") for d in docs):
        print("no stock produced a window", file=sys.stderr)
        return 1
    as_of = mark_stale(docs)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    meta = {"stamp": stamp, "asOf": as_of, "window": WINDOW, "binWidth": BIN,
            "periods": {"1y": {"sessions": YEAR_SESSIONS},
                        "all": {"since": str(cfg["backtest"]["start"])}},
            "tickers": len(docs), "errors": len(errs),
            "stale": [d["ticker"] for d in docs if d.get("stale")],
            "short": [d["ticker"] for d in docs if not d.get("now")],
            "rule": f"range = max(adj_high) / min(adj_low) - 1 over {WINDOW} sessions",
            "secs": round(time.time() - t0, 1)}
    payload = {"meta": meta, "tickers": docs}

    have = [d for d in docs if d.get("now") and "1y" in d["periods"]]
    now = np.array([d["now"]["range"] for d in have])
    wide = sum(1 for d in have if (d["periods"]["1y"]["below"] or 0) >= 0.9)
    print(f"\n{len(have)} stocks over the 10 sessions ending {as_of} in {meta['secs']}s")
    if len(now):
        print(f"median swing now {np.median(now):.1%}; {wide} wider than 90% of their past year")
    if meta["stale"]:
        print(f"stale (last bar before {as_of}): {', '.join(meta['stale'])}")
    if meta["short"]:
        print(f"fewer than {WINDOW} sessions: {', '.join(meta['short'])}")

    if local_out:
        os.makedirs(local_out, exist_ok=True)
        with open(os.path.join(local_out, "ranges.json"), "w") as fh:
            json.dump(payload, fh, indent=2)
        print(f"      {local_out}/ranges.json  (local copy)")
    if dry_run:
        print("dry run — nothing written to the volume")
        return 0

    dst = ResultStore(env)
    for pre in (f"{base}/latest", f"{base}/ranges/{stamp}"):
        dst.put_json(f"{pre}/ranges.json", payload)
    print(f"wrote {dst.url(base)}/latest/ranges.json")
    print(f"      {dst.url(base)}/ranges/{stamp}/  (timestamped copy)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=int(os.environ.get("WORKERS", "0")))
    ap.add_argument("--dry-run", action="store_true", help="compute but write nothing to the volume")
    ap.add_argument("--local-out", default=None, help="also write ranges.json here")
    a = ap.parse_args()
    return compute(config=a.config, limit=a.limit, workers=a.workers,
                   dry_run=a.dry_run, local_out=a.local_out)


if __name__ == "__main__":
    raise SystemExit(main())
