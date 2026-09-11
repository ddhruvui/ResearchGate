"""Per-ticker stop-loss paper trade: what $10,000 would have done following the model.

    python -m src.strategy                 # read latest/, write latest/strategy.*

This is a READER of the run. It never recomputes, regrades or rewrites a
prediction — it takes `predictions.parquet` as given and asks a separate
question: if you had traded each recorded call with a fixed stop, what would the
money have done? Delete everything this module writes and the rest of the
pipeline is unaffected.

THE RULE (one session, one ticker, one stop level `sl`)

    expected = adj_close[D] * (1 + pred_return)        the model's price target
    direction = LONG if pred_return > 0 else SHORT

    The open of session D+1 is the entry. If the open has ALREADY passed the
    target -- open >= expected for a long, open <= expected for a short -- there
    is no trade: the move the model predicted happened overnight, before any
    order could be worked. Capital sits out the session.

    Otherwise entry = adj_open, and the position is closed by the FIRST of:

        stop    long: low  <= entry*(1-sl)      exit at the stop price
                short: high >= entry*(1+sl)
        target  long: high >= expected          exit at `expected`
                short: low  <= expected
        close   neither level traded            exit at adj_close

    Daily bars cannot say which of the stop and the target traded first on a day
    that touched both, so the STOP is assumed to win. That is the conservative
    read and it keeps the stop levels honest: without it a 2% stop could score
    better than an 8% one purely from tie-breaking.

WHY THE ORDER MATTERS. Two earlier readings of the rule were tested on real bars
and both are wrong by construction:

  * "target beaten, so sell at `expected`" applied on a gap-through day books a
    LOSS on a session that moved the predicted way -- you buy at 102 and sell at
    the 101 target. That fires on ~32% of sessions.
  * "exit at the day's low (long) / high (short) when the target is missed"
    assumes you sell at the single worst tick of every session, forever. Also
    ~32% of sessions.

Together they turned $10,000 into $0.04 over 1,428 sessions on a 10-ticker
sample. The rule above leaves the same sample between $6.6k and $12.6k depending
on the stop, which is a result rather than an artefact.

MONEY. Capital compounds per ticker from $10,000 and fractional shares are
allowed, so the whole balance is deployed on every traded session. The balance is
rounded DOWN to whole cents after each session.
"""
from __future__ import annotations

import os

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_v, "1")

import argparse
import io
import json
import math
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .config import Env, load_config, load_tickers
from .data import load_adjusted
from .storage import LayeredSource, ResultStore

# Stop levels the UI offers. Changing this list changes every stored document, so
# publish_mongo and the dashboard read it from the run rather than hard-coding it.
STOPS = (0.02, 0.05, 0.08)
START_CAPITAL = 10_000.00

ROW_COLS = ["date", "ticker", "stop_pct", "direction", "traded", "exit_reason",
            "entry", "target", "stop_price", "exit_price", "shares",
            "pct_change", "capital_before", "capital_after"]


def floor_cents(x: float) -> float:
    """Round money DOWN to whole cents. The 1e-9 absorbs binary-float dust so a
    balance that is exactly 10050.00 in decimal does not floor to 10049.99."""
    return math.floor(x * 100 + 1e-9) / 100


def _max_drawdown(series) -> float:
    peak, dd = float("-inf"), 0.0
    for v in series:
        if v > peak:
            peak = v
        if peak > 0:
            dd = min(dd, v / peak - 1)
    return dd


