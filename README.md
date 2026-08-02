# StreamBridge

Stream to YouTube, Facebook, TikTok and others simultaneously from one Mac.
One encode, fanned out locally — no relay service, no monthly fee. Per-platform
start/stop/pause, live health status with actual diagnoses, and real-time
bandwidth monitoring.

macOS app, distributed as a `.dmg`.

> **Current state: M1 — one looping video per account.** The app streams for
> real: pick a video file, paste a stream key, and it loops to the platform
> indefinitely. Playlists of several files, auto-reconnect, and unattended
> hardening are still ahead. See [`ROADMAP.md`](./ROADMAP.md).

---

## Build it

**Requirements:** macOS 11+, Xcode command-line tools, Node 20+, Rust, ffmpeg.
`setup.sh` checks all four and installs what it can.

```bash
git clone https://github.com/carlosmateo04/personalpage.git
cd personalpage
git checkout claude/multiplatform-streaming-app-dopvk5

./setup.sh
npm run app:build
```

The `.dmg` lands in:

```
src-tauri/target/release/bundle/dmg/StreamBridge_0.1.0_<arch>.dmg
```

Double-click it, drag **StreamBridge** to Applications, launch it.

Because you built it on your own machine, macOS attaches no quarantine flag —
no Gatekeeper prompt, no right-click-to-open dance. That only applies to
downloaded builds (see [CI builds](#ci-builds) below).

### If setup.sh can't do it all

```bash
xcode-select --install                                    # Xcode CLT
brew install node                                         # Node 20+
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh   # Rust
npm install
```

## Commands

| Command | What it does |
| --- | --- |
| `npm run app:dev` | Development window with live reload |
| `npm run app:build` | `.dmg` for this Mac's architecture |
| `npm run app:build:universal` | `.dmg` for Intel + Apple Silicon |
| `npm run typecheck` | TypeScript check, no build |
| `npm run build` | Frontend only — works on any OS |
| `npm test` | Frontend state-machine tests |
| `npm run test:rust` | Rust unit tests |
| `npm run icons` | Regenerate the icon set from `assets/app-icon.png` |

`npm run dev` alone serves the frontend in a browser at
<http://localhost:1420>. Useful for UI work, but the Rust backend is absent, so
the app correctly reports **No bridge** there.

## Updating

From 0.2.0 the app updates itself: a bar appears when a new version is
available, and pressing **Update now** installs it and restarts. No `.dmg`
after the first install. Updates never apply on their own — installing ends
running streams, so it always asks. See [`UPDATES.md`](./UPDATES.md).

## CI builds

`.github/workflows/build-macos.yml` builds a universal `.dmg` on a macOS
runner for every push to this branch, and uploads it as a workflow artifact.
Useful if local tooling misbehaves.

Downloaded builds *are* quarantined and unsigned, so first launch needs one of:

```bash
xattr -d com.apple.quarantine /Applications/StreamBridge.app
```

…or right-click the app → **Open** → **Open** once.

## Project layout

```
src/                     React frontend
  App.tsx                M0 status panel
src-tauri/               Rust backend
  src/lib.rs             Tauri commands
  tauri.conf.json        Bundle + window config
  icons/                 Generated app icons
assets/app-icon.png      Icon source (1024px)
setup.sh                 Developer setup
ROADMAP.md               Milestone plan and architecture
REGRESSION.md            Accumulating acceptance checklist
```

## Verifying M0

Work through the M0 section of [`REGRESSION.md`](./REGRESSION.md). The short
version: the `.dmg` installs, the window opens, and the status pill reads
**Bridge OK** with a version, profile, OS, and architecture — all of which are
produced in Rust and crossed the IPC boundary to get on screen.

## Notes

- **Bundle identifier** is `com.streambridge.desktop`. Change it in
  `src-tauri/tauri.conf.json` before signing under your own Apple account.
- **Signing** is only needed to share the `.dmg` with other people. Building
  for yourself needs no Apple Developer account.
- **Never commit stream keys.** From M6 they live in the macOS Keychain.
  `.env*` is gitignored as a backstop.
