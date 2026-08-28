---
name: daily-run
description: Run and monitor the everyday PSO+LS-SVM pipeline — grade yesterday's stored prediction against the bar that just landed, learn from it, and record a new prediction for the next session. Use when the user says "run the daily", "we have new data for <date>", "grade yesterday", or asks for today's prediction. Not for full rebuilds — use quarterly-backtest for those.
---

# Daily run

One pass: **grade → learn → guess**. Never replays history. ~40 s of compute for
100 tickers; the slow part is placing a pod.

Working directory is the repo root (`/Users/dhruvdesai/Development/ResearchGate`).

## 1. Confirm the new bar actually landed

Do not skip this. The run is a no-op without it, and you will waste a pod.

```bash
cd /Users/dhruvdesai/Development/InvestOpediaClaude/data_acquisition && bash -c 'set +e; . scripts/_common.sh; set +e
for t in AAPL MSFT AVGO; do aws s3 cp $S3FLAGS "$BUCKET/data/$t.json" /tmp/chk_$t.json --quiet; done
python3 -c "
import json
for t in (\"AAPL\",\"MSFT\",\"AVGO\"):
    d=json.load(open(f\"/tmp/chk_{t}.json\"))
    print(t, [r[\"date\"] for r in d[-3:]])
"'
```

The newest date must be **later** than the `as_of` of the stored prediction
(see step 2). If it is not, stop and tell the user the vendor has not published
yet — do not launch anything.

## 2. Check what is already recorded

```bash
bash -c 'set +e; . scripts/_common.sh; set +e
aws s3 cp $S3FLAGS "$DST_BUCKET/runs/pso_lssvm_v1/latest/next_session.parquet" /tmp/ns.parquet --quiet'
python3 -c "
import pandas as pd; d=pd.read_parquet('/tmp/ns.parquet')
print(f'{len(d)} stored guesses, as_of {d[\"as_of\"].astype(str).unique()[0]} -> for {d[\"for_session\"].astype(str).unique()[0]}')"
```

**If this shows fewer than 100 rows, stop.** A partial file means a limited run
overwrote it. Restore with `python3 -m src.merge --shards 20` before continuing.

## 3. Check nothing is already running

```bash
cd /Users/dhruvdesai/Development/ResearchGate && bash -c 'set +e; . scripts/_common.sh; set +e
curl -sS --max-time 25 https://rest.runpod.io/v1/pods -H "Authorization: Bearer $RUNPOD_API_KEY" \
 | python3 -c "import json,sys; print([x[\"name\"] for x in json.load(sys.stdin) if \"researchgate\" in (x.get(\"name\") or \"\")] or \"none\")"'
```

A pod already running will do the work; launching a second is wasteful (the
idempotency guard makes it harmless, but it still costs a placement).

## 4. Launch

```bash
cd /Users/dhruvdesai/Development/ResearchGate
RUN_MODE=daily SHARDS=1 WATCHDOG_SEC=3600 bash scripts/launch.sh 2>&1 | grep -vE "^Completed|upload:" | tail -10
```

CPU capacity in EU-RO-1 is frequently exhausted. The launcher tries 16→8→4→2
vCPU, then falls back to the **cheapest secure-cloud GPU** (live-priced, capped
at `GPU_MAX_PRICE`, default $0.50/hr). The GPU is never used for compute — it is
bought for the host CPU. If it reports no capacity at all, retry every ~40 s;
placement usually succeeds within a few attempts.

Note the pod id from `launched researchgate-pso-lssvm -> <id>`.

## 5. Monitor

Use the Monitor tool, wrapped in `bash -c` (zsh does not word-split `$AWSF`,
which silently breaks every aws call and reports a plausible-looking zero):

```bash
bash -c '
cd /Users/dhruvdesai/Development/ResearchGate
set +e
export AWS_ACCESS_KEY_ID=$(grep "^AWS_ACCESS_KEY_ID=" .env | cut -d= -f2-)
export AWS_SECRET_ACCESS_KEY=$(grep "^AWS_SECRET_ACCESS_KEY=" .env | cut -d= -f2-)
AWSF="--region eu-ro-1 --endpoint-url https://s3api-eu-ro-1.runpod.io"
prev=""
for i in $(seq 1 90); do
  K=$(aws s3 ls $AWSF s3://x3n7kgbbit/_pod_logs/ 2>/dev/null | grep POD_ID | awk "{print \$4}" | tail -1)
  if [ -n "$K" ]; then
    aws s3 cp $AWSF "s3://x3n7kgbbit/_pod_logs/$K" /tmp/dr.log --quiet 2>/dev/null
    cur=$(grep -E "resources:|to grade|graded |LIVE-only|next guess|run exit|Killed|Traceback|nothing to do" /tmp/dr.log 2>/dev/null)
    if [ "$cur" != "$prev" ]; then
      diff <(printf "%s\n" "$prev") <(printf "%s\n" "$cur") 2>/dev/null | grep "^>" | sed "s/^> //"
      prev="$cur"
    fi
    grep -q "run exit" /tmp/dr.log 2>/dev/null && { echo "RUN FINISHED"; exit 0; }
  fi
  sleep 20
done'
```

Substitute the real pod id for `POD_ID`. The grep must cover failure signatures
(`Killed`, `Traceback`), not just the happy path — silence otherwise looks
identical to a crash.

## 6. Verify and report

```bash
cd /Users/dhruvdesai/Development/ResearchGate && bash -c 'set +e; . scripts/_common.sh; set +e
aws s3 cp $S3FLAGS "$DST_BUCKET/runs/pso_lssvm_v1/latest/predictions.parquet" /tmp/p.parquet --quiet
aws s3 cp $S3FLAGS "$DST_BUCKET/runs/pso_lssvm_v1/latest/next_session.parquet" /tmp/n.parquet --quiet'
python3 -c "
import pandas as pd
d=pd.read_parquet('/tmp/p.parquet'); n=pd.read_parquet('/tmp/n.parquet')
print(d['source'].value_counts().to_dict())
print('live dates:', sorted(pd.to_datetime(d[d.source=='live']['date']).dt.date.astype(str).unique()))
print(f'next: {len(n)} rows for {n[\"for_session\"].astype(str).unique()[0]}')"
```

Report: rows graded, live-row count, the next session's date and up/down split.

**Always caveat the live-only accuracy.** It is computed over very few sessions,
and same-day predictions across 100 stocks are ~2–6 independent observations,
not 100 — measured cross-sectional correlation is 0.08–0.25. It swings by tens
of points for weeks. Quote the backtest figure (currently **−1.4pp edge over the
always-up baseline**) as the real number, and say plainly that the live figure
means nothing yet.

## Expected outcomes

| Log line | Meaning |
|---|---|
| `graded 100 · new guesses 100 · errors 0` | success |
| `nothing to do` | already processed, or vendor has not published. Correct, not a failure |
| `run exit=137` | OOM. Should not recur (worker count is cgroup-aware), but report it |
| `to grade : <100` | stored file was clobbered — re-merge before rerunning |
