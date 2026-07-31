import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { AddDestinationCard } from './components/DestinationCard'
import { PlatformSection } from './components/PlatformSection'
import { PlaylistModal } from './components/PlaylistModal'
import { BandwidthBar } from './components/BandwidthBar'
import {
  groupByPlatform,
  isActive,
  type Destination,
  type LoopMode,
  type VideoClip,
} from './types'
import { MOCK_DESTINATIONS } from './mockDestinations'

/** Mocked uplink capacity until M4 measures it for real. */
const CAPACITY_KBPS = 25_000
const TARGET_KBPS = 6_000

export default function App() {
  const [destinations, setDestinations] = useState<Destination[]>(MOCK_DESTINATIONS)
  const [history, setHistory] = useState<number[]>(() => Array<number>(48).fill(0))
  const [version, setVersion] = useState('')
  const [editingSource, setEditingSource] = useState<string | null>(null)
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>())

  useEffect(() => {
    invoke<{ version: string }>('build_info')
      .then((i) => setVersion(i.version))
      .catch(() => setVersion(''))
  }, [])

  useEffect(() => {
    const pending = timers.current
    return () => {
      pending.forEach(clearTimeout)
      pending.clear()
    }
  }, [])

  const groups = useMemo(() => groupByPlatform(destinations), [destinations])
  const activeCount = destinations.filter((d) => isActive(d.status)).length
  const anyActive = activeCount > 0

  const usedKbps = useMemo(
    () => destinations.reduce((sum, d) => sum + (isActive(d.status) ? d.bitrate : 0), 0),
    [destinations],
  )

  // Simulated telemetry. M2 replaces this with parsed ffmpeg progress.
  useEffect(() => {
    const tick = setInterval(() => {
      setDestinations((prev) =>
        prev.map((d) => {
          if (d.status === 'live' || d.status === 'degraded') {
            const jitter = Math.round((Math.random() - 0.5) * 700)
            return {
              ...d,
              uptime: d.uptime + 1,
              bitrate: Math.max(1200, TARGET_KBPS + jitter),
              dropped: d.dropped + (Math.random() < 0.12 ? Math.floor(Math.random() * 3) : 0),
            }
          }
          return d
        }),
      )
    }, 1000)
    return () => clearInterval(tick)
  }, [])

  useEffect(() => {
    setHistory((h) => [...h.slice(1), usedKbps])
  }, [usedKbps])

  const start = useCallback((id: string) => {
    setDestinations((prev) =>
      prev.map((d) =>
        d.id === id ? { ...d, status: 'connecting', issue: undefined, uptime: 0, dropped: 0 } : d,
      ),
    )
    // Stagger slightly so simultaneous starts do not all flip in the same frame.
    const t = setTimeout(() => {
      setDestinations((prev) =>
        prev.map((d) => (d.id === id ? { ...d, status: 'live', bitrate: TARGET_KBPS } : d)),
      )
      timers.current.delete(id)
    }, 1100 + Math.random() * 900)
    timers.current.set(id, t)
  }, [])

  const stop = useCallback((id: string) => {
    const pending = timers.current.get(id)
    if (pending) {
      clearTimeout(pending)
      timers.current.delete(id)
    }
    setDestinations((prev) =>
      prev.map((d) =>
        d.id === id ? { ...d, status: 'idle', uptime: 0, bitrate: 0, dropped: 0, issue: undefined } : d,
      ),
    )
  }, [])

  const toggle = useCallback(
    (id: string) => {
      const d = destinations.find((x) => x.id === id)
      if (!d) return
      if (isActive(d.status)) stop(id)
      else start(id)
    },
    [destinations, start, stop],
  )

  /** Whole-platform control: if anything in the set is up, stop the set. */
  const toggleMany = useCallback(
    (ids: string[]) => {
      const set = destinations.filter((d) => ids.includes(d.id))
      const running = set.some((d) => isActive(d.status))
      set.forEach((d) => (running ? isActive(d.status) && stop(d.id) : start(d.id)))
    },
    [destinations, start, stop],
  )

  const toggleAll = useCallback(
    () => toggleMany(destinations.map((d) => d.id)),
    [destinations, toggleMany],
  )

  const setPlaylist = useCallback((id: string, clips: VideoClip[], loop: LoopMode) => {
    setDestinations((prev) =>
      prev.map((d) => (d.id === id ? { ...d, source: { kind: 'playlist', clips, loop } } : d)),
    )
  }, [])

  const setLiveSource = useCallback((id: string) => {
    setDestinations((prev) =>
      prev.map((d) => (d.id === id ? { ...d, source: { kind: 'live' } } : d)),
    )
    setEditingSource(null)
  }, [])

  /** Forces a realistic failure so the problem UI can be judged before it is wired up. */
  const previewIssue = useCallback(() => {
    setDestinations((prev) =>
      prev.map((d) =>
        d.id === 'yt-es'
          ? {
              ...d,
              status: 'failed',
              bitrate: 0,
              issue: {
                title: 'Stream key rejected',
                detail: 'YouTube refused the key for this channel. Sign in again to refresh it.',
                action: { label: 'Reconnect account', kind: 'reauth' },
                raw: 'RTMP handshake failed: NetStream.Publish.BadName (code 403)',
              },
            }
          : d,
      ),
    )
  }, [])

  const platformCount = groups.length
  const loopingCount = destinations.filter((d) => d.source.kind === 'playlist').length
  const editing = destinations.find((d) => d.id === editingSource) ?? null

  return (
    <main className="app">
      <header className="topbar">
        <span className="brand">
          <span className="brand-mark" aria-hidden="true">
            <span className="bm-dot" />
            <span className="bm-ring" />
          </span>
          StreamBridge
        </span>
        <button className="icon-btn" title="Settings" aria-label="Settings">
          ⚙
        </button>
      </header>

      <section className="stage">
        <button className={`btn-hero ${anyActive ? 'live' : ''}`} onClick={toggleAll}>
          <span className="hero-glyph" aria-hidden="true">
            {anyActive ? '■' : '▶'}
          </span>
          <span className="hero-text">{anyActive ? 'Stop everything' : 'Go live everywhere'}</span>
        </button>
        <p className="stage-sub">
          {anyActive ? (
            <>
              Live on <strong>{activeCount}</strong> of {destinations.length} accounts
            </>
          ) : (
            <>
              <strong>{destinations.length}</strong> accounts across{' '}
              <strong>{platformCount}</strong> platforms &middot; {loopingCount} looping videos,{' '}
              {destinations.length - loopingCount} on the live feed
            </>
          )}
        </p>
      </section>

      <div className="scroller">
        {groups.map((g) => (
          <PlatformSection
            key={g.platform}
            group={g}
            onToggle={toggle}
            onToggleAll={toggleMany}
            onFix={toggle}
            onEditSource={setEditingSource}
          />
        ))}

        <section className="pgroup">
          <header className="pgroup-head">
            <h2 className="pgroup-more">Connect another</h2>
            <span className="pgroup-count">Same platform again, or a new one</span>
          </header>
          <div className="pgroup-grid">
            <AddDestinationCard onClick={() => {}} />
          </div>
        </section>
      </div>

      <BandwidthBar
        usedKbps={usedKbps}
        capacityKbps={CAPACITY_KBPS}
        history={history}
        activeCount={activeCount}
      />

      <footer className="statusbar">
        <span>Preview build{version && ` · v${version}`} · data is simulated</span>
        <button className="link-btn" onClick={previewIssue}>
          Preview a problem
        </button>
      </footer>

      {editing && (
        <PlaylistModal
          destination={editing}
          onClose={() => setEditingSource(null)}
          onChange={(clips, loop) => setPlaylist(editing.id, clips, loop)}
          onUseLive={() => setLiveSource(editing.id)}
        />
      )}
    </main>
  )
}
