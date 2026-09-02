"""Incremental daily run: grade yesterday's stored guess, learn, guess again.

    python -m src.daily

The contract, in order, per ticker:

  1. GRADE   the prediction already written to next_session.parquet, against the
             bar that has since landed. The stored guess is graded as-is — never
             recomputed — so the log records what the model actually said at the
             time it said it. A graded verdict is LOCKED: re-running never
             revises it, even if the vendor later restates the price.
  2. LEARN   the new bar joins the trailing window (config: rolling 1,260).
  3. GUESS   refit on that window and write a fresh prediction for the next
             session, re-tuning PSO if a quarter boundary has passed (on trailing
             data only).

Nothing replays the past. One fit per ticker, plus a tuning run on quarter turns.

Idempotent: a (ticker, date) already present is never appended twice, so running
it twice in a day is a no-op. If the source has no new bar yet, it exits 0 having
changed nothing.
"""
from __future__ import annotations

import os

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_v, "1")

import argparse
import io
import json
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .calendar_util import holiday_set, next_session
from .config import Env, load_config, load_tickers
from .data import load_adjusted
from .indicators import FEATURES, build_features, next_session_return
from .lssvm import LSSVM
from .metrics import score
from .pso import optimise
from .storage import LayeredSource, ResultStore
from .walkforward import _train_slice

RAW_COLS = ["date", "ticker", "pred_return", "actual_return", "n_train",
            "prev_actual_return", "gap_days", "source"]

_ENV = _SRC = None


def _init_worker():
    global _ENV, _SRC
    _ENV = Env.load()
    _SRC = LayeredSource(_ENV)


def _rss_mb() -> float:
    """Resident set size of this process, MB. Used to localise the OOM that
    SIGKILLed a 3814 MB pod during the per-ticker loop."""
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except Exception:
        pass
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)
    except Exception:
        return float("nan")


