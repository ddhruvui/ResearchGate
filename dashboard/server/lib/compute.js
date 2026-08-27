/**
 * Derived views. The headline question the dashboard answers is
 * "if I had followed the model with 1 share, what would I have?" — so the
 * equity curve is the centrepiece, and it is always shown against buy-and-hold,
 * because a strategy that only ever goes long is a worse buy-and-hold unless it
 * proves otherwise.
 *
 * Strategy: hold the share on days the model predicts UP; sit in cash otherwise.
 * A round trip costs `costBps` each way, charged when the position CHANGES.
 */

export function equityCurve (rows, { costBps = 0, tickers = null } = {}) {
  const use = tickers && tickers.length
    ? rows.filter(r => tickers.includes(r.ticker))
    : rows
  if (!use.length) return { points: [], stats: null }

  // Per-ticker running equity; the portfolio is the equal-weight mean.
  const model = new Map()
  const hold = new Map()
  const prevPos = new Map()
  const trades = new Map()

  const byDate = new Map()
  for (const r of use) {
    if (!byDate.has(r.date)) byDate.set(r.date, [])
    byDate.get(r.date).push(r)
  }
  const dates = [...byDate.keys()].sort()

  const points = []
  const cost = costBps / 10000
  for (const d of dates) {
    for (const r of byDate.get(d)) {
      if (!model.has(r.ticker)) { model.set(r.ticker, 1); hold.set(r.ticker, 1); prevPos.set(r.ticker, 0); trades.set(r.ticker, 0) }
      const pos = r.pred > 0 ? 1 : 0
      let m = model.get(r.ticker)
      if (pos !== prevPos.get(r.ticker)) {
        m *= (1 - cost)                       // entry or exit friction
        trades.set(r.ticker, trades.get(r.ticker) + 1)
        prevPos.set(r.ticker, pos)
      }
      m *= (1 + pos * r.actual)
      model.set(r.ticker, m)
      hold.set(r.ticker, hold.get(r.ticker) * (1 + r.actual))
    }
    points.push({
      date: d,
      model: mean([...model.values()]),
      hold: mean([...hold.values()]),
    })
  }

  const last = points[points.length - 1]
  const nT = model.size
  const years = (new Date(last.date) - new Date(points[0].date)) / 3.15576e10
  const stats = {
    nTickers: nT,
    nDays: points.length,
    start: points[0].date,
    end: last.date,
    modelFinal: last.model,
    holdFinal: last.hold,
    modelCagr: cagr(1, last.model, years),
    holdCagr: cagr(1, last.hold, years),
    modelMaxDD: maxDrawdown(points.map(p => p.model)),
    holdMaxDD: maxDrawdown(points.map(p => p.hold)),
    tradesPerTicker: mean([...trades.values()]),
    costBps,
  }
  return { points, stats }
}

/** Per-ticker leaderboard, joined with the pre-computed metrics block. */
export function tickerTable (metrics) {
  const bt = metrics.by_ticker || {}
  return Object.entries(bt)
    .filter(([, b]) => b && b.n)
    .map(([ticker, b]) => ({
      ticker,
      n: b.n,
      accuracy: b.direction_accuracy,
      baseline: b.baseline_always_up,
      edge: b.edge_vs_always_up,
      mseRatio: b.mse_model / b.mse_zero_baseline,
    }))
    .sort((a, b) => b.edge - a.edge)
}

export function yearTable (metrics) {
  return Object.entries(metrics.by_year || {})
    .filter(([, b]) => b && b.n)
    .map(([year, b]) => ({
      year: Number(year),
      n: b.n,
      accuracy: b.direction_accuracy,
      baseline: b.baseline_always_up,
      edge: b.edge_vs_always_up,
    }))
    .sort((a, b) => a.year - b.year)
}

const mean = a => a.reduce((s, x) => s + x, 0) / (a.length || 1)
const cagr = (from, to, years) => (years > 0 ? Math.pow(to / from, 1 / years) - 1 : 0)

function maxDrawdown (series) {
  let peak = -Infinity, dd = 0
  for (const v of series) { if (v > peak) peak = v; dd = Math.min(dd, v / peak - 1) }
  return dd
}