def simulate(bars: pd.DataFrame, preds: pd.DataFrame, stop: float,
             start: float = START_CAPITAL) -> tuple[list[dict], dict]:
    """Walk one ticker at one stop level. Returns (per-session rows, summary)."""
    idx = {d: i for i, d in enumerate(bars["date"])}
    o_, h_, l_, c_ = (bars[k].to_numpy(dtype=float)
                      for k in ("adj_open", "adj_high", "adj_low", "adj_close"))
    cap = float(start)
    rows: list[dict] = []
    curve: list[float] = []

    for d, pred in zip(preds["date"], preds["pred_return"]):
        i = idx.get(d)
        if not i:                       # unknown session, or the very first bar
            continue                    # (i == 0 has no prior close to target from)
        o, h, l, c = o_[i], h_[i], l_[i], c_[i]
        expected = c_[i - 1] * (1.0 + float(pred))
        long = float(pred) > 0
        before = cap

        # Overnight gap through the target -> the move is already gone. Sit out.
        if (o >= expected) if long else (o <= expected):
            rows.append({"date": d, "stop_pct": stop, "direction": "long" if long else "short",
                         "traded": False, "exit_reason": "gapped_past_target",
                         "entry": o, "target": expected, "stop_price": np.nan,
                         "exit_price": np.nan, "shares": 0.0, "pct_change": 0.0,
                         "capital_before": before, "capital_after": cap})
            curve.append(cap)
            continue

        stop_price = o * (1 - stop) if long else o * (1 + stop)
        if (l <= stop_price) if long else (h >= stop_price):
            exit_price, why = stop_price, "stop"          # stop wins a same-day tie
        elif (h >= expected) if long else (l <= expected):
            exit_price, why = expected, "target"
        else:
            exit_price, why = c, "close"

        shares = cap / o                                  # fractional: all of it works
        pnl = shares * (exit_price - o) if long else shares * (o - exit_price)
        cap = floor_cents(cap + pnl)
        rows.append({"date": d, "stop_pct": stop, "direction": "long" if long else "short",
                     "traded": True, "exit_reason": why, "entry": o, "target": expected,
                     "stop_price": stop_price, "exit_price": exit_price, "shares": shares,
                     "pct_change": (exit_price / o - 1) if long else (1 - exit_price / o),
                     "capital_before": before, "capital_after": cap})
        curve.append(cap)

    traded = [r for r in rows if r["traded"]]
    pcts = np.array([r["pct_change"] for r in traded], dtype=float)
    summary = {
        "stopPct": stop,
        "start": start,
        "end": cap,
        "totalReturn": cap / start - 1 if start else 0.0,
        "nSessions": len(rows),
        "nTraded": len(traded),
        "nSkipped": len(rows) - len(traded),
        "nStopped": sum(1 for r in traded if r["exit_reason"] == "stop"),
        "nTarget": sum(1 for r in traded if r["exit_reason"] == "target"),
        "nClose": sum(1 for r in traded if r["exit_reason"] == "close"),
        "nLong": sum(1 for r in traded if r["direction"] == "long"),
        "nShort": sum(1 for r in traded if r["direction"] == "short"),
        "winRate": float((pcts > 0).mean()) if len(pcts) else None,
        "meanPct": float(pcts.mean()) if len(pcts) else None,
        "bestDay": float(pcts.max()) if len(pcts) else None,
        "worstDay": float(pcts.min()) if len(pcts) else None,
        "maxDrawdown": _max_drawdown(curve),
        "last": {k: rows[-1][k] for k in
                 ("date", "direction", "traded", "exit_reason", "entry", "target",
                  "exit_price", "pct_change", "capital_before", "capital_after")} if rows else None,
    }
    return rows, summary


# ----------------------------------------------------------------------- driver

_ENV = _SRC = None


def _init_worker():
    global _ENV, _SRC
    _ENV = Env.load()
    _SRC = LayeredSource(_ENV)


def _one(ticker: str, cfg: dict, preds: pd.DataFrame, pending: dict | None):
    """Every stop level for one ticker. Returns (rows, per-ticker document)."""
    try:
        if preds.empty:
            return [], {"ticker": ticker, "skipped": "no predictions"}
        bars = load_adjusted(_SRC, ticker, cfg)
        if bars.empty:
            return [], {"ticker": ticker, "skipped": "no bars"}
        bars = bars.copy()
        bars["date"] = pd.to_datetime(bars["date"])

        rows: list[dict] = []
        per_stop: dict[str, dict] = {}
        curves: dict[str, list[float]] = {}
        for s in STOPS:
            r, summ = simulate(bars, preds, s)
            for x in r:
                x["ticker"] = ticker
            rows.extend(r)
            key = f"{s:.2f}"
            per_stop[key] = summ
            curves[key] = [x["capital_after"] for x in r]

        dates = [str(pd.Timestamp(x["date"]).date()) for x in rows[:len(curves[f"{STOPS[0]:.2f}"])]]
        series = [{"d": d, **{f"c{int(s*100)}": curves[f"{s:.2f}"][k] for s in STOPS}}
                  for k, d in enumerate(dates)]

        doc = {"ticker": ticker, "startCapital": START_CAPITAL,
               "stops": [float(s) for s in STOPS], "byStop": per_stop, "series": series}
        if pending:
            # Tomorrow's recorded call has no bar yet, so there is nothing to
            # settle — carry the plan (direction and target) and the balance that
            # would be deployed, per stop level.
            doc["pending"] = {
                "forSession": str(pd.Timestamp(pending["for_session"]).date()),
                "asOf": str(pd.Timestamp(pending["as_of"]).date()),
                "direction": "long" if float(pending["pred_return"]) > 0 else "short",
                "lastClose": float(pending["last_close"]),
                "target": float(pending["implied_close"]),
                "predReturn": float(pending["pred_return"]),
                "capital": {f"{s:.2f}": per_stop[f"{s:.2f}"]["end"] for s in STOPS},
            }
        return rows, doc
    except Exception:
        return [], {"ticker": ticker, "error": traceback.format_exc(limit=4)}


