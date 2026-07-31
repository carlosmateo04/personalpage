import { formatBitrate } from '../types'

/**
 * Compact always-visible bandwidth strip. Local fan-out sends one full copy
 * per destination, so headroom is the number that decides whether another
 * platform can be added — it gets the prominent treatment.
 */
export function BandwidthBar({
  usedKbps,
  capacityKbps,
  history,
}: {
  usedKbps: number
  capacityKbps: number
  history: number[]
}) {
  const pct = Math.min(100, Math.round((usedKbps / capacityKbps) * 100))
  const level = pct >= 85 ? 'bad' : pct >= 65 ? 'warn' : 'good'
  const peak = Math.max(capacityKbps, ...history, 1)

  return (
    <section className={`bw bw-${level}`}>
      <div className="bw-left">
        <span className="bw-value">{formatBitrate(usedKbps)}</span>
        <span className="bw-label">Uploading now</span>
      </div>

      <div className="bw-graph" aria-hidden="true">
        {history.map((v, i) => (
          <span key={i} className="bw-bar" style={{ height: `${Math.max(3, (v / peak) * 100)}%` }} />
        ))}
      </div>

      <div className="bw-right">
        <span className="bw-value">{100 - pct}%</span>
        <span className="bw-label">Headroom left</span>
      </div>
    </section>
  )
}