def _quarter(d: pd.Timestamp) -> tuple[int, int]:
    return (d.year, (d.month - 1) // 3)


def _one(ticker: str, cfg: dict, stored: dict | None, graded_dates: set, dials: dict | None):
    """Grade + learn + guess for one ticker. Returns (graded_row, live_row, info)."""
    try:
        bars = load_adjusted(_SRC, ticker, cfg)
        bt = cfg["backtest"]
        if bars.empty or len(bars) < int(bt["min_train_rows"]) + 40:
            return None, None, {"ticker": ticker, "skipped": "insufficient history"}

        dates = pd.DatetimeIndex(bars["date"])
        by = {d: i for i, d in enumerate(dates)}
        feats = build_features(bars, cfg)
        X = feats[FEATURES].to_numpy(dtype=float)
        y = next_session_return(bars).to_numpy(dtype=float)
        usable = np.isfinite(X).all(axis=1) & np.isfinite(y)

        # ---------- 1. GRADE the stored guess ----------
        graded = None
        if stored:
            target = pd.Timestamp(stored["for_session"])
            asof = pd.Timestamp(stored["as_of"])
            if target in by and asof in by and (ticker, str(target.date())) not in graded_dates:
                i_t, i_a = by[target], by[asof]
                if i_t == i_a + 1:                     # must be consecutive sessions
                    c_t = float(bars["adj_close"].iloc[i_t])
                    c_a = float(bars["adj_close"].iloc[i_a])
                    prev = float(y[i_a - 1]) if i_a >= 1 and np.isfinite(y[i_a - 1]) else 0.0
                    graded = {
                        "date": target, "ticker": ticker,
                        "pred_return": float(stored["pred_return"]),
                        "actual_return": c_t / c_a - 1.0,
                        "n_train": int(stored.get("n_train") or 0),
                        "prev_actual_return": prev,
                        "gap_days": int((target - asof).days),
                        "source": "live",             # a real forecast, not a replay
                    }

        # ---------- 2/3. LEARN + GUESS ----------
        j = len(bars) - 1
        if not np.isfinite(X[j]).all():
            return graded, None, {"ticker": ticker, "note": "features not ready"}
        sl = _train_slice(j, bt["window"], int(bt["window_sessions"]))
        assert sl.stop <= j, "training window must end before the bar being predicted"
        m = usable[sl]
        Xt, yt = X[sl][m], y[sl][m]
        if len(yt) < int(bt["min_train_rows"]):
            return graded, None, {"ticker": ticker, "skipped": "below min_train_rows"}

        C = (dials or {}).get("C")
        gamma = (dials or {}).get("gamma")
        tuned_at = (dials or {}).get("tuned_at")
        retuned = False
        cadence = cfg["pso"].get("retune", "once") if cfg["pso"]["enabled"] else "once"
        need = C is None or gamma is None
        if not need and cadence in ("annual", "quarterly") and tuned_at:
            last = pd.Timestamp(tuned_at)
            need = (dates[j].year != last.year) if cadence == "annual" \
                else (_quarter(dates[j]) != _quarter(last))
        if need and cfg["pso"]["enabled"]:
            # Tuned on the SAME trailing slice the model trains on — data prior only.
            C, gamma, _ = optimise(Xt, yt, cfg)
            tuned_at = str(dates[j].date())
            retuned = True
        if C is None:
            C, gamma = 1.0, 1.0 / max(X.shape[1], 1)

        model = LSSVM(C=C, gamma=gamma, kernel=cfg["model"]["kernel"]).fit(Xt, yt)
        pred = float(model.predict(X[j:j + 1])[0])
        if not np.isfinite(pred):
            return graded, None, {"ticker": ticker, "note": "non-finite prediction"}

        last_close = float(bars["adj_close"].iloc[j])
        live = {"ticker": ticker, "as_of": dates[j], "last_close": last_close,
                "pred_return": pred, "implied_close": last_close * (1 + pred),
                "n_train": int(len(yt))}
        info = {"ticker": ticker, "C": C, "gamma": gamma, "tuned_at": tuned_at,
                "retuned": retuned, "graded": graded is not None,
                "as_of": str(dates[j].date())}
        return graded, live, info
    except Exception:
        return None, None, {"ticker": ticker, "error": traceback.format_exc(limit=4)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=int(os.environ.get("WORKERS", "0")))
    ap.add_argument("--dry-run", action="store_true", help="compute but write nothing")
    args = ap.parse_args()

    cfg = load_config(args.config)
    env = Env.load()
    src, dst = LayeredSource(env), ResultStore(env)
    tickers = load_tickers(cfg)
    if args.limit:
        tickers = tickers[:args.limit]

    run_id = cfg["run_id"]
    base = f"{cfg['output']['prefix']}/{run_id}"

    def read(key):
        return dst._s3.get_object(Bucket=dst.bucket, Key=f"{base}/latest/{key}")["Body"].read()

    # ---- existing state ----
    try:
        # Column-select on read: the stored frame has 16 columns (8 raw + 8 derived
        # by score()), and the derived ones are recomputed anyway. Halves peak RSS,
        # which matters — a 2-vCPU pod SIGKILLed (exit 137) loading the full frame.
        buf = io.BytesIO(read("predictions.parquet"))
        try:
            hist = pd.read_parquet(buf, columns=RAW_COLS)
        except Exception:
            buf.seek(0)
            hist = pd.read_parquet(buf)           # older writes lack `source`
    except Exception as exc:
        print(f"no prior predictions.parquet ({exc}); run a full backtest first",
              file=sys.stderr)
        return 1
    if "source" not in hist.columns:
        hist["source"] = "backtest"
    hist["date"] = pd.to_datetime(hist["date"])
    graded_dates = {(t, d.strftime("%Y-%m-%d")) for t, d in zip(hist["ticker"], hist["date"])}

    try:
        prev_live = pd.read_parquet(io.BytesIO(read("next_session.parquet")))
        stored = {r["ticker"]: r for r in prev_live.to_dict("records")}
    except Exception:
        stored = {}
        print("no stored next_session.parquet — nothing to grade this run", flush=True)

    try:
        dials = {r["ticker"]: r for r in json.loads(read("ticker_info.json"))
                 if isinstance(r, dict) and "ticker" in r}
    except Exception:
        dials = {}

    # A stored guess must be written ONCE and never regenerated. If the source has
    # no bar past the one the stored guesses were computed from, this run has
    # nothing to do — re-predicting would silently replace yesterday's recorded
    # forecast with a new one (a restated bar makes them differ), and tomorrow we
    # would grade something the model never actually said.
    if stored:
        stored_asof = max(pd.Timestamp(r["as_of"]) for r in stored.values())
        # Probe a few tickers, not one: a staged ext ticker whose refresh hasn't
        # run yet must not make the whole universe look stale.
        latest_bar = None
        for pt in {tickers[0], tickers[len(tickers) // 2], tickers[-1]}:
            probe = load_adjusted(src, pt, cfg)
            if not probe.empty:
                d = pd.Timestamp(probe["date"].iloc[-1])
                latest_bar = d if latest_bar is None else max(latest_bar, d)
        if latest_bar is not None:
            if latest_bar <= stored_asof:
                print(f"source latest bar {latest_bar.date()} is not past the stored "
                      f"guess as-of {stored_asof.date()} — nothing to do.", flush=True)
                print("Exiting clean; the recorded forecast is left untouched.", flush=True)
                return 0

    print(f"read  volume : {src.bucket}  (READ-ONLY)", flush=True)
    print(f"write volume : {dst.bucket}", flush=True)
    print(f"history      : {len(hist):,} rows, latest {hist['date'].max().date()}", flush=True)
    print(f"to grade     : {len(stored)} stored guesses", flush=True)
    print(f"rss after load: {_rss_mb():.0f} MB", flush=True)

    # MUST use the container-aware probe, not os.cpu_count(): on a RunPod pod the
    # host reports 191 cores, so this asked for ~190 workers inside a 3814 MB
    # container and the kernel SIGKILLed it (exit 137). run.py was fixed for this;
    # daily.py was not, until now.
    from .run import container_resources
    cpus, total_gb, how = container_resources()
    ram_cap = max(1, int(total_gb // 1.5))
    workers = max(1, min(args.workers or max(1, cpus - 1), cpus, ram_cap))
    print(f"resources: cpus={cpus} ram={total_gb:.1f}GB (via {how}) -> workers={workers}",
          flush=True)
    t0 = time.time()
    graded, lives, infos = [], [], []

    def collect(res):
        g, l, i = res
        if g: graded.append(g)
        if l: lives.append(l)
        infos.append(i)

    if workers <= 1:
        _init_worker()
        print(f"  rss before loop: {_rss_mb():.0f} MB", flush=True)
        for k, t in enumerate(tickers, 1):
            collect(_one(t, cfg, stored.get(t), graded_dates, dials.get(t)))
            if k % 10 == 0:
                print(f"  [{k}/{len(tickers)}] {time.time()-t0:.0f}s "
                      f"rss={_rss_mb():.0f}MB", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker) as pool:
            futs = [pool.submit(_one, t, cfg, stored.get(t), graded_dates, dials.get(t))
                    for t in tickers]
            for k, f in enumerate(as_completed(futs), 1):
                collect(f.result())
                if k % 20 == 0:
                    print(f"  [{k}/{len(tickers)}] {time.time()-t0:.0f}s", flush=True)

    errs = [i for i in infos if "error" in i]
    retuned = [i["ticker"] for i in infos if i.get("retuned")]
    print(f"\ngraded {len(graded)} · new guesses {len(lives)} · retuned {len(retuned)} · "
          f"errors {len(errs)} · {time.time()-t0:.0f}s", flush=True)
    if retuned:
        print(f"  PSO re-tuned (quarter turned): {', '.join(sorted(retuned)[:12])}"
              f"{' …' if len(retuned) > 12 else ''}", flush=True)
    for e in errs[:3]:
        print(f"  !! {e['ticker']}: {e['error'].splitlines()[-1]}", file=sys.stderr)

    if not graded and not lives:
        print("nothing new — source has no bar beyond the last prediction. Exiting clean.")
        return 0

    # ---- append graded rows (locked; duplicates impossible via graded_dates) ----
    if graded:
        add = pd.DataFrame(graded)
        add["date"] = pd.to_datetime(add["date"])
        keep = [c for c in hist.columns if c in RAW_COLS]
        merged = pd.concat([hist[keep], add[keep]], ignore_index=True)
        merged = merged.drop_duplicates(subset=["ticker", "date"], keep="first")
        merged = merged.sort_values(["date", "ticker"]).reset_index(drop=True)
    else:
        merged = hist[[c for c in hist.columns if c in RAW_COLS]].copy()

    scored, metrics = score(merged)
    live_only = scored[scored["source"] == "live"]
    if len(live_only):
        _, live_metrics = score(live_only)
        metrics["live_only"] = live_metrics["overall"]

    hol = holiday_set(src)
    live_df = pd.DataFrame(lives)
    if len(live_df):
        target = next_session(pd.Timestamp(live_df["as_of"].max()), hol)
        live_df["for_session"] = target
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    meta = {"mode": "daily", "stamp": stamp, "graded": len(graded),
            "new_guesses": len(lives), "retuned": retuned,
            "rows_total": int(len(scored)),
            "rows_live": int(len(live_only)), "errors": len(errs),
            "for_session": str(target.date()) if len(live_df) else None}

    if args.dry_run:
        print("dry run — nothing written")
        print(json.dumps(meta, indent=2))
        return 0

    for pre in (f"{base}/latest", f"{base}/daily/{stamp}"):
        dst.put_dataframe(f"{pre}/predictions.parquet", scored)
        dst.put_json(f"{pre}/metrics.json", metrics)
        dst.put_json(f"{pre}/run_meta.json", meta)
        if len(live_df):
            dst.put_dataframe(f"{pre}/next_session.parquet", live_df)
    dst.put_json(f"{base}/latest/ticker_info.json",
                 [i for i in infos if "error" not in i])

    o = metrics["overall"]
    print(f"\nhistory now {len(scored):,} rows ({len(live_only):,} live)")
    print(f"direction acc {o['direction_accuracy']:.4f} vs always-up "
          f"{o['baseline_always_up']:.4f} (edge {o['edge_vs_always_up']:+.4f})")
    if "live_only" in metrics and metrics["live_only"].get("n"):
        lo = metrics["live_only"]
        print(f"LIVE-only: n={lo['n']} acc={lo['direction_accuracy']:.4f} "
              f"edge={lo['edge_vs_always_up']:+.4f}")
    if len(live_df):
        print(f"next guess for {target.date()}: "
              f"{int((live_df['pred_return']>0).sum())} up / "
              f"{int((live_df['pred_return']<=0).sum())} down")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
