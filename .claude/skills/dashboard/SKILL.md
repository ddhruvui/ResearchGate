---
name: dashboard
description: Bring up, verify, or refresh the outcome dashboard on http://localhost:8891 — the Express API plus React SPA over the PSO+LS-SVM results. Use when the user says "open the dashboard", "is the UI up", "show me the results in the UI", "download the new results for the UI", "the dashboard looks stale", or reports the red banner. Not for launching pipeline runs — use daily-run or quarterly-backtest for those.
---

# Dashboard

Serves `runs/pso_lssvm_v1/latest/` off the results volume `x3n7kgbbit`, with a
60 s cache. The UI is a build artefact under `dashboard/web/dist`.

Working directory is the repo root (`/Users/dhruvdesai/Development/ResearchGate`).

## 1. Check whether it is already running — before anything else

It is normally left running for days as a long-lived process. Starting a second
one just fails on the port.

```bash
lsof -nP -iTCP:8891 -sTCP:LISTEN | head -3
curl -s --max-time 8 http://localhost:8891/api/health
```

```json
{"ok":true,"rows":139325,"fromVolume":true,
 "origin":{"predictions":"s3://x3n7kgbbit/runs/pso_lssvm_v1/latest/predictions.parquet"}}
```

`fromVolume: true` and a fresh `rows` count means **there is nothing to do**.
Report the numbers and stop.

## 2. "Get the new results into the UI" is usually a no-op

The server reads the volume itself and re-reads it every 60 s
(`CACHE_TTL_MS`). A daily run that finished a minute ago is already on screen.

- **Do not run `scripts/results.sh`** to refresh the UI. That pulls a local
  snapshot into `results/<stamp>/`, which the server only reads when S3
  credentials are missing. It will not change what is displayed.
- **Do not restart the server** to pick up a new run. The TTL handles it.
- To force an immediate re-read, just wait out the minute, or restart per §4.

Confirm it has the run the user means by comparing the row count and date range
before and after, not by assuming:

```bash
curl -s http://localhost:8891/api/summary | python3 -c "
import json,sys; d=json.load(sys.stdin); o=d['overall']
print('rows', d['bySource'], 'range', d['range']['start'], '->', d['range']['end'])
print('acc %.4f  edge %+.4f  n=%s' % (o['direction_accuracy'], o['edge_vs_always_up'], o['n']))"
```

## 3. Start it, only if step 1 found nothing

```bash
cd /Users/dhruvdesai/Development/ResearchGate/dashboard
[ -d server/node_modules ] || npm run install:all
[ -d web/dist ] || npm run build
npm start
```

Run it in the background — it is a server, it does not exit. Then re-check
`/api/health` before reporting success; `npm start` printing its banner only
proves the port bound, not that S3 credentials resolved.

Credentials come from `dashboard/server/.env` (gitignored, same keys as the
pipeline's `.env`). Port 8787 belongs to InvestOpediaClaude on this machine —
leave 8891 alone unless the user asks, and override with `PORT` if they do.

## 4. If the UI changed, rebuild — restarting is not enough

`npm start` serves `web/dist`, not `web/src`. After any edit under `web/src`:

```bash
cd /Users/dhruvdesai/Development/ResearchGate/dashboard && npm run build
```

The running server picks the new bundle up with no restart (it is static file
serving). For iterative UI work use `npm run dev` instead — Vite on 5273
proxying `/api` to 8891 — rather than rebuilding by hand each time.

## 5. The red banner

`Showing a local backup, not the results volume` means `fromVolume` came back
false: the S3 read threw and it fell back to `results/final/` on disk.

**This matters.** `results/final/` is an older run with different settings —
n=138,681, edge −0.94pp — so the screen shows plausible numbers from the wrong
experiment. Never quote figures off a red-bannered dashboard.

Diagnose in order:

```bash
grep -c AWS_ACCESS_KEY_ID dashboard/server/.env          # 1 = present
bash -c '. scripts/_common.sh; aws s3 ls $S3FLAGS "$DST_BUCKET/runs/pso_lssvm_v1/latest/"'
```

| Finding | Cause | Fix |
|---|---|---|
| `.env` missing the keys | server started without credentials | restore `dashboard/server/.env`, restart |
| `latest/` empty or absent | a rebuild wiped the volume and has not finished | wait for the run; the banner is correct |
| Keys present, `latest/` populated | server started before the keys existed | restart it — the cache holds the fallback |
| Server log shows `[store] S3 miss` | transient endpoint failure | restart; if it repeats, check the endpoint/region |

## 6. Endpoints

| Endpoint | Returns |
|---|---|
| `/api/health` | row count, load time, which origin served the data |
| `/api/summary` | overall metrics, per-year table, date range |
| `/api/equity?costBps=N&tickers=A,B` | equity curve points + stats |
| `/api/tickers` | per-ticker leaderboard |
| `/api/next-session` | live predictions, sorted, with the up/down split |

To see it rendered rather than curled, open `http://localhost:8891` in the
Browser pane and screenshot it — do not ask the user to check for you.

## 7. Reporting what is on screen

**Never quote direction accuracy on its own.** The always-up baseline is ~52.4%
for free; the number that matters is the edge over it — **about −1.4pp** across
the backtest. Read the exact current value off `edge_vs_always_up` in
`/api/summary` rather than repeating a figure from here; it drifts as live rows
accumulate into the pooled total.

The live-only figure on the next-session panel is computed over a handful of
sessions, and 100 same-day predictions across correlated names are worth ~2–6
independent observations. Say plainly that it means nothing yet.
