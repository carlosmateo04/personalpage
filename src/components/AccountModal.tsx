import { useState } from 'react'
import { PLATFORM_AUTH, PLATFORM_NAMES, type Destination } from '../types'
import { PlatformIcon } from './PlatformIcon'

/** Settings for an account that is already connected. */
export function AccountModal({
  destination: d,
  onClose,
  onRename,
  onRemove,
  onSetSecret,
  onSetAutoStart,
}: {
  destination: Destination
  onClose: () => void
  onRename: (id: string, label: string) => void
  onRemove: (id: string) => void
  onSetSecret: (id: string, secret: string) => void
  onSetAutoStart: (id: string, autoStart: boolean) => void
}) {
  const [label, setLabel] = useState(d.label)
  const [confirmRemove, setConfirmRemove] = useState(false)
  const [key, setKey] = useState('')
  const [saved, setSaved] = useState(false)
  const hasSecret = d.auth.method === 'key' && d.auth.hasSecret

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <div className="acct-head">
            <span className={`dest-icon plat-${d.platform}`}>
              <PlatformIcon platform={d.platform} />
            </span>
            <div>
              <h2>{d.label}</h2>
              <p>
                {PLATFORM_NAMES[d.platform]} &middot; {d.account}
              </p>
            </div>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>

        <div className="modal-body">
          <label className="field">
            Name
            <input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              onBlur={() => onRename(d.id, label.trim() || d.label)}
            />
            <small>Only affects what you see here, not the channel name on the platform.</small>
          </label>

          <label className="toggle-row">
            <input
              type="checkbox"
              checked={d.autoStart}
              onChange={(e) => onSetAutoStart(d.id, e.target.checked)}
            />
            <span>
              <strong>Start automatically when Caudal opens</strong>
              <small>
                For a stream meant to run around the clock: launch the app and it goes live without
                anyone pressing anything.
              </small>
            </span>
          </label>

          <div className="conn">
            <p className="conn-title">Connection</p>
            {d.auth.method === 'oauth' ? (
              <dl className="conn-grid">
                <div>
                  <dt>Method</dt>
                  <dd>Signed in with {d.auth.provider}</dd>
                </div>
                <div>
                  <dt>Account</dt>
                  <dd>{d.auth.connectedAs}</dd>
                </div>
              </dl>
            ) : (
              <>
                <dl className="conn-grid">
                  <div>
                    <dt>Server</dt>
                    <dd className="mono">{d.auth.server}</dd>
                  </div>
                  <div>
                    <dt>Stream key</dt>
                    <dd>{hasSecret ? 'Saved in the Keychain' : 'Not set'}</dd>
                  </div>
                </dl>

                <label className="field">
                  {hasSecret ? 'Replace the key' : 'Paste the stream key'}
                  <input
                    type="password"
                    value={key}
                    onChange={(e) => {
                      setKey(e.target.value)
                      setSaved(false)
                    }}
                    placeholder="Paste from the platform"
                    spellCheck={false}
                    autoFocus={!hasSecret}
                  />
                  <small>{PLATFORM_AUTH[d.platform].keyHelp}</small>
                </label>
                <button
                  className="btn-primary"
                  disabled={!key.trim()}
                  onClick={() => {
                    onSetSecret(d.id, key.trim())
                    setKey('')
                    setSaved(true)
                  }}
                >
                  {saved ? 'Saved ✓' : 'Save key'}
                </button>
                <p className="oauth-note">
                  Stored in the macOS Keychain, encrypted at rest. It is never written to the
                  configuration file, and never read back into this window — only the streaming
                  engine reads it.
                </p>
              </>
            )}
          </div>
        </div>

        <footer className="modal-foot">
          {confirmRemove ? (
            <>
              <span className="modal-total">Remove {d.label} and forget its key?</span>
              <button className="btn-secondary" onClick={() => setConfirmRemove(false)}>
                Cancel
              </button>
              <button className="btn-danger" onClick={() => onRemove(d.id)}>
                Remove
              </button>
            </>
          ) : (
            <>
              <span className="modal-total">
                Removing an account does not touch the channel on {PLATFORM_NAMES[d.platform]}.
              </span>
              <button className="btn-danger-ghost" onClick={() => setConfirmRemove(true)}>
                Remove account
              </button>
            </>
          )}
        </footer>
      </div>
    </div>
  )
}
