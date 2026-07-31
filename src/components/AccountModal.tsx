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
}: {
  destination: Destination
  onClose: () => void
  onRename: (id: string, label: string) => void
  onRemove: (id: string) => void
  onSetSecret: (id: string, secret: string) => void
}) {
  const [label, setLabel] = useState(d.label)
  const [confirmRemove, setConfirmRemove] = useState(false)
  const [key, setKey] = useState('')
  const hasSecret = d.auth.method === 'key' && Boolean(d.auth.secret)

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

          <div className="conn">
            <p className="conn-title">Connection</p>
            {d.auth.method === 'oauth' ? (
              <>
                <dl className="conn-grid">
                  <div>
                    <dt>Method</dt>
                    <dd>Signed in with {d.auth.provider}</dd>
                  </div>
                  <div>
                    <dt>Account</dt>
                    <dd>{d.auth.connectedAs}</dd>
                  </div>
                  <div>
                    <dt>Stream key</dt>
                    <dd>Fetched and refreshed automatically</dd>
                  </div>
                </dl>
                {d.auth.needsReauth ? (
                  <p className="warn-note">
                    <strong>Sign-in expired.</strong> The token could not be refreshed, so this
                    account cannot start until you sign in again.
                  </p>
                ) : null}
                <button className="btn-secondary">Sign in again</button>
              </>
            ) : (
              <>
                <dl className="conn-grid">
                  <div>
                    <dt>Server</dt>
                    <dd className="mono">{d.auth.server}</dd>
                  </div>
                  <div>
                    <dt>Key</dt>
                    <dd className="mono">{hasSecret ? d.auth.keyPreview : 'Not set'}</dd>
                  </div>
                </dl>

                <label className="field">
                  {hasSecret ? 'Replace the key' : 'Paste the stream key'}
                  <input
                    type="password"
                    value={key}
                    onChange={(e) => setKey(e.target.value)}
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
                  }}
                >
                  Save key
                </button>
                <p className="oauth-note">
                  Held in memory only for now — quitting the app forgets it. Keychain storage is
                  M7.
                </p>
              </>
            )}
          </div>
        </div>

        <footer className="modal-foot">
          {confirmRemove ? (
            <>
              <span className="modal-total">Remove {d.label} and its playlist?</span>
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
