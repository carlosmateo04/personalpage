import { useEffect, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'

/** Mirrors the `BuildInfo` struct returned by the Rust `build_info` command. */
type BuildInfo = {
  name: string
  version: string
  os: string
  arch: string
  profile: string
}

type Bridge =
  | { state: 'checking' }
  | { state: 'ok'; info: BuildInfo }
  | { state: 'failed'; reason: string }

export default function App() {
  const [bridge, setBridge] = useState<Bridge>({ state: 'checking' })

  useEffect(() => {
    invoke<BuildInfo>('build_info')
      .then((info) => setBridge({ state: 'ok', info }))
      .catch((err: unknown) =>
        setBridge({ state: 'failed', reason: err instanceof Error ? err.message : String(err) }),
      )
  }, [])

  return (
    <main className="shell">
      <header className="hero">
        <div className="mark" aria-hidden="true">
          <span className="dot" />
          <span className="ring r1" />
          <span className="ring r2" />
        </div>
        <div>
          <h1>StreamBridge</h1>
          <p className="tagline">Multi-platform live streaming from one machine</p>
        </div>
      </header>

      <section className="card">
        <div className="card-head">
          <h2>Milestone&nbsp;0 &middot; Build pipeline</h2>
          <StatusPill bridge={bridge} />
        </div>

        <p className="explain">
          The values below are produced in Rust and passed across the Tauri IPC bridge. If they
          render, the app bundled correctly and the frontend can talk to the backend &mdash; the
          two things M0 exists to prove.
        </p>

        {bridge.state === 'checking' && <p className="muted">Querying backend&hellip;</p>}

        {bridge.state === 'failed' && (
          <div className="error">
            <strong>IPC bridge unavailable.</strong>
            <p>
              Expected when the page is opened in a normal browser via <code>npm run dev</code>.
              Inside the packaged app it should succeed.
            </p>
            <pre>{bridge.reason}</pre>
          </div>
        )}

        {bridge.state === 'ok' && (
          <dl className="grid">
            <Row label="App version" value={bridge.info.version} />
            <Row label="Build profile" value={bridge.info.profile} />
            <Row label="Operating system" value={bridge.info.os} />
            <Row label="Architecture" value={bridge.info.arch} />
            <Row label="Binary name" value={bridge.info.name} />
          </dl>
        )}
      </section>

      <footer className="next">
        Next up &mdash; <strong>M1:</strong> bundle MediaMTX and ffmpeg, push a synthetic test
        pattern into a local relay, and preview it here.
      </footer>
    </main>
  )
}

function StatusPill({ bridge }: { bridge: Bridge }) {
  const map = {
    checking: { cls: 'pending', text: 'Checking' },
    ok: { cls: 'good', text: 'Bridge OK' },
    failed: { cls: 'bad', text: 'No bridge' },
  } as const
  const { cls, text } = map[bridge.state]
  return <span className={`pill ${cls}`}>{text}</span>
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="row">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  )
}
