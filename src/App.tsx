import { useCallback, useEffect, useMemo, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { AddDestinationCard } from './components/DestinationCard'
import { PlatformSection } from './components/PlatformSection'
import { PlaylistModal } from './components/PlaylistModal'
import { AddDestinationModal, type NewDestination } from './components/AddDestinationModal'
import { AccountModal } from './components/AccountModal'
import { BandwidthBar } from './components/BandwidthBar'
import { engine, useStreamEvents, type Ended, type Progress, type ToolStatus } from './engine'
import { applyEnded, applyProgress, maskKey, startBlocker } from './reduce'
import {
  groupByPlatform,
  isActive,
  type Destination,
  type LoopMode,
  type VideoClip,
} from './types'

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
  const [editingSource, setEditingSource] = useState<string | null>(null)
  const [editingAccount, setEditingAccount] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    invoke<{ version: string }>('build_info')
      .then((i) => setVersion(i.version))
      .catch(() => setVersion(''))
    engine.toolStatus().then(setTools).catch(() => setTools(null))
  }, [])

  const patch = useCallback((id: string, change: Partial<Destination>) => {
    setDestinations((prev) => prev.map((d) => (d.id === id ? { ...d, ...change } : d)))
  }, [])

  // Real telemetry from the running ffmpeg processes.
  const onProgress = useCallback((p: Progress) => {
    setDestinations((prev) => applyProgress(prev, p))
  }, [])

  const onEnded = useCallback((e: Ended) => {
    setDestinations((prev) => applyEnded(prev, e))
  }, [])

  useStreamEvents(onProgress, onEnded)

  const groups = useMemo(() => groupByPlatform(destinations), [destinations])
  const activeCount = destinations.filter((d) => isActive(d.status)).length
  const anyActive = activeCount > 0

  const usedKbps = useMemo(
    () => destinations.reduce((sum, d) => sum + (isActive(d.status) ? d.bitrate : 0), 0),
    [destinations],
  )

  useEffect(() => {
    setHistory((h) => [...h.slice(1), usedKbps])
  }, [usedKbps])

  const start = useCallback(
    async (id: string) => {
      const d = destinations.find((x) => x.id === id)
      if (!d) return
      const why = startBlocker(d)
      if (why) {
        setError(why)
        return
      }
      // Narrowed by `blocker` above.
      const clip = (d.source as { clips: VideoClip[] }).clips[0]!
      const auth = d.auth as { server: string; secret?: string }

      patch(id, { status: 'connecting', issue: undefined, uptime: 0, dropped: 0, bitrate: 0 })
      try {
        await engine.startLoop(id, clip.path, auth.server, auth.secret ?? '')
      } catch (e) {
        patch(id, {
          status: 'failed',
          issue: {
            title: 'Could not start',
            detail: String(e),
            action: { label: 'Try again', kind: 'retry' },
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
      patch(id, { status: 'idle', uptime: 0, bitrate: 0, dropped: 0, issue: undefined })
    },
    [patch],
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

  /** Whole-platform control: if anything in the set is up, stop the set. */
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

  const addDestination = useCallback((next: NewDestination) => {
    setDestinations((prev) => [
      ...prev,
      {
        id: `d${prev.length}-${next.platform}-${next.label.replace(/\W+/g, '')}`,
        platform: next.platform,
        label: next.label,
        account: next.account,
        auth: next.auth,
        source: { kind: 'playlist', clips: [], loop: 'all' },
        status: 'idle',
        uptime: 0,
        bitrate: 0,
        dropped: 0,
      },
    ])
    setAdding(false)
  }, [])

  const renameDestination = useCallback((id: string, label: string) => {
    setDestinations((prev) => prev.map((d) => (d.id === id ? { ...d, label } : d)))
  }, [])

  const setSecret = useCallback((id: string, secret: string) => {
    setDestinations((prev) =>
      prev.map((d) =>
        d.id === id && d.auth.method === 'key'
          ? {
              ...d,
              auth: {
                ...d.auth,
                secret,
                keyPreview: maskKey(secret),
              },
            }
          : d,
      ),
    )
  }, [])

  const removeDestination = useCallback(
    (id: string) => {
      void stop(id)
      setDestinations((prev) => prev.filter((d) => d.id !== id))
      setEditingAccount(null)
    },
    [stop],
  )

  const platformCount = groups.length
  const loopingCount = destinations.filter((d) => d.source.kind === 'playlist').length
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
        <button className="icon-btn" title="Settings" aria-label="Settings">
          ⚙
        </button>
      </header>

      {tools?.state === 'missing' && (
        <p className="banner-bad">
          <strong>ffmpeg not found.</strong> Nothing can stream until it is installed. Run{' '}
          <code>./setup.sh</code>, or <code>brew install ffmpeg</code>.
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
            </>
          ) : (
            <>
              <strong>{destinations.length}</strong> accounts across{' '}
              <strong>{platformCount}</strong> platforms &middot; {loopingCount} with video,{' '}
              <strong>{ready}</strong> ready to stream
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
        </span>
        <span>M1 · one looping video per account</span>
      </footer>

      {adding && (
        <AddDestinationModal onClose={() => setAdding(false)} onAdd={addDestination} />
      )}

      {account && (
        <AccountModal
          destination={account}
          onClose={() => setEditingAccount(null)}
          onRename={renameDestination}
          onRemove={removeDestination}
          onSetSecret={setSecret}
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
