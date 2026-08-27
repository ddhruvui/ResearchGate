"""Entrypoint. Runs the walk-forward backtest and the live next-session prediction.

  python -m src.run                 # backtest + next-session prediction (the daily job)
  python -m src.run --mode backtest
  python -m src.run --mode predict
  python -m src.run --limit 5       # smoke test on the first 5 tickers

Reads volume 8qik4zxpxq (READ-ONLY, never mounted). Writes volume x3n7kgbbit.
"""
from __future__ import annotations

import os

# Each worker does its own single-threaded linear algebra and we parallelise
# ACROSS tickers, so a per-process BLAS thread pool is pure oversubscription:
# W workers x T threads fight for the same cores and each pool carries its own
# buffers. On a 2-vCPU pod that got a worker OOM-killed (BrokenProcessPool) in
# under a minute. Must be set before numpy is imported.
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_v, "1")

import argparse
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .calendar_util import holiday_set, next_session
from .config import Env, load_config, load_tickers
from .data import load_adjusted
from .indicators import FEATURES, build_features, next_session_return
from .lssvm import LSSVM
from .metrics import score
from .storage import ResultStore, SourceStore
from .walkforward import _train_slice, run_ticker

_ENV: Env | None = None
_SRC: SourceStore | None = None


def dst_read(dst: ResultStore, key: str) -> bytes:
    """Read our OWN prior output off the results volume (never the source)."""
    return dst._s3.get_object(Bucket=dst.bucket, Key=key)["Body"].read()


def _init_worker():
    global _ENV, _SRC
    _ENV = Env.load()
    _SRC = SourceStore(_ENV)


def _one(ticker: str, cfg: dict, mode: str = "both", known: dict | None = None):
    """Backtest one ticker (unless mode=predict), plus its next-session prediction.

    mode=predict is the DAILY path: it skips the 1,415-step replay entirely and
    fits once on the current window, reusing the hyper-parameters PSO found on the
    last full backtest. Seconds per ticker instead of minutes.
    """
    try:
        bars = load_adjusted(_SRC, ticker, cfg)
        if bars.empty or len(bars) < cfg["backtest"]["min_train_rows"] + 40:
            return ticker, None, {"ticker": ticker, "skipped": "insufficient history",
                                  "n_bars": int(len(bars))}, None
        if mode == "predict":
            info = dict(known or {})
            info.setdefault("ticker", ticker)
            if "C" not in info or "gamma" not in info:
                # No prior backtest to inherit from: tune on everything but the last bar.
                _, tuned = run_ticker(ticker, bars.iloc[:-1], {**cfg, "backtest": {
                    **cfg["backtest"], "start": str(bars["date"].iloc[-2].date())}})
                info.update({k: tuned[k] for k in ("C", "gamma") if k in tuned})
            live = _live_prediction(ticker, bars, cfg, info)
            info["n_predictions"] = 0
            return ticker, None, info, live
        preds, info = run_ticker(ticker, bars, cfg)
        live = _live_prediction(ticker, bars, cfg, info)
        return ticker, preds, info, live
    except Exception:
        return ticker, None, {"ticker": ticker, "error": traceback.format_exc(limit=4)}, None


def _live_prediction(ticker: str, bars: pd.DataFrame, cfg: dict, info: dict):
    """Predict the session AFTER the last bar. Nothing to grade yet — this is the
    paper-trade output; it gets scored on the next run once the bar lands."""
    feats = build_features(bars, cfg)
    X = feats[FEATURES].to_numpy(dtype=float)
    y = next_session_return(bars).to_numpy(dtype=float)
    usable = np.isfinite(X).all(axis=1) & np.isfinite(y)
    j = len(bars) - 1                       # last row: features known, target unknown
    if not np.isfinite(X[j]).all():
        return None
    bt = cfg["backtest"]
    sl = _train_slice(j, bt["window"], int(bt["window_sessions"]))
    m = usable[sl]
    Xt, yt = X[sl][m], y[sl][m]
    if len(yt) < int(bt["min_train_rows"]):
        return None
    try:
        model = LSSVM(C=info.get("C", 1.0), gamma=info.get("gamma", 0.1),
                      kernel=cfg["model"]["kernel"]).fit(Xt, yt)
        pred = float(model.predict(X[j:j + 1])[0])
    except Exception:
        return None
    if not np.isfinite(pred):
        return None
    return {"ticker": ticker, "as_of": bars["date"].iloc[-1],
            "last_close": float(bars["adj_close"].iloc[-1]),
            "pred_return": pred, "pred_direction": "UP" if pred > 0 else "DOWN",
            "implied_close": float(bars["adj_close"].iloc[-1]) * (1.0 + pred),
            "n_train": int(len(yt))}


