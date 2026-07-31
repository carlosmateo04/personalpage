import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { DestinationCard, AddDestinationCard } from './components/DestinationCard'
import { BandwidthBar } from './components/BandwidthBar'
import { isActive, type Destination } from './types'

/** Mocked uplink capacity until M4 measures it for real. */
const CAPACITY_KBPS = 25_000
const TARGET_KBPS = 6_000

const INITIAL: Destination[] = [
  {
    id: 'yt',
    platform: 'youtube',
    label: 'Main channel',
    account: '@carlosmateo',
    status: 'idle',
    uptime: 0,
    bitrate: 0,
    dropped: 0,
  },
  {
    id: 'fb',
    platform: 'facebook',
    label: 'Business page',
    account: 'Carlos Mateo',
    status: 'idle',
    uptime: 0,
    bitrate: 0,
    dropped: 0,
  },
  {
    id: 'tt',
    platform: 'tiktok',
    label: 'TikTok LIVE',
    account: '@carlosmateo',
    status: 'idle',
    uptime: 0,
    bitrate: 0,
    dropped: 0,
  },
]

export default function App() {
  const [destinations, setDestinations] = useState<Destination[]>(INITIAL)
  const [history, setHistory] = useState<number[]>(() => Array<number>(48).fill(0))
  const [version, setVersion] = useState('')
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>())

  useEffect(() => {
    invoke<{ version: string }>('build_info')
      .then((i) => setVersion(i.version))
      .catch(() => setVersion(''))
  }, [])

  // Clear any pending connection timers if the component ever unmounts.
  useEffect(() => {
    const pending = timers.current
    return () => {
      pending.forEach(clearTimeout)
      pending.clear()
    }
  }, [])

  const liveCount = destinations.filter((d) => isActive(d.status)).length
  const anyActive = liveCount > 0

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
    const t = setTimeout(() => {
      setDestinations((prev) =>
        prev.map((d) => (d.id === id ? { ...d, status: 'live', bitrate: TARGET_KBPS } : d)),
      )
      timers.current.delete(id)
    }, 1400)
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

  const toggleAll = useCallback(() => {
    if (anyActive) destinations.forEach((d) => isActive(d.status) && stop(d.id))
    else destinations.forEach((d) => start(d.id))
  }, [anyActive, destinations, start, stop])

  /** Forces a realistic failure so the problem UI can be judged before it is wired up. */
  const previewIssue = useCallback(() => {
    setDestinations((prev) =>
      prev.map((d) =>
        d.id === 'tt'
          ? {
              ...d,
              status: 'failed',
              bitrate: 0,
              issue: {
                title: 'Stream key rejected',
                detail: 'TikTok refused the key. Keys are single-use and expire once a broadcast ends.',
                action: { label: 'Enter new key', kind: 'reauth' },
                raw: 'RTMP handshake failed: NetStream.Publish.BadName (code 403)',
              },
            }
          : d,
      ),
    )
  }, [])

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
          <span className="hero-text">{anyActive ? 'Stop everything' : 'Go live'}</span>
        </button>
        <p className="stage-sub">
          {anyActive
            ? `Streaming to ${liveCount} of ${destinations.length} destinations`
            : `${destinations.length} destinations ready`}
        </p>
      </section>

      <section className="dest-grid">
        {destinations.map((d) => (
          <DestinationCard key={d.id} destination={d} onToggle={toggle} onFix={toggle} />
        ))}
        <AddDestinationCard onClick={() => {}} />
      </section>

      <BandwidthBar usedKbps={usedKbps} capacityKbps={CAPACITY_KBPS} history={history} />

      <footer className="statusbar">
        <span>Preview build{version && ` · v${version}`} · data is simulated</span>
        <button className="link-btn" onClick={previewIssue}>
          Preview a problem
        </button>
      </footer>
    </main>
  )
}
