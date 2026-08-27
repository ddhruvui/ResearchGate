# Outcome dashboard

Node/Express API + React (Vite) frontend over the PSO + LS-SVM backtest.

Answers one question: **did the model beat the free alternative?** — in accuracy
terms and, more importantly, in money.

## Run it

```sh
npm run install:all     # server + web deps
npm run build           # build the SPA
npm start               # API + UI on http://localhost:8891
```

Or with hot reload during development:

```sh
npm run dev             # api on 8891, vite on 5273 (proxies /api)
```

Port note: 8787 is taken by the InvestOpediaClaude reports server on this
machine, so this API defaults to **8891**. Override with `PORT`.

## Where the data comes from

`server/lib/store.js` reads `runs/<RUN_ID>/latest/` from the **results** volume
`x3n7kgbbit` over S3, and falls back to `../results/final/` on disk if
credentials are absent. `metrics.json` is served as-is; `predictions.parquet`
is parsed in Node with `hyparquet` (pure JS, no native build).

The market-data volume `8qik4zxpxq` is never touched here — the dashboard only
reads what the pipeline already wrote. `store.js` throws at import if
`RESULTS_VOLUME_ID` is ever set to the source volume.

Credentials come from `server/.env` (gitignored), same keys as the pipeline.

## API

| Endpoint | Returns |
|---|---|
| `GET /api/health` | row count, load time, and which origin served the data |
| `GET /api/summary` | overall metrics + per-year table + date range |
| `GET /api/equity?costBps=N&tickers=A,B` | equity curve points + stats |
| `GET /api/tickers` | per-ticker leaderboard |
| `GET /api/next-session` | live paper-trade predictions |

## The equity model

Hold the share on days the model predicts up; sit in cash otherwise. Equal
weight across names, each rebased to 1.00×. A switch charges `costBps` each way,
applied only when the position actually changes — averaging ~278 switches per
stock over the window, which is why the cost toggle matters.

Buy-and-hold is the comparison because a long-only strategy **is** a worse
buy-and-hold unless it proves otherwise. It is also exactly what the always-up
baseline looks like as money.

## Charts

Built as hand-rolled SVG — no chart library — to hit the mark specs directly:
2px line strokes, 4px rounded data-ends anchored to the baseline, ≥8px hover
markers with a 2px surface ring, recessive grid and axes, direct labels at the
line ends, and a legend on every multi-series chart so identity is never carried
by colour alone. The 100-name leaderboard is a sortable table rather than a
chart, since 100 series is far past any categorical ceiling.

Palette is the dataviz reference instance. Every pair used was run through
`validate_palette.js` and passes all six checks in **both** modes:

| Use | Light | Dark | Worst CVD ΔE |
|---|---|---|---|
| model / buy-hold | `#2a78d6` `#eb6834` | `#3987e5` `#d95926` | 24.7 / 26.8 |
| beat / lost (diverging) | `#2a78d6` `#e34948` | `#3987e5` `#e66767` | 21.6 / 19.2 |

Dark mode is a selected set of steps for the dark surface, not an inverted
light palette, and the `data-theme` toggle wins over the OS setting in both
directions.
