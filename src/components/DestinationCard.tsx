import {
  currentClip,
  formatBitrate,
  formatDuration,
  formatUptime,
  isActive,
  PLATFORM_NAMES,
  totalDuration,
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
  onEditSource,
  onEditAccount,
}: {
  destination: Destination
  onToggle: (id: string) => void
  onFix: (id: string) => void
  onEditSource: (id: string) => void
  onEditAccount: (id: string) => void
}) {
  const active = isActive(d.status)
  const playlist = d.source.kind === 'playlist' ? d.source : null
  const playing = playlist && active ? currentClip(playlist.clips, playlist.loop, d.uptime) : null

  return (
    <article className={`dest dest-${d.status}`} data-id={d.id}>
      <button
        className="dest-head"
        onClick={() => onEditAccount(d.id)}
        title="Account settings"
      >
        <span className={`dest-icon plat-${d.platform}`}>
          <PlatformIcon platform={d.platform} />
        </span>
        <span className="dest-id">
          <span className="dest-name">
            <h3>{d.label}</h3>
            {d.auth.method === 'oauth' && !d.auth.needsReauth && (
              <span className="auth-badge" title={`Signed in with ${d.auth.provider}`}>
                ✓
              </span>
            )}
            {d.auth.method === 'oauth' && d.auth.needsReauth && (
              <span className="auth-badge warn" title="Sign-in expired">
                !
              </span>
            )}
          </span>
          <p>
            {PLATFORM_NAMES[d.platform]} &middot; {d.account}
          </p>
        </span>
      </button>

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
        <p className="dest-metrics">
          {active ? (
            <>
              <strong>{formatBitrate(d.bitrate)}</strong> up &middot; {d.dropped.toLocaleString()}{' '}
              dropped
            </>
          ) : (
            'Not streaming'
          )}
        </p>
      )}

      <button
        className={`source-chip ${playlist ? 'is-playlist' : 'is-live'}`}
        onClick={() => onEditSource(d.id)}
        title="Change what this account streams"
      >
        {playlist ? (
          playing ? (
            <>
              <span className="chip-icon" aria-hidden="true">
                ▶
              </span>
              <span className="chip-main" title={playing.clip.name}>
                {playing.clip.name}
              </span>
              <span className="chip-sub">
                {playing.index + 1}/{playlist.clips.length}
              </span>
            </>
          ) : (
            <>
              <span className="chip-icon" aria-hidden="true">
                ≡
              </span>
              <span className="chip-main">
                {playlist.clips.length} {playlist.clips.length === 1 ? 'video' : 'videos'} on loop
              </span>
              <span className="chip-sub">{formatDuration(totalDuration(playlist.clips))}</span>
            </>
          )
        ) : (
          <>
            <span className="chip-icon" aria-hidden="true">
              ◉
            </span>
            <span className="chip-main">Live feed</span>
            <span className="chip-sub">shared</span>
          </>
        )}
      </button>

      {playing && (
        <div
          className="clip-progress"
          aria-hidden="true"
          title={`${formatDuration(playing.into)} of ${formatDuration(playing.clip.duration)}`}
        >
          <span style={{ width: `${(playing.into / playing.clip.duration) * 100}%` }} />
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
