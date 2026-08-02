import type { Incident } from '../engine'
import type { Destination } from '../types'

const KIND_LABEL: Record<string, string> = {
  reconnect: 'Reconnected',
  failed: 'Failed',
  stopped: 'Stopped',
}

function clock(ms: number): string {
  const d = new Date(ms)
  const pad = (n: number) => n.toString().padStart(2, '0')
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function day(ms: number): string {
  return new Date(ms).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })
}

/**
 * What happened while nobody was watching.
 *
 * The whole point of an unattended stream is that the interesting moments
 * happen at four in the morning, so they have to be recoverable afterwards
 * rather than only visible as they occur.
 */
export function IncidentLog({
  incidents,
  destinations,
  onClose,
}: {
  incidents: Incident[]
  destinations: Destination[]
  onClose: () => void
}) {
  const nameOf = (id: string) => destinations.find((d) => d.id === id)?.label ?? id
  const newestFirst = [...incidents].reverse()

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <div>
            <h2>Activity</h2>
            <p>
              Disconnections, recoveries, and failures since the app opened — the events worth
              knowing about after an overnight run.
            </p>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>

        <div className="modal-body">
          {newestFirst.length === 0 ? (
            <p className="empty">Nothing has happened yet. That is the good outcome.</p>
          ) : (
            <ol className="log">
              {newestFirst.map((incident, i) => (
                <li key={`${incident.at}-${i}`} className={`log-row log-${incident.kind}`}>
                  <span className="log-time" title={day(incident.at)}>
                    {clock(incident.at)}
                  </span>
                  <span className={`log-kind log-kind-${incident.kind}`}>
                    {KIND_LABEL[incident.kind] ?? incident.kind}
                  </span>
                  <span className="log-where">{nameOf(incident.id)}</span>
                  <span className="log-what">{incident.message}</span>
                </li>
              ))}
            </ol>
          )}
        </div>

        <footer className="modal-foot">
          <span className="modal-total">
            {newestFirst.length} {newestFirst.length === 1 ? 'event' : 'events'} &middot; kept in
            memory for this session
          </span>
        </footer>
      </div>
    </div>
  )
}
