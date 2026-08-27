import { useEffect, useState } from 'react'
import StatTile from './components/StatTile.jsx'
import EquityChart from './components/EquityChart.jsx'
import YearEdgeChart from './components/YearEdgeChart.jsx'
import TickerTable from './components/TickerTable.jsx'
import NextSession from './components/NextSession.jsx'

const COSTS = [0, 1, 5, 10]
const pct = v => `${(v * 100).toFixed(2)}%`
const j = u => fetch(u).then(r => r.json())

export default function App () {
  const [summary, setSummary] = useState(null)
  const [tickers, setTickers] = useState([])
  const [next, setNext] = useState(null)
  const [costBps, setCostBps] = useState(0)
  const [equity, setEquity] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    Promise.all([j('/api/summary'), j('/api/tickers'), j('/api/next-session')])
      .then(([s, t, n]) => { setSummary(s); setTickers(t); setNext(n) })
      .catch(e => setErr(e.message))
  }, [])

  useEffect(() => {
    setEquity(null)
    j(`/api/equity?costBps=${costBps}`).then(setEquity).catch(e => setErr(e.message))
  }, [costBps])

  if (err) return <div className="wrap"><h1>Dashboard</h1><p className="sub">API error: {err}</p></div>
  if (!summary) return <div className="wrap"><p className="sub">Loading…</p></div>

  const o = summary.overall
  const st = equity?.stats
  const beatsBaseline = o.edge_vs_always_up > 0
  const mseRatio = o.mse_model / o.mse_zero_baseline

  return (
    <div className="wrap">
      {!summary.fromVolume && (
        <div className="card" style={{
          borderColor: 'var(--critical)', background: 'transparent', marginBottom: 18,
        }}>
          <h2 style={{ color: 'var(--critical)' }}>Showing a local backup, not the results volume</h2>
          <p className="note" style={{ margin: 0 }}>
            The results volume returned nothing, so this is the last copy saved to disk —
            it may be from an <strong>older run with different settings</strong>. Source:{' '}
            <code>{summary.origin?.predictions}</code>
          </p>
        </div>
      )}
      <h1>PSO + LS-SVM — did it beat the guess?</h1>
      <p className="sub">
        {o.n.toLocaleString()} graded next-session predictions · {summary.nTickers} S&amp;P 500 names ·
        {' '}{summary.range.start} → {summary.range.end}
        <br />
        <span style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>
          {summary.fromVolume ? 'from the results volume' : 'from a local backup'} ·
          loaded {new Date(summary.loadedAt).toLocaleTimeString()}
        </span>
      </p>

      <div className="kpis">
        <StatTile label="Direction accuracy" value={pct(o.direction_accuracy)}
                  delta={`vs coin flip 50.00% · p=${Number(o.p_value_vs_coin).toExponential(1)}`} tone="pos" />
        <StatTile label="Always-up baseline" value={pct(o.baseline_always_up)}
                  delta="the free alternative" />
        <StatTile label="Edge over baseline"
                  value={`${o.edge_vs_always_up >= 0 ? '+' : ''}${(o.edge_vs_always_up * 100).toFixed(2)}pp`}
                  delta={beatsBaseline ? 'model wins' : 'model loses'}
                  tone={beatsBaseline ? 'pos' : 'neg'} />
        <StatTile label="Error vs predicting zero" value={`${mseRatio.toFixed(3)}×`}
                  delta={mseRatio <= 1 ? 'better than no-change' : 'worse than no-change'}
                  tone={mseRatio <= 1 ? 'pos' : 'neg'} />
      </div>

      <div className="card">
        <h2>If you had put $1 into each stock and followed the model</h2>
        <p className="note">
          Hold the share on days the model predicts up; sit in cash otherwise. Equal weight
          across every name, rebased to 1.00×. Buy &amp; hold is the same dollar left invested
          throughout — it is what the always-up baseline looks like as money.
        </p>
        <div className="controls">
          <label>Trading cost per switch</label>
          {COSTS.map(c => (
            <button key={c} className="seg" aria-pressed={costBps === c} onClick={() => setCostBps(c)}>
              {c === 0 ? 'none' : `${c} bps`}
            </button>
          ))}
        </div>
        {equity ? <EquityChart points={equity.points} /> :
          <p className="note" style={{ margin: 0 }}>Computing…</p>}
        {st && (
          <div className="kpis" style={{ marginTop: 20, marginBottom: 0 }}>
            <StatTile label="Model, final" value={`${st.modelFinal.toFixed(2)}×`}
                      delta={`${(st.modelCagr * 100).toFixed(1)}% a year`} />
            <StatTile label="Buy & hold, final" value={`${st.holdFinal.toFixed(2)}×`}
                      delta={`${(st.holdCagr * 100).toFixed(1)}% a year`} />
            <StatTile label="Model vs hold"
                      value={`${((st.modelFinal / st.holdFinal - 1) * 100).toFixed(1)}%`}
                      delta={st.modelFinal >= st.holdFinal ? 'ahead' : 'behind'}
                      tone={st.modelFinal >= st.holdFinal ? 'pos' : 'neg'} />
            <StatTile label="Switches per stock" value={Math.round(st.tradesPerTicker).toLocaleString()}
                      delta="each one pays the cost above" />
          </div>
        )}
      </div>

      <div className="card">
        <h2>Edge over the always-up baseline, by year</h2>
        <p className="note">
          Above zero means the model called direction better than simply saying &ldquo;up&rdquo; every day.
          A pooled average can hide a model that only works in calm markets, so it is split by year.
        </p>
        <YearEdgeChart rows={summary.byYear} />
      </div>

      <div className="card">
        <h2>Every stock</h2>
        <p className="note">
          <strong>Direction accuracy</strong> is how often that stock&rsquo;s up/down call was right.
          Compare it with the <strong>always-up baseline</strong> beside it — the free alternative for
          that same stock — and <strong>edge</strong> is the difference. Click any header to sort;
          hover a header for what it means.
        </p>
        <TickerTable rows={tickers} />
      </div>

      {summary.liveOnly?.n > 0 && (
        <div className="card">
          <h2>Live track record</h2>
          <p className="note">
            Only predictions the model actually made in advance and had graded the next
            day — <strong>{summary.liveOnly.n.toLocaleString()}</strong> of{' '}
            {(summary.bySource?.backtest || 0).toLocaleString()} replayed rows. This is the
            honest forward record; the replay above is a rehearsal.
          </p>
          <div className="kpis" style={{ marginBottom: 0 }}>
            <StatTile label="Live direction accuracy" value={pct(summary.liveOnly.direction_accuracy)}
                      delta={`${summary.liveOnly.n.toLocaleString()} graded live`} />
            <StatTile label="Always-up baseline" value={pct(summary.liveOnly.baseline_always_up)}
                      delta="same days, free alternative" />
            <StatTile label="Live edge"
                      value={`${summary.liveOnly.edge_vs_always_up >= 0 ? '+' : ''}${(summary.liveOnly.edge_vs_always_up * 100).toFixed(2)}pp`}
                      delta={summary.liveOnly.edge_vs_always_up > 0 ? 'ahead' : 'behind'}
                      tone={summary.liveOnly.edge_vs_always_up > 0 ? 'pos' : 'neg'} />
          </div>
        </div>
      )}

      <div className="card">
        <h2>Tomorrow&rsquo;s prediction</h2>
        <p className="note">Live paper trade — no answer exists yet. It gets graded on the next run.</p>
        <NextSession data={next} />
      </div>

      <div className="card">
        <h2>Reading this</h2>
        <p className="verdict">
          The model beats a coin flip with an overwhelming p-value, and that is the wrong benchmark.
          Stocks drift up, so predicting &ldquo;up&rdquo; every day earns{' '}
          <strong>{pct(o.baseline_always_up)}</strong> for free — and the model reaches{' '}
          <strong>{pct(o.direction_accuracy)}</strong>, leaving it{' '}
          <strong className={beatsBaseline ? 'pos' : 'neg'}>
            {(o.edge_vs_always_up * 100).toFixed(2)}pp
          </strong>{' '}
          {beatsBaseline ? 'ahead of' : 'behind'} the free alternative. Its squared error is{' '}
          <strong>{mseRatio.toFixed(3)}×</strong> that of simply predicting no change.
          The equity curve above says the same thing in money.
        </p>
      </div>
    </div>
  )
}
