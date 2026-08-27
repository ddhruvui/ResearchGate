export default function StatTile ({ label, value, delta, tone }) {
  return (
    <div className="tile">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {delta && <div className={`delta ${tone || ''}`}>{delta}</div>}
    </div>
  )
}
