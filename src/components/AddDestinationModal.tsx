import { useEffect, useState } from 'react'
import { PLATFORM_AUTH, PLATFORM_NAMES, type Platform } from '../types'
import { PlatformIcon } from './PlatformIcon'

const PLATFORMS: Platform[] = ['youtube', 'facebook', 'tiktok', 'twitch', 'custom']

/** Channels the mocked sign-in comes back with. Real ones arrive from the platform API. */
const MOCK_CHANNELS: Record<string, { id: string; name: string; handle: string }[]> = {
  youtube: [
    { id: 'ch1', name: 'Pipo y Lula', handle: '@pipoylula' },
    { id: 'ch2', name: 'Curiora', handle: '@curiora' },
    { id: 'ch3', name: 'Carlos Mateo', handle: '@carlosmateo' },
  ],
  facebook: [
    { id: 'pg1', name: 'Pipo y Lula', handle: 'Page · 24.1k followers' },
    { id: 'pg2', name: 'Curiora', handle: 'Page · 8.3k followers' },
  ],
  twitch: [{ id: 'tw1', name: 'carlosmateo', handle: 'twitch.tv/carlosmateo' }],
}

type Step =
  | { name: 'platform' }
  | { name: 'method'; platform: Platform }
  | { name: 'signing-in'; platform: Platform }
  | { name: 'choose'; platform: Platform }
  | { name: 'paste'; platform: Platform }

export type NewDestination = {
  platform: Platform
  label: string
  account: string
  server: string
  /** Handed straight to the Keychain by the caller, never stored in state. */
  key: string
}