def container_resources() -> tuple[int, float, str]:
    """CPU and RAM available to THIS CONTAINER, not the host.

    os.cpu_count() and SC_PHYS_PAGES report the HOST. On a RunPod 2-vCPU pod the
    host has 191 cores, so the auto-sizing asked for 191 workers on one core and
    the pool OOM-died instantly. cgroup v2 (then v1) is the authority; sched
    affinity is the fallback.
    """
    src = []
    cpus = None
    try:                                             # cgroup v2
        quota, period = open("/sys/fs/cgroup/cpu.max").read().split()
        if quota != "max":
            cpus = max(1, int(float(quota) / float(period)))
            src.append("cgroup2")
    except Exception:
        pass
    if cpus is None:
        try:                                         # cgroup v1
            q = int(open("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read())
            pd_ = int(open("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read())
            if q > 0:
                cpus = max(1, q // pd_)
                src.append("cgroup1")
        except Exception:
            pass
    if cpus is None:
        try:
            cpus = len(os.sched_getaffinity(0)); src.append("affinity")
        except AttributeError:
            cpus = os.cpu_count() or 1; src.append("cpu_count")

    gb = None
    for path in ("/sys/fs/cgroup/memory.max",
                 "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            raw = open(path).read().strip()
            if raw != "max":
                v = int(raw)
                if 0 < v < (1 << 50):                # sentinel for "unlimited"
                    gb = v / 1e9; src.append("cgroup-mem"); break
        except Exception:
            continue
    if gb is None:
        try:
            gb = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9
            src.append("sysconf")
        except (ValueError, OSError, AttributeError):
            gb = 2.0; src.append("default")
    return cpus, gb, "+".join(src)


def _run_pool(tickers, cfg, args, known, workers):
    """Fan out across tickers. Raises BrokenProcessPool if a worker is killed."""
    out, t0 = [], time.time()
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker) as pool:
        futs = {pool.submit(_one, t, cfg, args.mode, known.get(t)): t for t in tickers}
        for k, fut in enumerate(as_completed(futs), 1):
            res = fut.result()
            out.append(res)
            info = res[2]
            n = info.get("n_predictions", 0)
            note = info.get("skipped") or ("ERROR" if "error" in info else f"{n} preds")
            print(f"[{k:3d}/{len(tickers)}] {res[0]:6} {note}  ({time.time()-t0:.0f}s)",
                  flush=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--mode", choices=["backtest", "predict", "both"], default="both")
    ap.add_argument("--limit", type=int, default=0, help="only the first N tickers")
    ap.add_argument("--shard", type=int, default=int(os.environ.get("SHARD", "0")),
                    help="0-based shard index; tickers[shard::shards]")
    ap.add_argument("--shards", type=int, default=int(os.environ.get("SHARDS", "1")),
                    help="total shards. Each shard writes its own output prefix.")
    ap.add_argument("--workers", type=int, default=int(os.environ.get("WORKERS", "0")))
    ap.add_argument("--local-out", default=os.environ.get("LOCAL_OUT", ""))
    args = ap.parse_args()

    cfg = load_config(args.config)
    env = Env.load()
    src, dst = SourceStore(env), ResultStore(env)
    tickers = load_tickers(cfg)
    if args.limit:
        tickers = tickers[:args.limit]
    if args.shards > 1:
        # Stride, not block: consecutive tickers have similar history lengths, so
        # striding balances cost across shards better than contiguous slices.
        tickers = tickers[args.shard::args.shards]

    started = datetime.now(timezone.utc)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    run_id = cfg["run_id"]
    base = f"{cfg['output']['prefix']}/{run_id}"
    if args.shards > 1:
        base = f"{base}/shards/{args.shards:02d}/{args.shard:02d}"
    cpus, total_gb, how = container_resources()
    requested = args.workers or max(1, cpus - 1)
    caps = {"requested": requested, "cpus": cpus, "ram": max(1, int(total_gb // 1.5))}
    workers = max(1, min(caps.values()))
    print(f"resources: cpus={cpus} ram={total_gb:.1f}GB (via {how})", flush=True)
    if workers != requested:
        print(f"workers {requested} -> {workers} (limited by {min(caps, key=caps.get)})",
              flush=True)

    print(f"read  volume : {src.bucket}  (READ-ONLY)", flush=True)
    print(f"write volume : {dst.bucket}", flush=True)
    print(f"tickers={len(tickers)} workers={workers} window={cfg['backtest']['window']}"
          f"/{cfg['backtest']['window_sessions']} kernel={cfg['model']['kernel']}", flush=True)

    # In predict mode reuse the hyper-parameters from the last full backtest.
    known: dict[str, dict] = {}
    if args.mode == "predict":
        try:
            import json as _j
            prev = _j.loads(dst_read(dst, f"{base}/latest/ticker_info.json"))
            known = {r["ticker"]: r for r in prev if isinstance(r, dict) and "ticker" in r}
            print(f"reusing PSO parameters for {len(known)} tickers from the last backtest",
                  flush=True)
        except Exception:
            print("no prior backtest found — tuning fresh", flush=True)

    frames, infos, lives = [], [], []
    t0 = time.time()
    try:
        if workers <= 1:
            print("single worker: running in-process (no pool to duplicate RSS)",
                  flush=True)
            _init_worker()
            results = []
            for k, t in enumerate(tickers, 1):
                results.append(_one(t, cfg, args.mode, known.get(t)))
                info = results[-1][2]
                n = info.get("n_predictions", 0)
                note = info.get("skipped") or ("ERROR" if "error" in info else f"{n} preds")
                print(f"[{k:3d}/{len(tickers)}] {t:6} {note}  ({time.time()-t0:.0f}s)",
                      flush=True)
        else:
            results = _run_pool(tickers, cfg, args, known, workers)
    except BrokenProcessPool:
        print("!! worker pool broke (likely OOM) — falling back to in-process run",
              file=sys.stderr, flush=True)
        _init_worker()
        results = []
        for k, t in enumerate(tickers, 1):
            results.append(_one(t, cfg, args.mode, known.get(t)))
            print(f"[{k:3d}/{len(tickers)}] {t:6} (serial)", flush=True)
    for ticker, preds, info, live in results:
        infos.append(info)
        if preds is not None and len(preds):
            frames.append(preds)
        if live:
            lives.append(live)

    if not frames and args.mode != "predict":
        print("no predictions produced", file=sys.stderr)
        dst.put_json(f"{base}/{stamp}/ticker_info.json", infos)
        return 1

    allpred = (pd.concat(frames, ignore_index=True).sort_values(["date", "ticker"])
               if frames else pd.DataFrame())
    scored, metrics = score(allpred)

    meta = {
        "run_id": run_id, "stamp": stamp,
        "started_utc": started.isoformat(), "elapsed_sec": round(time.time() - t0, 1),
        "source_volume": src.bucket, "results_volume": dst.bucket,
        "n_tickers_requested": len(tickers),
        "mode": args.mode,
        "shard": args.shard, "shards": args.shards,
        "n_tickers_with_predictions": int(allpred["ticker"].nunique()) if len(allpred) else 0,
        "n_predictions": int(len(allpred)),
        "config": cfg,
    }

    if args.mode in ("backtest", "both") and len(allpred):
        for prefix in (f"{base}/{stamp}", f"{base}/latest"):
            dst.put_dataframe(f"{prefix}/predictions.parquet", scored)
            dst.put_json(f"{prefix}/metrics.json", metrics)
            dst.put_json(f"{prefix}/ticker_info.json", infos)
            dst.put_json(f"{prefix}/run_meta.json", meta)

    if args.mode in ("predict", "both") and lives:
        live_df = pd.DataFrame(lives)
        hol = holiday_set(src)
        target = next_session(pd.Timestamp(live_df["as_of"].max()), hol)
        live_df["for_session"] = target
        for prefix in (f"{base}/{stamp}", f"{base}/latest"):
            dst.put_dataframe(f"{prefix}/next_session.parquet", live_df)
            dst.put_json(f"{prefix}/next_session.json", {
                "for_session": str(target.date()),
                "as_of": str(pd.Timestamp(live_df['as_of'].max()).date()),
                "n": len(live_df),
                "n_up": int((live_df["pred_return"] > 0).sum()),
                "n_down": int((live_df["pred_return"] <= 0).sum()),
            })
        print(f"live prediction for session {target.date()}: "
              f"{int((live_df['pred_return']>0).sum())} up / "
              f"{int((live_df['pred_return']<=0).sum())} down", flush=True)

    if args.local_out:
        os.makedirs(args.local_out, exist_ok=True)
        scored.to_parquet(f"{args.local_out}/predictions.parquet", index=False)
        import json as _json
        with open(f"{args.local_out}/metrics.json", "w") as fh:
            _json.dump(metrics, fh, indent=2, default=str)

    o = metrics["overall"]
    if not o.get("n"):
        print(f"\nmode={args.mode}: no scored backtest rows (expected for predict mode)",
              flush=True)
        print(f"results -> s3://{dst.bucket}/{base}/{stamp}/ (and /latest/)", flush=True)
        return 0
    print("\n" + "=" * 68, flush=True)
    print(f"predictions      : {o['n']:,} over {allpred['ticker'].nunique()} tickers")
    print(f"direction acc    : {o['direction_accuracy']:.4f}   (p={o['p_value_vs_coin']:.3g})")
    print(f"  always-up base : {o['baseline_always_up']:.4f}   edge {o['edge_vs_always_up']:+.4f}")
    print(f"  last-dir base  : {o['baseline_last_dir']:.4f}")
    print(f"MSE model / zero : {o['mse_model']:.3e} / {o['mse_zero_baseline']:.3e}")
    print("by year:")
    for yr, b in sorted(metrics["by_year"].items()):
        if b.get("n"):
            print(f"  {yr}  n={b['n']:7,}  acc={b['direction_accuracy']:.4f}  "
                  f"up-base={b['baseline_always_up']:.4f}  edge={b['edge_vs_always_up']:+.4f}")
    print("=" * 68, flush=True)
    print(f"results -> s3://{dst.bucket}/{base}/{stamp}/ (and /latest/)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
