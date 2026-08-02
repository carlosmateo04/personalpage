import { useEffect, useState } from 'react'
import { engine, type ConsoleUrls } from '../engine'

/**
 * The one-time registration of Caudal with Google.
 *
 * This is not connecting an account — that is per channel and is a single
 * button. This happens once for the whole app and produces a client ID, which
 * only Google's console can create.
 *
 * The console visit cannot be removed, so the screen removes everything around
 * it: each step opens the exact page it refers to, the field says what it is
 * looking for, and the answer is checked before it is stored rather than at
 * sign-in time in a browser tab that never names the field at fault.
 */
export function GoogleSetupModal({
  onClose,
  onSaved,
}: {
  onClose: () => void
  onSaved: () => void
}) {
  const [urls, setUrls] = useState<ConsoleUrls | null>(null)
  const [id, setId] = useState('')
  const [secret, setSecret] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    engine.googleConsoleUrls().then(setUrls).catch(() => {})
  }, [])

  const open = (url: string | undefined) => {
    if (url) void engine.openExternal(url).catch((e) => setError(String(e)))
  }

  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      await engine.setGoogleClient(id, secret)
      onSaved()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal modal-wide" onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <h2>Connect Caudal to YouTube</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>

        <p className="modal-lede">
          Once, for the app. Google needs to know which application is asking before it will let
          you sign a channel in — that identity is a <strong>client ID</strong>, and only Google's
          console can create one. Afterwards, adding a channel is a single button.
        </p>

        <ol className="setup-steps">
          <li>
            <div className="setup-text">
              <strong>Turn on the YouTube Data API</strong>
              <small>Press Enable. If it asks you to create a project first, any name will do.</small>
            </div>
            <button className="btn-secondary" onClick={() => open(urls?.enableApi)}>
              Open ↗
            </button>
          </li>

          <li>
            <div className="setup-text">
              <strong>Set the app to External, then Publish it</strong>
              <small>
                While it is in <em>Testing</em>, Google expires the sign-in every 7 days — a stream
                schedule would quietly stop a week after you set it up. Publishing shows an
                "unverified app" warning the first time you sign in, which you can pass.
              </small>
            </div>
            <button className="btn-secondary" onClick={() => open(urls?.consent)}>
              Open ↗
            </button>
          </li>

          <li>
            <div className="setup-text">
              <strong>Create an OAuth client ID, of type Desktop app</strong>
              <small>Create credentials → OAuth client ID → Application type: Desktop app.</small>
            </div>
            <button className="btn-secondary" onClick={() => open(urls?.credentials)}>
              Open ↗
            </button>
          </li>
        </ol>

        <label className="field">
          <span>Client ID</span>
          <input
            value={id}
            onChange={(e) => setId(e.target.value)}
            placeholder="000000000000-xxxxxxxx.apps.googleusercontent.com"
            spellCheck={false}
            autoFocus
          />
          <small>The console shows several long strings on that page. This is the one under
            <em> OAuth 2.0 Client IDs</em>, ending in <code>.apps.googleusercontent.com</code>.</small>
        </label>

        <label className="field">
          <span>Client secret</span>
          <input
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            placeholder="GOCSPX-…"
            spellCheck={false}
          />
          <small>
            Shown next to the ID. It goes to the Keychain with your stream keys — though for a
            desktop app it is not really a secret, since it ships inside every copy. What actually
            proves a sign-in is genuine is a one-time code this app generates per attempt.
          </small>
        </label>

        {error && <p className="banner-warn">{error}</p>}

        <footer className="modal-foot">
          <button className="btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button className="btn-primary" onClick={() => void save()} disabled={busy || !id.trim()}>
            {busy ? 'Saving…' : 'Save'}
          </button>
        </footer>
      </div>
    </div>
  )
}
