import { useMemo, useState } from 'react'

/**
 * Live paper-trade output: the one prediction nobody knows the answer to yet.
 * Shows the close it was computed from and the level the model implies, so the
 * number can be checked against a quote screen tomorrow.
 */
export default function NextSession ({ data }) {
  const [sort, setSort] = useState({ key: 'pred', dir: 'desc' })
  const [q, setQ] = useState('')
  const [dir, setDir] = useState('all')

  const rows = data?.rows ?? []
  const view = useMemo(() => {
    const f = q.trim().toUpperCase()
    let out = rows.filter(r => !f || r.ticker.includes(f))
    if (dir === 'up') out = out.filter(r => r.pred > 0)
    if (dir === 'down') out = out.filter(r => r.pred <= 0)
    const { key, dir: d } = sort
    return [...out].sort((a, b) => (d === 'asc' ? 1 : -1) *
      (typeof a[key] === 'string' ? a[key].localeCompare(b[key]) : a[key] - b[key]))
  }, [rows, q, dir, sort])

  if (!rows.length) {
    return <p className="note" style={{ margin: 0 }}>
      No live prediction available — run <code>RUN_MODE=predict scripts/launch.sh</code>.
    </p>
  }

  const th = (key, label, title) => (
    <th title={title || 'Click to sort'}
        onClick={() => setSort(s => ({ key, dir: s.key === key && s.dir === 'desc' ? 'asc' : 'desc' }))}>
      {label}{sort.key === key ? (sort.dir === 'desc' ? ' ↓' : ' ↑') : ''}
    </th>
  )

  return (
    <>
      <div className="controls">
        <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
          Close of <strong style={{ color: 'var(--text-primary)' }}>{data.asOf}</strong> →
          expected for <strong style={{ color: 'var(--text-primary)' }}>{data.forSession}</strong>
          {' · '}<strong style={{ color: 'var(--text-primary)' }}>{data.up}</strong> up
          {' / '}<strong style={{ color: 'var(--text-primary)' }}>{data.down}</strong> down
        </span>
        <span style={{ flex: 1 }} />
        <input value={q} onChange={e => setQ(e.target.value)} placeholder="Filter ticker…"
               style={{
                 font: 'inherit', fontSize: 13, padding: '6px 10px', borderRadius: 7,
                 border: '1px solid var(--border)', background: 'transparent',
                 color: 'var(--text-primary)', width: 130,
               }} />
        {['all', 'up', 'down'].map(d => (
          <button key={d} className="seg" aria-pressed={dir === d} onClick={() => setDir(d)}>{d}</button>
        ))}
      </div>

      <div className="scroll">
        <table>
          <thead>
            <tr>
              {th('ticker', 'Ticker')}
              {th('lastClose', `Close ${data.asOf}`, 'Adjusted close the prediction was computed from')}
              {th('impliedClose', 'Expected', 'Close the model implies for the next session')}
              {th('pred', 'Change')}
              <th style={{ textAlign: 'center' }}>Call</th>
            </tr>
          </thead>
          <tbody>
            {view.map(r => {
              const diff = r.impliedClose - r.lastClose
              const up = r.pred > 0
              return (
                <tr key={r.ticker}>
                  <td style={{ fontWeight: 560 }}>{r.ticker}</td>
                  <td>{fmt(r.lastClose)}</td>
                  <td style={{ fontWeight: 560 }}>{fmt(r.impliedClose)}</td>
                  <td className={up ? 'pos' : 'neg'}>
                    {up ? '+' : ''}{fmt(diff)} ({up ? '+' : ''}{(r.pred * 100).toFixed(2)}%)
                  </td>
                  <td style={{ textAlign: 'center', color: up ? 'var(--good)' : 'var(--critical)' }}>
                    {up ? '▲ UP' : '▼ DOWN'}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="note" style={{ margin: '12px 0 0' }}>
        Split- and dividend-adjusted closes. On the newest bar the adjustment factor is 1,
        so these line up with a quote screen. Expected = close × (1 + predicted return); the
        model&rsquo;s predicted moves average about a fifth of a real day&rsquo;s move, so these
        sit close to the last price by construction.
      </p>
    </>
  )
}

const fmt = v => Number.isFinite(v)
  ? v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  : '—'
