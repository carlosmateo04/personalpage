import {
  formatBitrate,
  formatUptime,
  isActive,
  PLATFORM_NAMES,
  type Destination,
  type DestinationStatus,
} from '../types'
import { PlatformIcon } from './PlatformIcon'

const STATUS_TEXT: Record<DestinationStatus, string> = {
  idle: 'Ready',
  connecting: 'Connecting',
  live: 'Live',
  degraded: 'Struggling',
  reconnecting: 'Reconnecting',
  failed: 'Problem',
}

export function DestinationCard({
  destination: d,
  onToggle,
  onFix,
}: {
  destination: Destination
  onToggle: (id: string) => void
  onFix: (id: string) => void
}) {
  const active = isActive(d.status)

  return (
    <article className={`dest dest-${d.status}`}>
      <header className="dest-head">
        <span className={`dest-icon plat-${d.platform}`}>
          <PlatformIcon platform={d.platform} />
        </span>
        <div className="dest-id">
          <h3>{d.label}</h3>
          <p>
            {PLATFORM_NAMES[d.platform]} &middot; {d.account}
          </p>
        </div>
      </header>

      <div className="dest-status">
        <span className={`beacon beacon-${d.status}`} aria-hidden="true" />
        <span className="dest-status-text">{STATUS_TEXT[d.status]}</span>
        {d.status === 'live' || d.status === 'degraded' ? (
          <span className="dest-uptime">{formatUptime(d.uptime)}</span>
        ) : null}
      </div>

      {d.issue ? (
        <div className="dest-issue">
          <p className="issue-title">{d.issue.title}</p>
          <p className="issue-detail">{d.issue.detail}</p>
          {d.issue.action && (
            <button className="btn-fix" onClick={() => onFix(d.id)}>
              {d.issue.action.label}
            </button>
          )}
        </div>
      ) : (
        <div className="dest-metrics">
          <div>
            <span className="metric-value">{active ? formatBitrate(d.bitrate) : '—'}</span>
            <span className="metric-label">Upload</span>
          </div>
          <div>
            <span className="metric-value">{active ? d.dropped.toLocaleString() : '—'}</span>
            <span className="metric-label">Dropped</span>
          </div>
        </div>
      )}

      <button
        className={`btn-dest ${active ? 'stop' : 'start'}`}
        onClick={() => onToggle(d.id)}
        disabled={d.status === 'connecting'}
      >
        {d.status === 'connecting' ? 'Starting…' : active ? 'Stop' : 'Start'}
      </button>
    </article>
  )
}

export function AddDestinationCard({ onClick }: { onClick: () => void }) {
  return (
    <button className="dest dest-add" onClick={onClick}>
      <span className="add-plus" aria-hidden="true">
        +
      </span>
      <span className="add-label">Add destination</span>
      <span className="add-hint">Sign in, or paste a stream key</span>
    </button>
  )
}
