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

**What happens:** clears the results volume's `runs/` prefix, refreshes the
staged ext tickers, replays all 167 tickers from 2021-01-04 across 20 parallel
pods with PSO re-tuning at every quarter boundary, then merges and rescores
globally.

**How long:** ~4–6 hours. **Cost:** ~$15–20.

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

The live figure is computed over a handful of sessions, and 167 same-day
predictions across correlated stocks are worth ~2–6 independent observations,
not 167. It will read +10pp one day and −10pp the next. It needs months.

---

## Dashboard

The UI is deployed. **Render** serves the React site, **Vercel** serves the API,
and both read what the pipeline published to **MongoDB Atlas** (database
`ResearchGate`). Nothing has to run on this laptop.

| Piece | Repo | Runs on |
|---|---|---|
| UI | [ddhruvui/ResearchGateFE](https://github.com/ddhruvui/ResearchGateFE) | Render static site |
| API | [ddhruvui/ResearchGateBE](https://github.com/ddhruvui/ResearchGateBE) | Vercel serverless function |
| Data | this repo, `src/publish_mongo.py` | MongoDB Atlas, db `ResearchGate` |

Fill these in once deployed (the `dashboard` skill reads them from here):

- API: `https://<vercel-project>.vercel.app`
- UI: `https://<render-site>.onrender.com`

> **Open the dashboard and tell me what it says.**

Or:

```
/dashboard
```

**It refreshes itself.** The daily pod publishes to Mongo right after it writes
`latest/` (`scripts/bootstrap.sh`), and `python3 -m src.merge` publishes after a
full rebuild. The API caches for 60 s and Vercel's edge for another 60 s, so a
new run is on screen within a couple of minutes.

To publish by hand — after a merge done elsewhere, or if the pod's publish step
failed:

```bash
bash -c 'set -a; . ./.env; set +a; python3 -m src.publish_mongo'
```

Safe to repeat. Graded rows are locked, so a re-publish only appends rows Mongo
does not have yet (seconds). After a rebuild add `--full`; `src.merge` already
does. A first seed of 220k rows takes about three minutes.

A **"last published N days ago" banner** means Mongo has not been written to
for more than five days — the daily run did not go through, or its publish step
failed. Check the pod log for `publishing latest/ to MongoDB` and `[publish]
FAILED`, then run the daily or republish by hand.

### Variants

> Is the dashboard showing today's run?

> The dashboard says it was last published four days ago — work out why.

> Publish the latest results to the dashboard.

---

## Something went wrong

| Symptom | What it means | Fix |
|---|---|---|
| `nothing to do` | already processed, or vendor hasn't published | Not a failure. Check the source's newest bar |
| `to grade : <167` | stored forecast file was clobbered, or staged ext tickers went stale | `python3 -m src.merge --shards 20` to restore; `python3 -m src.fetch_ext` to refresh |
| `run exit=137` | out of memory | Should not recur; report it if it does |
| `no CPU or GPU capacity` | EU-RO-1 full | Retry in a few minutes; volumes are pinned to that datacenter |
| Monitor says 0 done but results exist | zsh word-splitting bug | Monitor commands must be wrapped in `bash -c` |
| `[publish] FAILED` in the pod log | Mongo unreachable, or `MONGO_URI`/`DB_PASSWORD` wrong in `.env` | Results are on the volume; republish by hand with `python3 -m src.publish_mongo` |

---

## Hard rules

- **The source volume (`crimtr8kbf`, pinned in `src/config.py`) is read-only.**
  Market data. Never written, never deleted from. Anything extra goes on
  `x3n7kgbbit` — including the 31 staged ext tickers under `data/` that
  `python3 -m src.fetch_ext` maintains (refresh them before the daily run).
- **A graded verdict is locked.** Prices get restated; the record does not change.
- **Live and replayed rows are tagged** (`source` = `live` / `backtest`) so a real
  forward track record stays separable from a rehearsal.

Full method, deviations from the paper, and cost tables: [README.md](README.md).
