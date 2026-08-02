import { useCallback, useEffect, useState } from 'react'
import { check, type Update } from '@tauri-apps/plugin-updater'
import { relaunch } from '@tauri-apps/plugin-process'
import { engine, type Release } from '../engine'

type State =
  | { name: 'idle' }
  | { name: 'checking' }
  | { name: 'available'; update: Update }
  | { name: 'manual'; release: Release }
  | { name: 'downloading'; percent: number }
  | { name: 'ready' }
  | { name: 'failed'; reason: string }

/** Six hours, for a machine that stays open for weeks. */
const EVERY = 6 * 60 * 60 * 1000

/**
 * In-app updates, by whichever of the two routes this build can take.
 *
 * **Signed** — the updater plugin downloads, verifies, installs, and restarts.
 * One button.
 *
 * **Unsigned** — the plugin cannot be used at all. It verifies every download
 * against a public key and the check has no opt-out, so a build without a key
 * would fetch an entire release and then refuse it. The fallback does the half
 * that still works: ask GitHub what the newest release is, say so, and open the
 * `.dmg`. Installing stays manual.
 *
 * Either way the update is never applied on its own. Installing restarts the
 * app, which ends every stream, and a stream that has been up for three weeks
 * should not be interrupted by housekeeping.
 */
export function UpdateBanner({ streamsRunning }: { streamsRunning: number }) {
  const [state, setState] = useState<State>({ name: 'idle' })
  const [signed, setSigned] = useState<boolean | null>(null)
  const [dismissed, setDismissed] = useState(false)

  useEffect(() => {
    engine.updaterSigned().then(setSigned).catch(() => setSigned(false))
  }, [])

  const look = useCallback(
    async (manual: boolean) => {
      if (signed === null) return
      setState({ name: 'checking' })
      try {
        if (signed) {
          const update = await check()
          if (update) return setState({ name: 'available', update })
        } else {
          const release = await engine.latestRelease()
          if (release) return setState({ name: 'manual', release })
        }
        setState(manual ? { name: 'failed', reason: 'You are on the latest version.' } : { name: 'idle' })
      } catch (e) {
        // A failed check is not worth interrupting anyone over unless they asked.
        setState(manual ? { name: 'failed', reason: String(e) } : { name: 'idle' })
      }
    },
    [signed],
  )

  useEffect(() => {
    if (signed === null) return
    void look(false)
    const t = setInterval(() => void look(false), EVERY)
    return () => clearInterval(t)
  }, [look, signed])

  const install = useCallback(async () => {
    if (state.name !== 'available') return
    const { update } = state
    try {
      let downloaded = 0
      let total = 0
      setState({ name: 'downloading', percent: 0 })

      await update.downloadAndInstall((event) => {
        if (event.event === 'Started') {
          total = event.data.contentLength ?? 0
        } else if (event.event === 'Progress') {
          downloaded += event.data.chunkLength
          setState({
            name: 'downloading',
            percent: total > 0 ? Math.round((downloaded / total) * 100) : 0,
          })
        } else if (event.event === 'Finished') {
          setState({ name: 'ready' })
        }
      })

      setState({ name: 'ready' })
      await relaunch()
    } catch (e) {
      setState({ name: 'failed', reason: String(e) })
    }
  }, [state])

  if (dismissed && (state.name === 'available' || state.name === 'manual')) return null

  switch (state.name) {
    case 'available':
      return (
        <div className="update-bar">
          <span className="update-dot" aria-hidden="true" />
          <span className="update-text">
            <strong>Version {state.update.version} is available.</strong>{' '}
            {streamsRunning > 0
              ? `Installing restarts Caudal and will end ${streamsRunning} running ${
                  streamsRunning === 1 ? 'stream' : 'streams'
                }.`
              : 'Takes a few seconds; the app restarts itself.'}
          </span>
          <button className="update-later" onClick={() => setDismissed(true)}>
            Later
          </button>
          <button className="update-now" onClick={() => void install()}>
            {streamsRunning > 0 ? 'Stop streams and update' : 'Update now'}
          </button>
        </div>
      )

    case 'manual':
      return (
        <div className="update-bar">
          <span className="update-dot" aria-hidden="true" />
          <span className="update-text">
            <strong>Version {state.release.version} is available.</strong> This build cannot
            install it itself — the download opens in your browser, then drag it to Applications.
          </span>
          <button className="update-later" onClick={() => setDismissed(true)}>
            Later
          </button>
          {/* Says download, because that is all it does. Labelling it "Update"
              would promise the one-click install this build cannot perform. */}
          <button
            className="update-now"
            onClick={() =>
              void engine
                .openExternal(state.release.dmg_url ?? state.release.page_url)
                .catch((e) => setState({ name: 'failed', reason: String(e) }))
            }
          >
            Download {state.release.version}
          </button>
        </div>
      )

    case 'downloading':
      return (
        <div className="update-bar">
          <span className="update-dot busy" aria-hidden="true" />
          <span className="update-text">Downloading update… {state.percent}%</span>
          <span className="update-progress" aria-hidden="true">
            <span style={{ width: `${state.percent}%` }} />
          </span>
        </div>
      )

    case 'ready':
      return (
        <div className="update-bar">
          <span className="update-dot" aria-hidden="true" />
          <span className="update-text">Update installed. Restarting…</span>
        </div>
      )

    case 'failed':
      return (
        <div className="update-bar quiet" onClick={() => setState({ name: 'idle' })}>
          <span className="update-text">{state.reason}</span>
          <span className="update-later">Dismiss</span>
        </div>
      )

    default:
      return null
  }
}

/** Manual check, for the status bar. */
export function CheckForUpdates() {
  const [note, setNote] = useState<string | null>(null)

  return (
    <button
      className="link-btn"
      onClick={async () => {
        setNote('Checking…')
        try {
          const signed = await engine.updaterSigned()
          const version = signed
            ? (await check())?.version
            : (await engine.latestRelease())?.version
          setNote(version ? `Version ${version} available` : 'Up to date')
        } catch (e) {
          setNote(`Check failed: ${e}`)
        }
        setTimeout(() => setNote(null), 6000)
      }}
    >
      {note ?? 'Check for updates'}
    </button>
  )
}
