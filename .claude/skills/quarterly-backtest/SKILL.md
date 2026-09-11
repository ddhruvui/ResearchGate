---
name: quarterly-backtest
description: Run and monitor a full walk-forward rebuild of the PSO+LS-SVM backtest — 167 tickers replayed from 2021 with quarterly PSO re-tuning, sharded across 20 RunPod pods, then merged and rescored. Use when the user asks to re-run the backtest from scratch, changes a model setting (fitness, window, kernel, re-tune cadence) or the universe and wants it revalidated, or says "start over" / "rebuild". Takes ~4-6 hours. Not for everyday operation — use daily-run for that.
---

# Quarterly backtest (full rebuild)

Replays every session from 2021-01-04 to the latest bar for all 167 tickers,
re-tuning PSO at each quarter boundary on trailing data only. **~4–6 hours**,
**~$15–20**, 20 parallel pods. (Was ~3.5 h/$12 at 100 tickers; the 67 adds are
mostly shorter histories, so cost grows less than linearly.)

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

Only ever `x3n7kgbbit`, and only its `runs/` prefix — `data/` on the same volume
holds staged ext tickers (see step 3) and must survive the wipe. **The source
volume (`crimtr8kbf`, pinned in `src/config.py`) is read-only market data and
must never be written to or deleted from.** Keep a local backup first.

```bash
bash -c 'set +e; . scripts/_common.sh; set +e
mkdir -p results/backup && aws s3 cp $S3FLAGS "$DST_BUCKET/runs/pso_lssvm_v1/latest/" results/backup/ --recursive --quiet
aws s3 rm $S3FLAGS "$DST_BUCKET/runs/" --recursive | tail -2
aws s3 ls $S3FLAGS "$SRC_BUCKET/data/" | head -2'
```

The last line is a deliberate check that the source volume is untouched.

The deployed dashboard is unaffected by the wipe: it reads MongoDB, which still
holds the previous run until step 5 publishes the merged rebuild over it. There
is no gap in what the UI shows.

## 2b. Stage/refresh ext tickers

The acquisition pipeline only publishes its own universe to the source volume.
Tickers outside it are staged on the results volume under identical keys and
read through `LayeredSource`. Refresh them so the replay ends on the same bar
for every ticker:

```bash
cd /Users/dhruvdesai/Development/ResearchGate
bash -c 'set -a; . ./.env; set +a; python3 -m src.fetch_ext'
```

Every staged ticker must report its last bar at the same date the source volume
carries (compare with AAPL). Failures list at the bottom — do not launch with
failures outstanding.

## 3. Launch 20 shards

```bash
cd /Users/dhruvdesai/Development/ResearchGate
SHARDS=20 WATCHDOG_SEC=43200 bash scripts/launch.sh 2>&1 | grep -E "launched|placed at|no capacity at 2|all pods|some pods" | tail -25
```

20 shards × 8–9 tickers keeps each pod under the 12 h watchdog (quarterly
re-tuning is ~8× the cost of tuning once: 1,415 walk-forward fits **plus 24 × 620
PSO fits** per ticker — and many of the 67 added tickers have far shorter
histories). Tickers are strided (`tickers[k::20]`) so history lengths balance
across shards.

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

The merge rescores **globally** across all 167 tickers, so accuracy and baselines
are not averages of per-shard averages.

It then **publishes to MongoDB itself** (`src.merge` → `publish(full=True)`):
every row of the run is replaced, because a rebuild changes old rows too, not
just appends. Expect `[publish] ResearchGate: predictions replaced +220,929 …`
after `written to s3://…/latest/`; about three minutes. The `.env` sourced above
is what supplies `MONGO_URI`.

## 5b. Confirm the dashboard switched to the rebuild

```bash
API=https://research-gate-be.vercel.app
curl -s --max-time 20 "$API/api/summary" | python3 -c "
import json,sys; d=json.load(sys.stdin); o=d['overall']; m=d.get('runMeta') or {}
print('published', d['publishedAt'], '| merged', m.get('merged_utc'), '| shards', m.get('shards_found'), '/', m.get('shards_expected'))
print('rows', d['bySource'], '| range', d['range']['start'], '->', d['range']['end'])
print('acc %.4f  edge %+.4f  n=%s' % (o['direction_accuracy'], o['edge_vs_always_up'], o['n']))"
```

`runMeta.merged_utc` present (a daily run has `mode: daily` instead) and
`publishedAt` after the merge mean the UI is on the new run; allow two minutes
for the API and edge caches. If `[publish] FAILED` appeared, the merged results
are on the volume — publish them by hand, reading S3 and writing Mongo with
nothing saved locally:

```bash
bash -c 'set -a; . ./.env; set +a; python3 -m src.strategy && python3 -m src.publish_mongo --full'
```

`src.merge` runs `src.strategy` itself just before publishing, because a rebuild
replaces every prediction and the $10k stop-loss paper trade is computed from
them. Re-run it by hand as above whenever you publish a rebuild by hand.

`--full` matters here: without it the publisher would only append rows it does
not have and leave the old run's rows in place under the same run id (a
fingerprint guard usually catches that and falls back to a full replace, but do
not rely on it).

## 6. Report

Give the pooled edge over the always-up baseline, the per-year table, and the
predicted/actual correlation, and say that the dashboard now shows the rebuild. State the baseline explicitly — accuracy alone is
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
| `[publish] FAILED: ServerSelectionTimeoutError` after the merge | Atlas unreachable or wrong `DB_PASSWORD` in `.env`; results are on the volume, republish per 5b |
| Dashboard shows the rebuild's metrics but the daily live log is gone | expected — the wipe discards the accumulated live rows and the rebuild replaces the run; the pre-wipe copy is in `results/backup/` |
