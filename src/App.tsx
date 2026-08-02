import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { AddDestinationCard } from './components/DestinationCard'
import { PlatformSection } from './components/PlatformSection'
import { PlaylistModal } from './components/PlaylistModal'
import { AddDestinationModal, type NewDestination } from './components/AddDestinationModal'
import { AccountModal } from './components/AccountModal'
import { BandwidthBar } from './components/BandwidthBar'
import { IncidentLog } from './components/IncidentLog'
import { CheckForUpdates, UpdateBanner } from './components/UpdateBanner'
import {
  engine,
  useStreamEvents,
  type Incident,
  type Progress,
  type StatusEvent,
  type ToolStatus,
} from './engine'
import {
  applyProgress,
  applyStatus,
  fromStored,
  startBlocker,
  tickRetry,
  toStored,
} from './reduce'
import { groupByPlatform, isActive, type Destination, type LoopMode, type VideoClip } from './types'

/**
 * Assumed uplink until M5 measures it. Only used to draw the headroom figure,
 * never to gate anything.
 */
const ASSUMED_UPLINK_KBPS = 25_000

export default function App() {
  const [destinations, setDestinations] = useState<Destination[]>([])
  const [history, setHistory] = useState<number[]>(() => Array<number>(48).fill(0))
  const [version, setVersion] = useState('')
  const [tools, setTools] = useState<ToolStatus | null>(null)
  const [secretStore, setSecretStore] = useState<'keychain' | 'memory' | null>(null)
  const [incidents, setIncidents] = useState<Incident[]>([])
  const [awake, setAwake] = useState(false)
  const [editingSource, setEditingSource] = useState<string | null>(null)
  const [editingAccount, setEditingAccount] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [showLog, setShowLog] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)

  // ------------------------------------------------------------- startup ---

  useEffect(() => {
    let cancelled = false

    async function boot() {
      const [info, toolStatus, store] = await Promise.all([
        invoke<{ version: string }>('build_info').catch(() => ({ version: '' })),
        engine.toolStatus().catch(() => null),
        engine.secretStore().catch(() => null),
      ])
      if (cancelled) return
      setVersion(info.version)
      setTools(toolStatus)
      setSecretStore(store)

      try {
        const json = await engine.loadConfig()
        if (!json) return
        // Which keys survive is the Keychain's business, so ask it rather than
        // trusting the configuration file to remember.
        const ids = (JSON.parse(json).destinations ?? []).map((d: { id: string }) => d.id)
        const withSecrets = await engine.whichHaveSecrets(ids)
        if (!cancelled) setDestinations(fromStored(json, withSecrets))
      } catch (e) {
        if (!cancelled) setError(`Could not load saved accounts: ${e}`)
      } finally {
        if (!cancelled) setLoaded(true)
      }
    }

    void boot()
    return () => {
      cancelled = true
    }
  }, [])

  // Persist whenever anything durable changes, but never before the initial
  // load has finished — writing an empty list over a real one would be worse
  // than any bug this saves.
  const savedRef = useRef('')
  useEffect(() => {
    if (!loaded) return
    const json = JSON.stringify(toStored(destinations))
    if (json === savedRef.current) return
    savedRef.current = json
    engine.saveConfig(json).catch((e) => setError(`Could not save accounts: ${e}`))
  }, [destinations, loaded])

  // ------------------------------------------------------------- telemetry ---

  const onProgress = useCallback((p: Progress) => {
    setDestinations((prev) => applyProgress(prev, p))
  }, [])

  const onStatus = useCallback((s: StatusEvent) => {
    setDestinations((prev) => applyStatus(prev, s))
    engine.keepingAwake().then(setAwake).catch(() => {})
  }, [])

  const onIncident = useCallback((i: Incident) => {
    setIncidents((prev) => [...prev.slice(-499), i])
  }, [])

  useStreamEvents({ onProgress, onStatus, onIncident })

  // Purely cosmetic countdown between backend events.
  useEffect(() => {
    const t = setInterval(() => setDestinations((prev) => tickRetry(prev)), 1000)
    return () => clearInterval(t)
  }, [])

  const groups = useMemo(() => groupByPlatform(destinations), [destinations])
  const activeCount = destinations.filter((d) => isActive(d.status)).length
  const anyActive = activeCount > 0
  const reconnectingCount = destinations.filter((d) => d.status === 'reconnecting').length

  const usedKbps = useMemo(
    () => destinations.reduce((sum, d) => sum + (isActive(d.status) ? d.bitrate : 0), 0),
    [destinations],
  )

  useEffect(() => {
    setHistory((h) => [...h.slice(1), usedKbps])
  }, [usedKbps])

  // --------------------------------------------------------------- control ---

  const patch = useCallback((id: string, change: Partial<Destination>) => {
    setDestinations((prev) => prev.map((d) => (d.id === id ? { ...d, ...change } : d)))
  }, [])

  const start = useCallback(
    async (id: string) => {
      const d = destinations.find((x) => x.id === id)
      if (!d) return
      const why = startBlocker(d)
      if (why) {
        setError(why)
        return
      }
      const clip = (d.source as { clips: VideoClip[] }).clips[0]!
      const auth = d.auth as { server: string }

      patch(id, { status: 'connecting', issue: undefined, uptime: 0, dropped: 0, bitrate: 0 })
      try {
        await engine.startLoop(id, clip.path, auth.server)
      } catch (e) {
        patch(id, {
          status: 'failed',
          issue: {
            title: 'Could not start',
            detail: String(e),
            action: 'retry',
            retryable: false,
          },
        })
      }
    },
    [destinations, patch],
  )

  const stop = useCallback(
    async (id: string) => {
      try {
        await engine.stopLoop(id)
      } catch (e) {
        setError(String(e))
      }
    },
    [],
  )

  const toggle = useCallback(
    (id: string) => {
      const d = destinations.find((x) => x.id === id)
      if (!d) return
      if (isActive(d.status)) void stop(id)
      else void start(id)
    },
    [destinations, start, stop],
  )

  const toggleMany = useCallback(
    (ids: string[]) => {
      const set = destinations.filter((d) => ids.includes(d.id))
      const running = set.some((d) => isActive(d.status))
      set.forEach((d) => {
        if (running) {
          if (isActive(d.status)) void stop(d.id)
        } else if (!isActive(d.status)) {
          void start(d.id)
        }
      })
    },
    [destinations, start, stop],
  )

  const toggleAll = useCallback(() => {
    if (anyActive) {
      // Ask the backend to stop everything it is running, rather than everything
      // this list happens to show. If the two ever disagree, the backend is the
      // one still uploading.
      engine.stopAll().catch((e) => setError(String(e)))
      return
    }
    toggleMany(destinations.map((d) => d.id))
  }, [anyActive, destinations, toggleMany])

  // Auto-start runs once, after the first load, for destinations marked for it.
  const autoStarted = useRef(false)
  useEffect(() => {
    if (!loaded || autoStarted.current) return
    autoStarted.current = true
    destinations
      .filter((d) => d.autoStart && !startBlocker(d))
      .forEach((d) => void start(d.id))
    // Intentionally not reacting to later changes: this is a launch action.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaded])

  // ------------------------------------------------------------ mutations ---

  const setPlaylist = useCallback((id: string, clips: VideoClip[], loop: LoopMode) => {
    setDestinations((prev) =>
      prev.map((d) => (d.id === id ? { ...d, source: { kind: 'playlist', clips, loop } } : d)),
    )
  }, [])

  const setLiveSource = useCallback((id: string) => {
    setDestinations((prev) => prev.map((d) => (d.id === id ? { ...d, source: { kind: 'live' } } : d)))
    setEditingSource(null)
  }, [])

  const addDestination = useCallback(async (next: NewDestination) => {
    const id = `${next.platform}-${next.label.replace(/\W+/g, '')}-${Math.abs(
      [...`${next.label}${next.server}${next.account}`].reduce((h, c) => (h * 31 + c.charCodeAt(0)) | 0, 7),
    ).toString(36)}`

    try {
      await engine.setSecret(id, next.key)
    } catch (e) {
      setError(`Could not save the stream key: ${e}`)
      return
    }

    setDestinations((prev) => [
      ...prev,
      {
        id,
        platform: next.platform,
        label: next.label,
        account: next.account,
        auth: { method: 'key', server: next.server, hasSecret: true },
        source: { kind: 'playlist', clips: [], loop: 'all' },
        status: 'idle',
        uptime: 0,
        bitrate: 0,
        dropped: 0,
        attempt: 0,
        retryIn: 0,
        reconnects: 0,
        autoStart: false,
      },
    ])
    setAdding(false)
  }, [])

  const renameDestination = useCallback((id: string, label: string) => {
    setDestinations((prev) => prev.map((d) => (d.id === id ? { ...d, label } : d)))
  }, [])

  const setAutoStart = useCallback((id: string, autoStart: boolean) => {
    setDestinations((prev) => prev.map((d) => (d.id === id ? { ...d, autoStart } : d)))
  }, [])

  const saveSecret = useCallback(
    async (id: string, key: string) => {
      try {
        await engine.setSecret(id, key)
        setDestinations((prev) =>
          prev.map((d) =>
            d.id === id && d.auth.method === 'key'
              ? { ...d, auth: { ...d.auth, hasSecret: true } }
              : d,
          ),
        )
      } catch (e) {
        setError(`Could not save the stream key: ${e}`)
      }
    },
    [],
  )

  const removeDestination = useCallback(
    async (id: string) => {
      await stop(id)
      await engine.deleteSecret(id).catch(() => {})
      setDestinations((prev) => prev.filter((d) => d.id !== id))
      setEditingAccount(null)
    },
    [stop],
  )

  // ------------------------------------------------------------- rendering ---

  const platformCount = groups.length
  const editing = destinations.find((d) => d.id === editingSource) ?? null
  const account = destinations.find((d) => d.id === editingAccount) ?? null
  const ready = destinations.filter((d) => !startBlocker(d)).length

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
        <span className="topbar-right">
          {awake && (
            <span className="chip-awake" title="The Mac is being kept awake while streaming">
              ☾ Sleep held off
            </span>
          )}
          <button
            className="icon-btn"
            title="Activity log"
            aria-label="Activity log"
            onClick={() => setShowLog(true)}
          >
            ☰
          </button>
        </span>
      </header>

      <UpdateBanner streamsRunning={activeCount} />

      {tools?.state === 'missing' && (
        <p className="banner-bad">
          <strong>ffmpeg not found.</strong> Nothing can stream until it is installed. Run{' '}
          <code>./setup.sh</code>, or <code>brew install ffmpeg</code>.
        </p>
      )}

      {secretStore === 'memory' && (
        <p className="banner-warn">
          <strong>Keys are not being saved.</strong> The Keychain is only available on macOS, so
          keys entered here last until the app quits.
        </p>
      )}

      {error && (
        <p className="banner-warn" onClick={() => setError(null)}>
          {error} <span className="banner-dismiss">Dismiss</span>
        </p>
      )}

      <section className="stage">
        <button
          className={`btn-hero ${anyActive ? 'live' : ''}`}
          onClick={toggleAll}
          disabled={!anyActive && ready === 0}
        >
          <span className="hero-glyph" aria-hidden="true">
            {anyActive ? '■' : '▶'}
          </span>
          <span className="hero-text">{anyActive ? 'Stop everything' : 'Go live everywhere'}</span>
        </button>
        <p className="stage-sub">
          {destinations.length === 0 ? (
            'No accounts yet'
          ) : anyActive ? (
            <>
              Live on <strong>{activeCount}</strong> of {destinations.length} accounts
              {reconnectingCount > 0 && (
                <span className="sub-warn"> · {reconnectingCount} reconnecting</span>
              )}
            </>
          ) : (
            <>
              <strong>{destinations.length}</strong> accounts across{' '}
              <strong>{platformCount}</strong> platforms &middot; <strong>{ready}</strong> ready to
              stream
            </>
          )}
        </p>
      </section>

      <div className="scroller">
        {destinations.length === 0 ? (
          <div className="empty-state">
            <h2>Nothing connected yet</h2>
            <p>
              Add an account, paste its stream key, and choose a video to loop. Each account gets
              its own video and its own controls, so several can run at once.
            </p>
            <button className="btn-primary" onClick={() => setAdding(true)}>
              Add your first account
            </button>
          </div>
        ) : (
          <>
            {groups.map((g) => (
              <PlatformSection
                key={g.platform}
                group={g}
                onToggle={toggle}
                onToggleAll={toggleMany}
                onFix={toggle}
                onEditSource={setEditingSource}
                onEditAccount={setEditingAccount}
              />
            ))}

            <section className="pgroup">
              <header className="pgroup-head">
                <h2 className="pgroup-more">Connect another</h2>
                <span className="pgroup-count">Same platform again, or a new one</span>
              </header>
              <div className="pgroup-grid">
                <AddDestinationCard onClick={() => setAdding(true)} />
              </div>
            </section>
          </>
        )}
      </div>

      <BandwidthBar
        usedKbps={usedKbps}
        capacityKbps={ASSUMED_UPLINK_KBPS}
        history={history}
        activeCount={activeCount}
      />

      <footer className="statusbar">
        <span>
          StreamBridge{version && ` ${version}`}
          {tools?.state === 'ready' && ` · ffmpeg ${tools.version} (${tools.source})`}
          {secretStore === 'keychain' && ' · keys in Keychain'}
        </span>
        <span className="statusbar-actions">
          <CheckForUpdates />
          <button className="link-btn" onClick={() => setShowLog(true)}>
            {incidents.length > 0 ? `${incidents.length} events` : 'Activity log'}
          </button>
        </span>
      </footer>

      {showLog && (
        <IncidentLog
          incidents={incidents}
          destinations={destinations}
          onClose={() => setShowLog(false)}
        />
      )}

      {adding && <AddDestinationModal onClose={() => setAdding(false)} onAdd={addDestination} />}

      {account && (
        <AccountModal
          destination={account}
          onClose={() => setEditingAccount(null)}
          onRename={renameDestination}
          onRemove={removeDestination}
          onSetSecret={saveSecret}
          onSetAutoStart={setAutoStart}
        />
      )}

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
