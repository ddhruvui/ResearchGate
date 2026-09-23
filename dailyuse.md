# Daily use

```bash
RUN_MODE=daily SHARDS=1 WATCHDOG_SEC=3600 bash scripts/launch.sh
```

Pods can't delete themselves. When the run finishes, delete the pod on RunPod or it keeps billing.
`/daily-run` runs this command and deletes the pod for you.

## Every day

After the new day's data is published, type:

```
/daily-run
```

Takes about a minute and costs about $0.01. The dashboard updates a couple of minutes later:
https://researchgatefe.onrender.com

If it says `nothing to do`, the new data isn't in yet. Try again later.

## Every quarter

Nothing extra. The daily run re-tunes each stock by itself on the first run of a new quarter.
That run just takes a little longer.

## Only when you change a setting or the ticker list

```
/quarterly-backtest
```

This is a full rebuild: about 30 minutes and $2–3. It wipes the current results, so don't run it casually.

## Check the dashboard

```
/dashboard
```

More detail: [RUNBOOK.md](RUNBOOK.md)
