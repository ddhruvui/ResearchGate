import { useMemo, useRef, useState } from 'react'

/**
 * Two-series line chart with crosshair + tooltip.
 * Marks: 2px strokes, recessive grid, direct labels at the right edge, and a
 * legend — identity is never carried by colour alone.
 */
export default function EquityChart ({ points, height = 340 }) {
  const wrapRef = useRef(null)
  const [hover, setHover] = useState(null)
  const W = 980, H = height
  const M = { top: 16, right: 96, bottom: 30, left: 56 }

  const geom = useMemo(() => {
    if (!points?.length) return null
    const vals = points.flatMap(p => [p.model, p.hold])
    const lo = Math.min(...vals), hi = Math.max(...vals)
    const pad = (hi - lo) * 0.08 || 0.1
    const y0 = Math.max(0, lo - pad), y1 = hi + pad
    const x = i => M.left + (i / (points.length - 1)) * (W - M.left - M.right)
    const y = v => M.top + (1 - (v - y0) / (y1 - y0)) * (H - M.top - M.bottom)
    const line = key => points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p[key]).toFixed(1)}`).join('')
    const ticks = []
    const step = niceStep((y1 - y0) / 5)
    for (let v = Math.ceil(y0 / step) * step; v <= y1; v += step) ticks.push(v)
    const years = []
    points.forEach((p, i) => {
      const yr = p.date.slice(0, 4)
      if (!years.length || years[years.length - 1].year !== yr) years.push({ year: yr, i })
    })
    return { x, y, line, ticks, years, y0, y1 }
  }, [points, H])

  if (!geom) return <div style={{ color: 'var(--text-muted)', fontSize: 13 }}>No data</div>
  const last = points[points.length - 1]

  const onMove = e => {
    const svg = e.currentTarget
    const r = svg.getBoundingClientRect()
    const px = ((e.clientX - r.left) / r.width) * W
    const t = (px - M.left) / (W - M.left - M.right)
    const i = Math.round(t * (points.length - 1))
    if (i >= 0 && i < points.length) setHover({ i, clientX: e.clientX - r.left, rectW: r.width })
  }

  return (
    <div className="chart-wrap" ref={wrapRef}>
      <div className="legend">
        <span className="item"><span className="swatch" style={{ background: 'var(--series-1)' }} />Following the model</span>
        <span className="item"><span className="swatch" style={{ background: 'var(--series-2)' }} />Buy &amp; hold</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} onMouseMove={onMove}
           onMouseLeave={() => setHover(null)} style={{ display: 'block', minWidth: 620 }}
           role="img" aria-label="Growth of 1 dollar per stock, model versus buy and hold">
        {geom.ticks.map(v => (
          <g key={v}>
            <line x1={M.left} x2={W - M.right} y1={geom.y(v)} y2={geom.y(v)}
                  stroke="var(--grid)" strokeWidth="1" />
            <text x={M.left - 9} y={geom.y(v) + 4} textAnchor="end" fontSize="11"
                  fill="var(--text-muted)">{v.toFixed(1)}×</text>
          </g>
        ))}
        <line x1={M.left} x2={M.left} y1={M.top} y2={H - M.bottom} stroke="var(--axis)" strokeWidth="1" />
        {geom.years.map(({ year, i }) => (
          <text key={year} x={geom.x(i)} y={H - 10} fontSize="11" fill="var(--text-muted)"
                textAnchor="middle">{year}</text>
        ))}
        {/* 1.0 reference — break-even */}
        <line x1={M.left} x2={W - M.right} y1={geom.y(1)} y2={geom.y(1)}
              stroke="var(--axis)" strokeWidth="1" strokeDasharray="3 3" />
        <path d={geom.line('hold')} fill="none" stroke="var(--series-2)" strokeWidth="2"
              strokeLinejoin="round" strokeLinecap="round" />
        <path d={geom.line('model')} fill="none" stroke="var(--series-1)" strokeWidth="2"
              strokeLinejoin="round" strokeLinecap="round" />
        {/* direct labels — identity without relying on colour */}
        <text x={W - M.right + 8} y={geom.y(last.hold) + 4} fontSize="12" fill="var(--text-secondary)">
          {last.hold.toFixed(2)}× hold
        </text>
        <text x={W - M.right + 8} y={geom.y(last.model) + 4} fontSize="12" fill="var(--text-secondary)">
          {last.model.toFixed(2)}× model
        </text>
        {hover && (
          <g>
            <line x1={geom.x(hover.i)} x2={geom.x(hover.i)} y1={M.top} y2={H - M.bottom}
                  stroke="var(--axis)" strokeWidth="1" />
            <circle cx={geom.x(hover.i)} cy={geom.y(points[hover.i].hold)} r="4.5"
                    fill="var(--series-2)" stroke="var(--surface-1)" strokeWidth="2" />
            <circle cx={geom.x(hover.i)} cy={geom.y(points[hover.i].model)} r="4.5"
                    fill="var(--series-1)" stroke="var(--surface-1)" strokeWidth="2" />
          </g>
        )}
      </svg>
      {hover && (
        <div className="tooltip" style={{
          left: Math.min(Math.max(hover.clientX + 14, 0), (hover.rectW || 900) - 170), top: 34,
        }}>
          <div className="t-date">{points[hover.i].date}</div>
          <div className="t-row"><span style={{ color: 'var(--series-1)' }}>Model</span>
            <strong>{points[hover.i].model.toFixed(3)}×</strong></div>
          <div className="t-row"><span style={{ color: 'var(--series-2)' }}>Buy &amp; hold</span>
            <strong>{points[hover.i].hold.toFixed(3)}×</strong></div>
        </div>
      )}
    </div>
  )
}

function niceStep (raw) {
  const p = Math.pow(10, Math.floor(Math.log10(raw)))
  const n = raw / p
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p
}
