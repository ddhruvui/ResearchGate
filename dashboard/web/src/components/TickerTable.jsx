import { useMemo, useState } from 'react'

/** The table view — 100 names is past every chart's series ceiling, and a table
 *  also satisfies the accessibility requirement that identity never depends on
 *  colour alone. */
export default function TickerTable ({ rows }) {
  const [sort, setSort] = useState({ key: 'edge', dir: 'desc' })
  const [q, setQ] = useState('')
  const [onlyBeat, setOnlyBeat] = useState(false)

  const view = useMemo(() => {
    const f = q.trim().toUpperCase()
    const out = rows.filter(r => (!f || r.ticker.includes(f)) && (!onlyBeat || r.edge > 0))
    const { key, dir } = sort
    return out.sort((a, b) => (dir === 'asc' ? 1 : -1) *
      (typeof a[key] === 'string' ? a[key].localeCompare(b[key]) : a[key] - b[key]))
  }, [rows, sort, q, onlyBeat])

  const beat = rows.filter(r => r.edge > 0).length
  const th = (key, label, help) => (
    <th onClick={() => setSort(s => ({ key, dir: s.key === key && s.dir === 'desc' ? 'asc' : 'desc' }))}
        title={help ? `${help} — click to sort` : 'Click to sort'}>
      {label}{sort.key === key ? (sort.dir === 'desc' ? ' ↓' : ' ↑') : ''}
    </th>
  )

  return (
    <>
      <div className="controls">
        <input value={q} onChange={e => setQ(e.target.value)} placeholder="Filter ticker…"
               style={{
                 font: 'inherit', fontSize: 13, padding: '6px 10px', borderRadius: 7,
                 border: '1px solid var(--border)', background: 'transparent', color: 'var(--text-primary)',
               }} />
        <span style={{ fontSize: 12.5, color: 'var(--text-secondary)' }}>
          {beat} of {rows.length} beat the baseline
        </span>
        <span style={{ flex: 1 }} />
        <button className="seg" aria-pressed={onlyBeat} onClick={() => setOnlyBeat(v => !v)}>
          only those that beat it
        </button>
      </div>
      <div className="scroll">
        <table>
          <thead>
            <tr>
              {th('ticker', 'Ticker')}
              {th('n', 'Predictions', 'Graded next-session calls for this stock')}
              {th('accuracy', 'Direction accuracy',
                  'How often the model called up vs down correctly')}
              {th('baseline', 'Always-up baseline',
                  'How often simply saying "up" every day would have been right')}
              {th('edge', 'Edge',
                  'Direction accuracy minus the baseline. Above zero means the model added something')}
              {th('mseRatio', 'Error vs no-change',
                  'Model squared error over that of predicting zero. Below 1 is better than no-change')}
            </tr>
          </thead>
          <tbody>
            {view.map(r => (
              <tr key={r.ticker}>
                <td style={{ fontWeight: 560 }}>{r.ticker}</td>
                <td>{r.n.toLocaleString()}</td>
                <td>{(r.accuracy * 100).toFixed(2)}%</td>
                <td>{(r.baseline * 100).toFixed(2)}%</td>
                <td className={r.edge >= 0 ? 'pos' : 'neg'}>
                  {r.edge >= 0 ? '+' : ''}{(r.edge * 100).toFixed(2)}pp
                </td>
                <td className={r.mseRatio <= 1 ? 'pos' : 'neg'}>{r.mseRatio.toFixed(3)}×</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
