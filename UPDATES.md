# In-app updates

Caudal checks for updates on launch and every six hours. When one is
available a bar appears at the top; pressing **Update now** downloads it,
installs it, and restarts the app. No `.dmg`, no dragging to Applications.

**Updates are never applied on their own.** Installing restarts the app, which
ends every running stream — a stream that has been up for three weeks should
not be interrupted by housekeeping. If anything is live, the button says so:
*"Stop streams and update"*.

---

## One-time setup

Updates are cryptographically signed so an installed app will only accept a
build that came from you. That needs a key pair, and the private half has to
stay private — which means it has to be generated on your machine, not here.

Two commands and three copy-pastes, once.

### 1 · Generate the key pair

Works from any folder; it does not need the repository checked out.

```bash
npx --yes -p @tauri-apps/cli tauri signer generate -w ~/.caudal-updater.key
```

It asks for a password twice. Press Enter twice for none — the workflow handles
either.

It writes **two files** and prints neither key:

| File | What it is |
| --- | --- |
| `~/.caudal-updater.key` | The **private** key. Never commit it, never paste it anywhere but the secret below. Anyone holding it can sign an update your app will install without question. |
| `~/.caudal-updater.key.pub` | The **public** key. Safe to share. |

Both are a single long base64 line.

### 2 · Add three repository secrets

Go to **Settings → Secrets and variables → Actions → New repository secret** on
<https://github.com/carlosmateo04/personalpage> and add:

| Name | Value |
| --- | --- |
| `TAURI_SIGNING_PRIVATE_KEY` | Output of `cat ~/.caudal-updater.key` |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | The password you chose, or leave the value empty |
| `TAURI_UPDATER_PUBKEY` | Output of `cat ~/.caudal-updater.key.pub` |

Copy each whole line, with no trailing spaces. Both files hold one base64 line
and nothing else.

### 3 · Back the private key up

If it is lost, installed apps can no longer be updated — they will refuse every
future build, and everyone has to reinstall from a `.dmg` by hand. Put it in a
password manager.

---

## Publishing an update

```bash
# bump the version in src-tauri/tauri.conf.json and package.json first
git tag v0.2.2
git push origin v0.2.2
```

The tag triggers the release workflow, which runs the tests, builds a universal
binary, signs it, and publishes a GitHub Release containing the `.dmg`, the
update archive, and `latest.json`. Installed apps notice within six hours, or
immediately via **Check for updates** in the status bar.

Only tags publish. Pushing to the branch still produces a test `.dmg` artifact
and changes nothing for anyone running the app.

---

## Notes

- **Versions must increase.** An app on 0.2.0 ignores a release tagged 0.2.0.
- **The first install is still manual.** There is no installed app to update
  from yet.
- **The app remains unsigned by Apple.** Update signing and Apple notarisation
  are unrelated: this proves the update came from you, not that Apple has
  reviewed it. Gatekeeper only inspects the initial download, so updates apply
  without any prompt.
- **If the signing key is missing** the release workflow fails loudly rather
  than publishing something no installed app can verify.
