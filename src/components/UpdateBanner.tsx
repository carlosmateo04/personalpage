import { useCallback, useEffect, useState } from 'react'
import { check, type Update } from '@tauri-apps/plugin-updater'
import { relaunch } from '@tauri-apps/plugin-process'

type State =
  | { name: 'idle' }
  | { name: 'checking' }
  | { name: 'available'; update: Update }
  | { name: 'downloading'; percent: number }
  | { name: 'ready' }
  | { name: 'failed'; reason: string }

/**
 * In-app updates.
 *
 * Installing restarts the app, which ends every stream, so an update is never
 * applied on its own — a stream that has been up for three weeks should not be
 * interrupted by housekeeping. The check is automatic; the install is not.
 */
export function UpdateBanner({ streamsRunning }: { streamsRunning: number }) {
  const [state, setState] = useState<State>({ name: 'idle' })
  const [dismissed, setDismissed] = useState(false)

  const look = useCallback(async (manual: boolean) => {
    setState({ name: 'checking' })
    try {
      const update = await check()
      setState(update ? { name: 'available', update } : { name: 'idle' })
      if (!update && manual) {
        setState({ name: 'failed', reason: 'You are on the latest version.' })
      }
    } catch (e) {
      // A failed check is not worth interrupting anyone over unless they asked.
      setState(manual ? { name: 'failed', reason: String(e) } : { name: 'idle' })
    }
  }, [])

  useEffect(() => {
    void look(false)
    // Check again every six hours, for a machine that stays open for weeks.
    const t = setInterval(() => void look(false), 6 * 60 * 60 * 1000)
    return () => clearInterval(t)
  }, [look])

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

  if (dismissed && state.name === 'available') return null

  switch (state.name) {
    case 'available': {
      const v = state.update.version
      return (
        <div className="update-bar">
          <span className="update-dot" aria-hidden="true" />
          <span className="update-text">
            <strong>Version {v} is available.</strong>{' '}
            {streamsRunning > 0
              ? `Installing restarts StreamBridge and will end ${streamsRunning} running ${
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
    }

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
          const update = await check()
          setNote(update ? `Version ${update.version} available` : 'Up to date')
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
