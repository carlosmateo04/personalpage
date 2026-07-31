import { useState } from 'react'
import { PLATFORM_NAMES, type Destination } from '../types'
import { PlatformIcon } from './PlatformIcon'

/** Settings for an account that is already connected. */
export function AccountModal({
  destination: d,
  onClose,
  onRename,
  onRemove,
}: {
  destination: Destination
  onClose: () => void
  onRename: (id: string, label: string) => void
  onRemove: (id: string) => void
}) {
  const [label, setLabel] = useState(d.label)
  const [confirmRemove, setConfirmRemove] = useState(false)

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
                    <dt>Method</dt>
                    <dd>Stream key</dd>
                  </div>
                  <div>
                    <dt>Server</dt>
                    <dd className="mono">{d.auth.server}</dd>
                  </div>
                  <div>
                    <dt>Key</dt>
                    <dd className="mono">{d.auth.keyPreview}</dd>
                  </div>
                </dl>
                <button className="btn-secondary">Replace key</button>
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
