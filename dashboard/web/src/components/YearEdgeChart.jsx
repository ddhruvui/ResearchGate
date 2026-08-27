import { useState } from 'react'

/**
 * Diverging bar: edge over the always-up baseline, per year.
 * Polarity is the job, so the palette's diverging pair (blue ↔ red) is used with
 * a neutral zero line. Bars carry 4px rounded data-ends anchored to the baseline.
 */
export default function YearEdgeChart ({ rows }) {
  const [hover, setHover] = useState(null)
  if (!rows?.length) return null
  const W = 980, H = 220
  const M = { top: 18, right: 20, bottom: 34, left: 56 }
  const maxAbs = Math.max(...rows.map(r => Math.abs(r.edge))) * 1.25 || 0.02
  const bw = Math.min(74, (W - M.left - M.right) / rows.length - 14)
  const x = i => M.left + (i + 0.5) * ((W - M.left - M.right) / rows.length)
  const y = v => M.top + (1 - (v + maxAbs) / (2 * maxAbs)) * (H - M.top - M.bottom)
  const zero = y(0)

  return (
    <div className="chart-wrap">
      <div className="legend">
        <span className="item"><span className="swatch" style={{ background: 'var(--pos)' }} />Beat the baseline</span>
        <span className="item"><span className="swatch" style={{ background: 'var(--neg)' }} />Lost to the baseline</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} style={{ display: 'block', minWidth: 620 }}
           role="img" aria-label="Edge over the always-up baseline by year">
        {[-maxAbs / 2, 0, maxAbs / 2].map(v => (
          <g key={v}>
            <line x1={M.left} x2={W - M.right} y1={y(v)} y2={y(v)} stroke="var(--grid)" strokeWidth="1" />
            <text x={M.left - 9} y={y(v) + 4} textAnchor="end" fontSize="11" fill="var(--text-muted)">
              {(v * 100).toFixed(1)}pp
            </text>
          </g>
        ))}
        <line x1={M.left} x2={W - M.right} y1={zero} y2={zero} stroke="var(--axis)" strokeWidth="1.5" />
        {rows.map((r, i) => {
          const top = r.edge >= 0 ? y(r.edge) : zero
          const h = Math.max(2, Math.abs(zero - y(r.edge)))
          const col = r.edge >= 0 ? 'var(--pos)' : 'var(--neg)'
          return (
            <g key={r.year} onMouseEnter={() => setHover(r)} onMouseLeave={() => setHover(null)}>
              <rect x={x(i) - bw / 2 - 6} y={M.top} width={bw + 12} height={H - M.top - M.bottom} fill="transparent" />
              <rect x={x(i) - bw / 2} y={top} width={bw} height={h} fill={col} rx="4" ry="4" />
              <text x={x(i)} y={H - 12} textAnchor="middle" fontSize="12" fill="var(--text-secondary)">{r.year}</text>
              <text x={x(i)} y={r.edge >= 0 ? top - 7 : top + h + 15} textAnchor="middle" fontSize="11.5"
                    fill="var(--text-secondary)">{(r.edge * 100).toFixed(2)}</text>
            </g>
          )
        })}
      </svg>
      {hover && (
        <div className="tooltip" style={{ left: 62, top: 6 }}>
          <div className="t-date">{hover.year} — {hover.n.toLocaleString()} predictions</div>
          <div className="t-row"><span>Model</span><strong>{(hover.accuracy * 100).toFixed(2)}%</strong></div>
          <div className="t-row"><span>Always-up</span><strong>{(hover.baseline * 100).toFixed(2)}%</strong></div>
          <div className="t-row"><span>Edge</span>
            <strong className={hover.edge >= 0 ? 'pos' : 'neg'}>{(hover.edge * 100).toFixed(2)}pp</strong></div>
        </div>
      )}
    </div>
  )
}
