# In-app updates

Caudal checks for updates on launch and every six hours, and a bar appears at
the top when one exists. What the bar can *do* depends on whether the build was
signed.

| | Signed build | Unsigned build |
| --- | --- | --- |
| Tells you a version exists | yes | yes |
| Downloads it | yes | opens it in your browser |
| Installs and restarts | yes | you drag it to Applications |
| Setup needed | the key pair below | none |

**There is no third option, and it is not a matter of accepting some risk.**
The updater plugin ends its download with

```rust
verify_signature(&buffer, &self.signature, &self.config.pubkey)?;
```

No condition, no flag. With no public key the decode fails and the whole
download is thrown away, so an unsigned build offering an install button would
fetch an entire release and then refuse it. The fallback in `release.rs` does
the half that still works rather than pretending.

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

It asks for a password twice. **Choose one and keep it** — the private key file
is otherwise usable by anything that can read it, and this file is a good
candidate for a backup folder that syncs to a cloud drive.

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
| `TAURI_SIGNING_PRIVATE_KEY` | `cat ~/.caudal-updater.key \| pbcopy` |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | The password you chose |
| `TAURI_UPDATER_PUBKEY` | `cat ~/.caudal-updater.key.pub \| pbcopy` |

Pipe through `pbcopy` rather than selecting the text. Both files are one long
base64 line, and a selection that misses a character produces a key that fails
only at signing time, in CI, with an error that does not mention the cause.

**If you chose no password, do not create the third secret at all.** GitHub
will not store an empty value, and an absent secret already reaches the
workflow as the empty string — which is exactly what a passwordless key
expects.

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
