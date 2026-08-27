import 'dotenv/config'
import express from 'express'
import cors from 'cors'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { getData } from './lib/store.js'
import { equityCurve, tickerTable, yearTable } from './lib/compute.js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const app = express()
app.use(cors())

const wrap = fn => (req, res) => fn(req, res).catch(err => {
  console.error(err)
  res.status(500).json({ error: err.message })
})

app.get('/api/health', wrap(async (_req, res) => {
  const d = await getData()
  res.json({ ok: true, rows: d.rows.length, loadedAt: d.loadedAt,
             fromVolume: d.fromVolume, origin: d.origin })
}))

app.get('/api/summary', wrap(async (_req, res) => {
  const d = await getData()
  const o = d.metrics.overall || {}
  const bySource = d.rows.reduce((a, r) => { a[r.source || 'backtest'] = (a[r.source || 'backtest'] || 0) + 1; return a }, {})
  res.json({
    overall: o,
    liveOnly: d.metrics.live_only || null,
    bySource,
    byYear: yearTable(d.metrics),
    nTickers: new Set(d.rows.map(r => r.ticker)).size,
    range: { start: d.rows[0]?.date, end: d.rows[d.rows.length - 1]?.date },
    origin: d.origin, fromVolume: d.fromVolume, loadedAt: d.loadedAt,
  })
}))

app.get('/api/equity', wrap(async (req, res) => {
  const d = await getData()
  const costBps = Number(req.query.costBps ?? 0)
  const tickers = req.query.tickers ? String(req.query.tickers).split(',').filter(Boolean) : null
  res.json(equityCurve(d.rows, { costBps, tickers }))
}))

app.get('/api/tickers', wrap(async (_req, res) => {
  const d = await getData()
  res.json(tickerTable(d.metrics))
}))

app.get('/api/next-session', wrap(async (_req, res) => {
  const d = await getData()
  const rows = [...d.next].sort((a, b) => b.pred - a.pred)
  res.json({
    forSession: rows[0]?.forSession ?? null,
    asOf: rows[0]?.asOf ?? null,
    up: rows.filter(r => r.pred > 0).length,
    down: rows.filter(r => r.pred <= 0).length,
    rows,
  })
}))

// Serve the built SPA when it exists.
const dist = path.resolve(__dirname, '../web/dist')
app.use(express.static(dist))
app.get('*', (_req, res) => res.sendFile(path.join(dist, 'index.html'), err => {
  if (err) res.status(404).json({ error: 'UI not built — run: npm run build' })
}))

const port = process.env.PORT || 8891
app.listen(port, () => console.log(`api on http://localhost:${port}`))
