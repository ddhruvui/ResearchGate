"""Merge shard outputs into one scored result set.

Each shard pod writes runs/<run_id>/shards/<NN>/<KK>/predictions.parquet. This
reads them all, concatenates, rescores globally (so direction accuracy and the
baselines are computed over the whole universe, not per shard) and writes the
combined result to runs/<run_id>/latest/.

  python -m src.merge --shards 10
"""
from __future__ import annotations

import argparse
import io
import json
from datetime import datetime, timezone

import pandas as pd

from .config import Env, load_config
from .metrics import score
from .storage import ResultStore


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--shards", type=int, required=True)
    ap.add_argument("--local-out", default="results/merged")
    ap.add_argument("--allow-partial", action="store_true",
                    help="write even if shards are missing (goes to latest_partial/)")
    args = ap.parse_args()

    cfg = load_config(args.config)
    env = Env.load()
    dst = ResultStore(env)
    run_id = cfg["run_id"]
    base = f"{cfg['output']['prefix']}/{run_id}"

    frames, lives, infos, missing = [], [], [], []
    for k in range(args.shards):
        pre = f"{base}/shards/{args.shards:02d}/{k:02d}/latest"
        try:
            blob = dst._s3.get_object(Bucket=dst.bucket,
                                      Key=f"{pre}/predictions.parquet")["Body"].read()
            frames.append(pd.read_parquet(io.BytesIO(blob)))
        except Exception as exc:
            missing.append((k, str(exc)[:80]))
            continue
        for name, sink in (("next_session.parquet", lives),):
            try:
                b = dst._s3.get_object(Bucket=dst.bucket, Key=f"{pre}/{name}")["Body"].read()
                sink.append(pd.read_parquet(io.BytesIO(b)))
            except Exception:
                pass
        try:
            b = dst._s3.get_object(Bucket=dst.bucket,
                                   Key=f"{pre}/ticker_info.json")["Body"].read()
            infos.extend(json.loads(b))
        except Exception:
            pass

    if missing:
        print(f"!! {len(missing)} of {args.shards} shard(s) missing — results are PARTIAL:")
        for k, e in missing:
            print(f"   shard {k:02d}: {e}")
        if not args.allow_partial:
            print("\nRefusing to publish partial results to latest/. Re-run with "
                  "--allow-partial to write them to latest_partial/ instead.")
            return 2
    if not frames:
        print("no shard output found")
        return 1

    allpred = pd.concat(frames, ignore_index=True).sort_values(["date", "ticker"])
    scored, metrics = score(allpred)
    meta = {
        "run_id": run_id, "merged_utc": datetime.now(timezone.utc).isoformat(),
        "shards_expected": args.shards, "shards_found": args.shards - len(missing),
        "shards_missing": [k for k, _ in missing],
        "n_tickers": int(allpred["ticker"].nunique()),
        "n_predictions": int(len(allpred)), "config": cfg,
    }

    # Partial merges must NEVER land on latest/ — a reader cannot tell 10 tickers
    # from 100 once it is there.
    out = f"{base}/latest_partial" if missing else f"{base}/latest"
    dst.put_dataframe(f"{out}/predictions.parquet", scored)
    dst.put_json(f"{out}/metrics.json", metrics)
    dst.put_json(f"{out}/run_meta.json", meta)
    dst.put_json(f"{out}/ticker_info.json", infos)
    if lives:
        dst.put_dataframe(f"{out}/next_session.parquet", pd.concat(lives, ignore_index=True))
    print(f"written to s3://{dst.bucket}/{out}/"
          + ("   [PARTIAL]" if missing else ""))

    # A rebuild changes every row, so the deployed dashboard's copy is replaced
    # wholesale. Partial merges never reach latest/, so they never reach Mongo.
    if not missing:
        # The stop-loss paper trade reads latest/predictions.parquet, which the
        # lines above just replaced, so it is refreshed BEFORE the publish that
        # ships it. A failure here leaves the previous strategy.json in place and
        # never takes the rebuild down with it.
        try:
            from .strategy import compute as compute_strategy
            compute_strategy()
        except Exception as exc:
            print(f"stop-loss strategy failed (predictions are still published): {exc}")
        try:
            from .publish_mongo import publish
            publish(full=True)
        except Exception as exc:                  # results are on the volume already
            print(f"mongo publish failed (results are on the volume): {exc}")

    if args.local_out:
        import os
        os.makedirs(args.local_out, exist_ok=True)
        scored.to_parquet(f"{args.local_out}/predictions.parquet", index=False)
        with open(f"{args.local_out}/metrics.json", "w") as fh:
            json.dump(metrics, fh, indent=2, default=str)

    o = metrics["overall"]
    print("=" * 68)
    print(f"tickers          : {meta['n_tickers']}   predictions: {o['n']:,}")
    print(f"direction acc    : {o['direction_accuracy']:.4f}   (p={o['p_value_vs_coin']:.3g})")
    print(f"  always-up base : {o['baseline_always_up']:.4f}   edge {o['edge_vs_always_up']:+.4f}")
    print(f"  last-dir base  : {o['baseline_last_dir']:.4f}")
    print(f"MSE model / zero : {o['mse_model']:.4e} / {o['mse_zero_baseline']:.4e}"
          f"  ratio {o['mse_model']/o['mse_zero_baseline']:.3f}")
    print("by year:")
    for yr, b in sorted(metrics["by_year"].items()):
        if b.get("n"):
            print(f"  {yr}  n={b['n']:7,}  acc={b['direction_accuracy']:.4f}  "
                  f"up-base={b['baseline_always_up']:.4f}  edge={b['edge_vs_always_up']:+.4f}")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
