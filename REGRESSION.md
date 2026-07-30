# Regression checklist

Every milestone appends its acceptance checks here. The final milestone (M12)
re-runs the whole file top to bottom, so a regression introduced late — say
M9's retry logic breaking M3's failure isolation — actually gets caught.

Check off in a **fresh build** each time, not a dev-server run, unless a step
says otherwise.

---

## M0 · Build pipeline

Environment

- [ ] `./setup.sh` completes with no errors on a clean machine
- [ ] Re-running `./setup.sh` is harmless (idempotent)
- [ ] `npm run typecheck` passes
- [ ] `npm run build` produces `dist/index.html`

Bundle

- [ ] `npm run app:build` completes without errors
- [ ] A `.dmg` exists in `src-tauri/target/release/bundle/dmg/`
- [ ] `npm run app:build:universal` completes, and `lipo -archs` on the binary
      inside the bundle reports both `x86_64` and `arm64`

Install and launch

- [ ] `.dmg` mounts by double-clicking
- [ ] App drags to `/Applications`
- [ ] App launches from `/Applications` with no Gatekeeper block
      (locally built binaries carry no quarantine flag)
- [ ] App icon appears correctly in Finder, Dock, and ⌘-Tab
- [ ] Window opens centred at roughly 1100×760
- [ ] Window title reads `StreamBridge`
- [ ] Window resizes, and refuses to go below 900×600
- [ ] ⌘Q quits cleanly, no crash dialog, no orphan process
      (`pgrep -fl StreamBridge` returns nothing after quit)

IPC bridge

- [ ] Status pill reads **Bridge OK**
- [ ] App version shows `0.1.0`
- [ ] Build profile shows `release`
- [ ] Operating system shows `macos`
- [ ] Architecture matches the machine (`aarch64` on Apple Silicon)
- [ ] Values are selectable text (they came from Rust, not hardcoded in JS)

Negative check — proves the bridge indicator is real

- [ ] `npm run dev` then open <http://localhost:1420> in Safari or Chrome:
      the pill reads **No bridge** and the error panel explains why
