import { useEffect, useRef, useState } from 'react'
import type { Bandwidth } from '../reduce'
import { formatBitrate } from '../types'

/** One second of history: what left the machine, and our share of it. */
export type BwSample = { total: number; streams: number }

/**
 * Compact always-visible bandwidth strip.
 *
 * The headline is what is actually leaving the machine, measured at the network
 * interface — not the sum of what ffmpeg says it is sending. Those differ
 * whenever anything else on the Mac is uploading, which is precisely when the
 * headroom figure matters. The breakdown underneath shows which is which.
 */
export function BandwidthBar({
  bw,
  history,
  iface,
  activeCount,
  onSetUplink,
}: {
  bw: Bandwidth
  history: BwSample[]
  iface: string
  activeCount: number
  onSetUplink: (kbps: number | null) => void
}) {
  const level = bw.usedPct >= 85 ? 'bad' : bw.usedPct >= 65 ? 'warn' : 'good'
  const peak = Math.max(bw.capacityKbps, ...history.map((h) => h.total), 1)

  return (
    <section className={`bw bw-${level}`}>
      <div className="bw-row">
        <div className="bw-left">
          <span className="bw-value">{formatBitrate(bw.totalKbps)}</span>
          <span className="bw-label">
            {bw.measured ? 'Leaving this Mac' : 'Reported by streams'}
          </span>
        </div>

        <div className="bw-graph" aria-hidden="true">
          {history.map((h, i) => (
            <span key={i} className="bw-bar" style={{ height: `${bar(h.total, peak)}%` }}>
              {/* Our share, drawn from the bottom, so the gap above it is
                  everything else competing for the same uplink. */}
              <span
                className="bw-bar-mine"
                style={{ height: `${h.total > 0 ? Math.min(100, (h.streams / h.total) * 100) : 0}%` }}
              />
            </span>
          ))}
        </div>

        <div className="bw-right">
          <span className="bw-value">{formatBitrate(bw.freeKbps)}</span>
          <span className="bw-label">Upload free &middot; {100 - bw.usedPct}%</span>
        </div>
      </div>

      <div className="bw-breakdown">
        <span className="bw-key bw-key-mine" />
        <span>
          Streams <strong>{formatBitrate(bw.streamsKbps)}</strong>
          {activeCount > 0 && ` (${activeCount})`}
        </span>

        <span className="bw-key bw-key-other" />
        <span>
          Other apps{' '}
          {/* Without counters this is not zero, it is unknown. Printing
              "0 kbps" would claim nothing else is uploading. */}
          <strong>{bw.measured ? formatBitrate(bw.otherKbps) : '—'}</strong>
        </span>

        <UplinkField capacityKbps={bw.capacityKbps} onSet={onSetUplink} />

        <span className="bw-note">
          {bw.measured
            ? `measured on ${iface}`
            : 'interface counters unavailable — other apps are not counted'}
        </span>
      </div>
    </section>
  )
}

function bar(v: number, peak: number): number {
  return Math.max(3, Math.min(100, (v / peak) * 100))
}

/**
 * The link ceiling. It has to be editable: nothing on the machine can discover
 * what the ISP sells, and every "free" figure is measured against it. Shown
 * with its provenance so a default is never mistaken for a measurement.
 */
function UplinkField({
  capacityKbps,
  onSet,
}: {
  capacityKbps: number
  onSet: (kbps: number | null) => void
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (editing) input.current?.select()
  }, [editing])

  const commit = () => {
    const mbps = Number(draft)
    onSet(Number.isFinite(mbps) && mbps > 0 ? Math.round(mbps * 1000) : null)
    setEditing(false)
  }

  if (editing) {
    return (
      <span className="bw-uplink-edit">
        <input
          ref={input}
          type="number"
          min={1}
          step={1}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commit()
            if (e.key === 'Escape') setEditing(false)
          }}
          onBlur={commit}
          aria-label="Upload speed in Mbps"
        />
        <span>Mbps up &middot; blank to reset</span>
      </span>
    )
  }

  return (
    <button
      className="bw-uplink"
      onClick={() => {
        setDraft(String(Math.round(capacityKbps / 1000)))
        setEditing(true)
      }}
      title="Your plan's upload speed. Run a speed test once and enter it here."
    >
      of {formatBitrate(capacityKbps)} <span className="bw-uplink-edit-hint">edit</span>
    </button>
  )
}