export function AddDestinationModal({
  onClose,
  onAdd,
}: {
  onClose: () => void
  onAdd: (dest: NewDestination) => void
}) {
  const [step, setStep] = useState<Step>({ name: 'platform' })
  const [label, setLabel] = useState('')
  const [server, setServer] = useState('')
  const [key, setKey] = useState('')

  // Stand-in for the browser round trip. The real flow opens the system browser
  // and waits on a loopback redirect.
  useEffect(() => {
    if (step.name !== 'signing-in') return
    const platform = step.platform
    const t = setTimeout(() => setStep({ name: 'choose', platform }), 1800)
    return () => clearTimeout(t)
  }, [step])

  const pickPlatform = (platform: Platform) => {
    setServer(PLATFORM_AUTH[platform].defaultServer)
    setLabel('')
    setKey('')
    setStep({ name: 'paste', platform })
  }

  const back = () => {
    if (step.name === 'platform') return onClose()
    if (step.name === 'method') return setStep({ name: 'platform' })
    if (step.name === 'paste') return setStep({ name: 'platform' })
    if (step.name === 'choose' || step.name === 'signing-in') {
      return setStep({ name: 'paste', platform: step.platform })
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal modal-add" onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <div>
            <h2>{headline(step)}</h2>
            <p>{subhead(step)}</p>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>

        <div className="modal-body">
          {step.name === 'platform' && (
            <div className="plat-picker">
              {PLATFORMS.map((p) => (
                <button key={p} className="plat-tile" onClick={() => pickPlatform(p)}>
                  <span className={`plat-tile-icon plat-${p}`}>
                    <PlatformIcon platform={p} size={30} />
                  </span>
                  <span className="plat-tile-name">{PLATFORM_NAMES[p]}</span>
                  <span className="plat-tile-sub">Paste a stream key</span>
                </button>
              ))}
            </div>
          )}

          {step.name === 'method' && (
            <div className="method-choice">
              <button
                className="btn-oauth"
                onClick={() => setStep({ name: 'signing-in', platform: step.platform })}
              >
                {PLATFORM_AUTH[step.platform].oauth}
              </button>
              <p className="oauth-note">
                Opens your browser to sign in on {PLATFORM_NAMES[step.platform]} directly.
                Caudal never sees your password, and the stream key is fetched and refreshed
                for you.
              </p>

              {PLATFORM_AUTH[step.platform].note && (
                <p className="warn-note">{PLATFORM_AUTH[step.platform].note}</p>
              )}

              <div className="method-or">
                <span>or</span>
              </div>

              <button
                className="btn-secondary"
                onClick={() => setStep({ name: 'paste', platform: step.platform })}
              >
                Paste a stream key instead
              </button>
            </div>
          )}

          {step.name === 'signing-in' && (
            <div className="signing">
              <span className="spinner" aria-hidden="true" />
              <p>Waiting for you to finish in the browser…</p>
              <button className="btn-secondary" onClick={back}>
                Cancel
              </button>
            </div>
          )}

          {step.name === 'choose' && (
            <div className="channel-list">
              {(MOCK_CHANNELS[step.platform] ?? []).map((c) => (
                <button
                  key={c.id}
                  className="channel-row"
                  onClick={() =>
                    onAdd({
                      platform: step.platform,
                      label: c.name,
                      account: c.handle,
                      server: PLATFORM_AUTH[step.platform].defaultServer,
                      key: '',
                    })
                  }
                >
                  <span className={`channel-avatar plat-${step.platform}`}>
                    <PlatformIcon platform={step.platform} size={20} />
                  </span>
                  <span className="channel-id">
                    <strong>{c.name}</strong>
                    <span>{c.handle}</span>
                  </span>
                  <span className="channel-add">Add</span>
                </button>
              ))}
              <p className="oauth-note">
                Each channel becomes its own destination with its own videos and controls. Add as
                many as you like — you can come back and add the rest later.
              </p>
            </div>
          )}

          {step.name === 'paste' && (
            <form
              className="key-form"
              onSubmit={(e) => {
                e.preventDefault()
                onAdd({
                  platform: step.platform,
                  label: label.trim() || PLATFORM_NAMES[step.platform],
                  account: 'Stream key',
                  server: server.trim(),
                  key: key.trim(),
                })
              }}
            >
              <p className="key-help">{PLATFORM_AUTH[step.platform].keyHelp}</p>

              <label>
                Name
                <input
                  value={label}
                  onChange={(e) => setLabel(e.target.value)}
                  placeholder="Pipo y Lula"
                  autoFocus
                />
                <small>What you will call this account inside Caudal.</small>
              </label>

              <label>
                Server URL
                <input
                  value={server}
                  onChange={(e) => setServer(e.target.value)}
                  placeholder="rtmp://…"
                  spellCheck={false}
                />
              </label>

              <label>
                Stream key
                <input
                  value={key}
                  onChange={(e) => setKey(e.target.value)}
                  type="password"
                  placeholder="Paste the key from the platform"
                  spellCheck={false}
                />
                <small>Stored in the macOS Keychain, never in plain text on disk.</small>
              </label>

              {PLATFORM_AUTH[step.platform].note && (
                <p className="warn-note">{PLATFORM_AUTH[step.platform].note}</p>
              )}

              <button className="btn-primary" type="submit" disabled={!server.trim() || !key.trim()}>
                Test key and add
              </button>
              <p className="oauth-note">
                The key is tested against the server before it is saved, so a dead key shows up now
                rather than five seconds into a broadcast. It is stored in the macOS Keychain.
              </p>

              {PLATFORM_AUTH[step.platform].oauth && (
                <>
                  <div className="method-or">
                    <span>optional</span>
                  </div>
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => setStep({ name: 'signing-in', platform: step.platform })}
                  >
                    {PLATFORM_AUTH[step.platform].oauth}
                  </button>
                  <p className="oauth-note">
                    Signing in is not required. It only saves you fetching the key by hand, and lets
                    the app read the broadcast's health from {PLATFORM_NAMES[step.platform]} rather
                    than inferring it from the upload alone.
                  </p>
                </>
              )}
            </form>
          )}
        </div>

        {step.name !== 'platform' && (
          <footer className="modal-foot">
            <button className="btn-secondary" onClick={back}>
              ← Back
            </button>
          </footer>
        )}
      </div>
    </div>
  )
}

function headline(step: Step): string {
  switch (step.name) {
    case 'platform':
      return 'Add an account'
    case 'method':
      return `Connect ${PLATFORM_NAMES[step.platform]}`
    case 'signing-in':
      return 'Signing in…'
    case 'choose':
      return 'Choose what to add'
    case 'paste':
      return `Connect ${PLATFORM_NAMES[step.platform]}`
  }
}

function subhead(step: Step): string {
  switch (step.name) {
    case 'platform':
      return 'Pick a platform. You can connect the same platform as many times as you have accounts.'
    case 'method':
      return 'Signing in is quicker and keeps the key fresh. Pasting a key always works.'
    case 'signing-in':
      return 'Finish in the browser window that just opened, then come back here.'
    case 'choose':
      return 'These are the channels this account can stream to.'
    case 'paste':
      return 'Paste the stream key. No developer account, no app review, nothing to set up.'
  }
}