def _rollup(docs: list[dict]) -> dict:
    """Universe-level totals: every ticker funded with START_CAPITAL at once."""
    good = [d for d in docs if "byStop" in d]
    out = {"nTickers": len(good), "startCapital": START_CAPITAL,
           "startTotal": round(START_CAPITAL * len(good), 2), "byStop": {}}
    for s in STOPS:
        k = f"{s:.2f}"
        b = [d["byStop"][k] for d in good]
        end = sum(x["end"] for x in b)
        traded = sum(x["nTraded"] for x in b)
        out["byStop"][k] = {
            "stopPct": s,
            "end": round(end, 2),
            "totalReturn": end / (START_CAPITAL * len(good)) - 1 if good else 0.0,
            "nTraded": traded,
            "nSkipped": sum(x["nSkipped"] for x in b),
            "nStopped": sum(x["nStopped"] for x in b),
            "nTarget": sum(x["nTarget"] for x in b),
            "nClose": sum(x["nClose"] for x in b),
            "winners": sum(1 for x in b if x["end"] > START_CAPITAL),
            "losers": sum(1 for x in b if x["end"] < START_CAPITAL),
            "meanWinRate": float(np.mean([x["winRate"] for x in b if x["winRate"] is not None]))
            if b else None,
            "medianReturn": float(np.median([x["totalReturn"] for x in b])) if b else None,
            "worstDrawdown": float(min(x["maxDrawdown"] for x in b)) if b else None,
        }
    return out


