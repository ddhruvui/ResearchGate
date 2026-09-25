# Daily use

```bash
RUN_MODE=daily SHARDS=1 WATCHDOG_SEC=3600 bash scripts/launch.sh
```

The pod deletes itself when the run finishes. If it can't, this clears it (safe to run any time —
it only deletes pods whose run is over):

```bash
scripts/reap_pods.sh
```

`/daily-run` does both for you.

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
