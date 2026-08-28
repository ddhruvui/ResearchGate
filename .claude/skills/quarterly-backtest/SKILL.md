---
name: quarterly-backtest
description: Run and monitor a full walk-forward rebuild of the PSO+LS-SVM backtest — 100 tickers replayed from 2021 with quarterly PSO re-tuning, sharded across 20 RunPod pods, then merged and rescored. Use when the user asks to re-run the backtest from scratch, changes a model setting (fitness, window, kernel, re-tune cadence) and wants it revalidated, or says "start over" / "rebuild". Takes ~3.5 hours. Not for everyday operation — use daily-run for that.
---

# Quarterly backtest (full rebuild)

Replays every session from 2021-01-04 to the latest bar for all 100 tickers,
re-tuning PSO at each quarter boundary on trailing data only. **~3.5 hours**,
**~$12**, 20 parallel pods.

Working directory is the repo root.

## Before launching — confirm the user wants this

It costs real money and wipes the current results. Confirm they want a full
rebuild rather than a daily run. Ask which settings changed and why, so the
result is attributable.

## 1. Check the config matches intent

```bash
cd /Users/dhruvdesai/Development/ResearchGate
python3 -c "
import yaml; c=yaml.safe_load(open('config/experiment.yaml'))
print('fitness  :', c['pso']['fitness'])       # rank_ic (mse reproduces the paper)
print('retune   :', c['pso']['retune'])        # quarterly
print('window   :', c['backtest']['window'], c['backtest']['window_sessions'])
print('kernel   :', c['model']['kernel'])
print('start    :', c['backtest']['start'])"
pytest tests/ -q 2>&1 | tail -3
```

All tests must pass first. `test_leakage.py` is the one that matters — it proves
no future information reaches a prediction.

## 2. Clear the results volume

Only ever `x3n7kgbbit`. **`8qik4zxpxq` is read-only market data and must never be
written to or deleted from.** Keep a local backup first.

```bash
bash -c 'set +e; . scripts/_common.sh; set +e
mkdir -p results/backup && aws s3 cp $S3FLAGS "$DST_BUCKET/runs/pso_lssvm_v1/latest/" results/backup/ --recursive --quiet
aws s3 rm $S3FLAGS "$DST_BUCKET/" --recursive | tail -2
aws s3 ls $S3FLAGS "$SRC_BUCKET/data/" | head -2'
```

The last line is a deliberate check that the source volume is untouched.

## 3. Launch 20 shards

```bash
cd /Users/dhruvdesai/Development/ResearchGate
SHARDS=20 WATCHDOG_SEC=43200 bash scripts/launch.sh 2>&1 | grep -E "launched|placed at|no capacity at 2|all pods|some pods" | tail -25
```

20 shards × 5 tickers keeps each pod under the 12 h watchdog (quarterly re-tuning
is ~8× the cost of tuning once: 1,415 walk-forward fits **plus 24 × 620 PSO
fits** per ticker). Tickers are strided (`tickers[k::20]`) so history lengths
balance across shards.

Expect only some to place — EU-RO-1 capacity is usually tight. Note which are
missing and start the retry loop:

```bash
cd /Users/dhruvdesai/Development/ResearchGate
SHARDS=20 SHARD_LIST="4 5 6 7" TRIES=80 INTERVAL=180 WATCHDOG_SEC=43200 \
  nohup bash scripts/retry_shards.sh > /tmp/retry.log 2>&1 &
```

It skips shards that are already running **or already finished**, so it is safe
to leave going.

## 4. Monitor

Wrap in `bash -c` — zsh will not word-split `$AWSF` and every aws call fails
silently, reporting 0 shards done while results sit on the volume.

```bash
bash -c '
cd /Users/dhruvdesai/Development/ResearchGate
set +e
export AWS_ACCESS_KEY_ID=$(grep "^AWS_ACCESS_KEY_ID=" .env | cut -d= -f2-)
export AWS_SECRET_ACCESS_KEY=$(grep "^AWS_SECRET_ACCESS_KEY=" .env | cut -d= -f2-)
KEYF=$(grep "^RUNPOD_API_KEY=" .env | cut -d= -f2-)
AWSF="--region eu-ro-1 --endpoint-url https://s3api-eu-ro-1.runpod.io"
prev=""
while true; do
  n=$(aws s3 ls $AWSF s3://x3n7kgbbit/runs/pso_lssvm_v1/shards/20/ --recursive 2>/dev/null | grep -c "latest/metrics.json")
  alive=$(curl -sS --max-time 20 https://rest.runpod.io/v1/pods -H "Authorization: Bearer $KEYF" 2>/dev/null | grep -c "researchgate-pso-lssvm")
  line="shards ${n:-0}/20 | pods ${alive:-0}"
  [ "$line" != "$prev" ] && { echo "$line"; prev="$line"; }
  [ "${n:-0}" -ge 20 ] && { echo "ALL 20 COMPLETE"; exit 0; }
  [ "${alive:-0}" -eq 0 ] && { echo "no pods left at ${n:-0}/20"; exit 0; }
  sleep 420
done'
```

Use `persistent: true` — the run outlasts the 1-hour monitor cap.

Pods **vanishing is success**, not failure: each self-terminates after writing.
Do not read a shrinking pod count as pods dying.

Shard pace varies a lot (7–44 min/ticker) depending on how many quarters its
tickers span. One straggler holding up the last hour is normal.

## 5. Merge

Only when all 20 are in:

```bash
cd /Users/dhruvdesai/Development/ResearchGate
set -a && . ./.env && set +a
python3 -m src.merge --shards 20 --local-out results/latest
```

It **refuses to publish partial results** to `latest/` (exit 2) — that guard is
deliberate, since a reader cannot tell 80 tickers from 100 once published. To
get an early read, `--allow-partial` routes to `latest_partial/` instead.

The merge rescores **globally** across all 100 tickers, so accuracy and baselines
are not averages of per-shard averages.

## 6. Report

Give the pooled edge over the always-up baseline, the per-year table, and the
predicted/actual correlation. State the baseline explicitly — accuracy alone is
meaningless, since a coin flip is the wrong benchmark and the always-up baseline
sits near 52.4%.

Current reference result: **51.00% accuracy vs 52.39% baseline, edge −1.39pp,
correlation ~0.003, negative in every year.**

## Gotchas that have actually bitten

| Symptom | Cause |
|---|---|
| `exit 137` seconds in | worker count from `os.cpu_count()` (host = 191 cores), not the cgroup |
| Monitor reports 0 done while results exist | zsh not word-splitting `$AWSF`; wrap in `bash -c` |
| `Container Disk must be <= 20` | `RUNPOD_CONTAINER_DISK_GB` above the flavor cap |
| Pod id equals the volume id | greedy `sed` for `"id"`; parse JSON properly |
| Finished shards relaunching | retry loop checking "running" instead of "completed" |