def compute(config: str | None = None, limit: int = 0, workers: int = 0,
            dry_run: bool = False, local_out: str | None = None) -> int:
    """Read latest/, simulate every ticker, write strategy.parquet + strategy.json.

    Callable from merge.py so a sharded rebuild refreshes the paper trade in the
    same pass that publishes it. Returns a process exit code.
    """
    args = argparse.Namespace(config=config, limit=limit, workers=workers,
                              dry_run=dry_run, local_out=local_out)
    cfg = load_config(args.config)
    env = Env.load()
    dst = ResultStore(env)
    base = f"{cfg['output']['prefix']}/{cfg['run_id']}"

    def read(key):
        return dst._s3.get_object(Bucket=dst.bucket, Key=f"{base}/latest/{key}")["Body"].read()

    try:
        preds = pd.read_parquet(io.BytesIO(read("predictions.parquet")),
                                columns=["date", "ticker", "pred_return"])
    except Exception as exc:
        print(f"no latest/predictions.parquet ({exc}); run the pipeline first", file=sys.stderr)
        return 1
    preds["date"] = pd.to_datetime(preds["date"])
    preds = preds.sort_values(["ticker", "date"], kind="stable")

    try:
        ns = pd.read_parquet(io.BytesIO(read("next_session.parquet")))
        pending = {r["ticker"]: r for r in ns.to_dict("records")}
    except Exception:
        pending = {}

    tickers = [t for t in load_tickers(cfg) if t in set(preds["ticker"])]
    if args.limit:
        tickers = tickers[:args.limit]
    by_ticker = {t: g.reset_index(drop=True) for t, g in preds.groupby("ticker")}

    from .run import container_resources
    cpus, total_gb, how = container_resources()
    workers = max(1, min(args.workers or max(1, cpus - 1), cpus, max(1, int(total_gb // 1.5))))
    print(f"read  volume : {env.source_volume}  (READ-ONLY)", flush=True)
    print(f"write volume : {dst.bucket}", flush=True)
    print(f"tickers      : {len(tickers)}   predictions: {len(preds):,}", flush=True)
    print(f"stops        : {', '.join(f'{s:.0%}' for s in STOPS)}   "
          f"start ${START_CAPITAL:,.2f}/ticker", flush=True)
    print(f"resources    : cpus={cpus} ram={total_gb:.1f}GB (via {how}) -> workers={workers}",
          flush=True)

    t0 = time.time()
    all_rows: list[dict] = []
    docs: list[dict] = []

    def collect(res):
        r, d = res
        all_rows.extend(r)
        docs.append(d)

    if workers <= 1:
        _init_worker()
        for k, t in enumerate(tickers, 1):
            collect(_one(t, cfg, by_ticker.get(t, preds.iloc[:0]), pending.get(t)))
            if k % 20 == 0:
                print(f"  [{k}/{len(tickers)}] {time.time()-t0:.0f}s", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker) as pool:
            futs = {pool.submit(_one, t, cfg, by_ticker.get(t, preds.iloc[:0]),
                                pending.get(t)): t for t in tickers}
            for k, f in enumerate(as_completed(futs), 1):
                collect(f.result())
                if k % 20 == 0:
                    print(f"  [{k}/{len(tickers)}] {time.time()-t0:.0f}s", flush=True)

    errs = [d for d in docs if "error" in d]
    for e in errs[:3]:
        print(f"  !! {e['ticker']}: {e['error'].splitlines()[-1]}", file=sys.stderr)
    docs = sorted([d for d in docs if "byStop" in d], key=lambda d: d["ticker"])
    if not docs:
        print("no ticker produced a result", file=sys.stderr)
        return 1

    frame = pd.DataFrame(all_rows)[ROW_COLS].sort_values(["date", "ticker", "stop_pct"])
    frame = frame.reset_index(drop=True)
    roll = _rollup(docs)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    meta = {"stamp": stamp, "rows": int(len(frame)), "tickers": len(docs),
            "errors": len(errs), "stops": [float(s) for s in STOPS],
            "startCapital": START_CAPITAL,
            "rule": "skip on overnight gap through target; stop wins a same-day tie; "
                    "else target; else close. Fractional shares, balance floored to cents.",
            "secs": round(time.time() - t0, 1)}
    payload = {"meta": meta, "rollup": roll, "tickers": docs}

    print(f"\n{len(frame):,} rows over {len(docs)} tickers in {time.time()-t0:.0f}s")
    print(f"{'stop':>5} {'total $':>14} {'return':>9} {'traded':>9} {'stopped':>8} "
          f"{'winners':>8} {'med/ticker':>11}")
    for s in STOPS:
        b = roll["byStop"][f"{s:.2f}"]
        print(f"{s:>5.0%} {b['end']:>14,.2f} {b['totalReturn']:>+8.1%} {b['nTraded']:>9,} "
              f"{b['nStopped']:>8,} {b['winners']:>4}/{roll['nTickers']:<3} "
              f"{b['medianReturn']:>+10.1%}")

    if args.dry_run:
        print("\ndry run — nothing written")
        return 0

    for pre in (f"{base}/latest", f"{base}/strategy/{stamp}"):
        dst.put_dataframe(f"{pre}/strategy.parquet", frame)
        dst.put_json(f"{pre}/strategy.json", payload)
    print(f"\nwrote s3://{dst.bucket}/{base}/latest/strategy.parquet (+ strategy.json)")
    print(f"      s3://{dst.bucket}/{base}/strategy/{stamp}/  (timestamped copy)")

    if args.local_out:
        os.makedirs(args.local_out, exist_ok=True)
        frame.to_parquet(f"{args.local_out}/strategy.parquet", index=False)
        with open(f"{args.local_out}/strategy.json", "w") as fh:
            json.dump(payload, fh, indent=2, default=str)
        print(f"      {args.local_out}/  (local copy)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=int(os.environ.get("WORKERS", "0")))
    ap.add_argument("--dry-run", action="store_true", help="compute but write nothing")
    ap.add_argument("--local-out", default=None, help="also write the parquet/json here")
    a = ap.parse_args()
    return compute(config=a.config, limit=a.limit, workers=a.workers,
                   dry_run=a.dry_run, local_out=a.local_out)


if __name__ == "__main__":
    raise SystemExit(main())
