import { isActive, PLATFORM_NAMES, type PlatformGroup } from '../types'
import { PlatformIcon } from './PlatformIcon'
import { DestinationCard } from './DestinationCard'

/**
 * One platform, all of its connected accounts. The header carries the account
 * count and a single control for the whole platform, so "go live on both
 * YouTube channels" is one click rather than two.
 */
export function PlatformSection({
  group,
  onToggle,
  onToggleAll,
  onFix,
}: {
  group: PlatformGroup
  onToggle: (id: string) => void
  onToggleAll: (ids: string[]) => void
  onFix: (id: string) => void
}) {
  const { platform, destinations } = group
  const liveHere = destinations.filter((d) => isActive(d.status)).length
  const allIds = destinations.map((d) => d.id)
  const count = destinations.length

  return (
    <section className="pgroup">
      <header className="pgroup-head">
        <span className={`pgroup-icon plat-${platform}`}>
          <PlatformIcon platform={platform} size={17} />
        </span>
        <h2>{PLATFORM_NAMES[platform]}</h2>
        <span className="pgroup-count">
          {count} {count === 1 ? 'account' : 'accounts'}
          {liveHere > 0 && <span className="pgroup-live"> · {liveHere} live</span>}
        </span>
        {count > 1 && (
          <button className="pgroup-action" onClick={() => onToggleAll(allIds)}>
            {liveHere > 0 ? 'Stop all' : 'Start all'}
          </button>
        )}
      </header>

      <div className="pgroup-grid">
        {destinations.map((d) => (
          <DestinationCard key={d.id} destination={d} onToggle={onToggle} onFix={onFix} />
        ))}
      </div>
    </section>
  )
}
