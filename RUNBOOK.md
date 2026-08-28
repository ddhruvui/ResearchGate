# Runbook — what to type

Three skills. Copy a prompt, paste it, done.

---

## Every day (after EODHD publishes)

> **We have new data for &lt;DATE&gt;. Run the daily pipeline and monitor it.**

Or invoke it directly:

```
/daily-run
```

**What happens:** grades yesterday's stored prediction against the bar that just
landed, adds that bar to each stock's training window, records a fresh prediction
for the next session, and pushes everything to `x3n7kgbbit`.

**How long:** ~40 s of compute. Most of the wait is placing a pod — EU-RO-1 is
often out of CPU, and it retries before falling back to a GPU.

**Cost:** ~$0.01 on CPU, ~$0.02 if it falls back to GPU.

**Safe to repeat.** Running it twice does nothing the second time — a recorded
forecast is written once and never regenerated.

### Variants

> Did the daily run go through? Show me the live track record so far.

> We have data for &lt;DATE&gt;. Grade it, then show me tomorrow's predictions with
> today's close and the expected value.

---

## Full rebuild (only when a model setting changes)

> **Re-run the full backtest from scratch with quarterly re-tuning, and monitor it.**

Or:

```
/quarterly-backtest
```

**What happens:** clears the results volume, replays all 100 tickers from
2021-01-04 across 20 parallel pods with PSO re-tuning at every quarter boundary,
then merges and rescores globally.

**How long:** ~3.5 hours. **Cost:** ~$12.

**This wipes the current results**, including the accumulated live log. It backs
up to `results/backup/` first. Do not run it casually.

### When it is actually warranted

- You changed `pso.fitness`, `pso.retune`, `backtest.window*`, `model.kernel`, or the feature set
- You changed `tickers.json`
- You want the backtest to match a rule change already applied to the daily run

### Variants

> I changed the fitness to `mse` in config/experiment.yaml — re-run the backtest
> so it matches, and tell me how it compares to rank_ic.

> Re-run the backtest but only 10 tickers first, so I can sanity-check before
> committing 3.5 hours.

---

## Reading the output

**Never quote direction accuracy on its own.** Stocks drift up, so predicting
"up" every day is right ~52.4% of the time for free. The number that matters is
the **edge over that baseline**.

| Figure | Current | Trust it? |
|---|---|---|
| Backtest edge | **−1.39pp** over 138,780 predictions | Yes |
| Correlation with actuals | ~0.003 | Yes — this is what "no signal" looks like |
| Live-only edge | swings wildly | **No** — see below |

The live figure is computed over a handful of sessions, and 100 same-day
predictions across correlated stocks are worth ~2–6 independent observations,
not 100. It will read +10pp one day and −10pp the next. It needs months.

---

## Dashboard

> **Open the dashboard and tell me what it says.**

Or:

```
/dashboard
```

**What happens:** checks whether it is already up on 8891 — it usually is, and
stays up for days — confirms it is serving the results volume and not a local
backup, and reads out the figures.

**How long:** seconds. **Cost:** nothing. It only reads what the pipeline wrote.

**It refreshes itself.** New results appear within 60 s with no restart, so
"download the new results for the UI" is a no-op: `scripts/results.sh` pulls a
local snapshot that the server ignores whenever credentials are present.

A **red banner** means it fell back to a stale local backup because the results
volume returned nothing — those numbers are from an older run with different
settings. Do not quote them.

### Variants

> The dashboard is showing a red banner — work out why and fix it.

> I changed the equity chart — rebuild the UI and show me.

> Is the UI up, and does it already have today's run?

Manual start, without the skill:

```bash
cd dashboard && npm start     # http://localhost:8891
```

---

## Something went wrong

| Symptom | What it means | Fix |
|---|---|---|
| `nothing to do` | already processed, or vendor hasn't published | Not a failure. Check the source's newest bar |
| `to grade : <100` | stored forecast file was clobbered | `python3 -m src.merge --shards 20` to restore |
| `run exit=137` | out of memory | Should not recur; report it if it does |
| `no CPU or GPU capacity` | EU-RO-1 full | Retry in a few minutes; volumes are pinned to that datacenter |
| Monitor says 0 done but results exist | zsh word-splitting bug | Monitor commands must be wrapped in `bash -c` |

---

## Hard rules

- **`8qik4zxpxq` is read-only.** Market data. Never written, never deleted from.
  Anything extra goes on `x3n7kgbbit`.
- **A graded verdict is locked.** Prices get restated; the record does not change.
- **Live and replayed rows are tagged** (`source` = `live` / `backtest`) so a real
  forward track record stays separable from a rehearsal.

Full method, deviations from the paper, and cost tables: [README.md](README.md).
