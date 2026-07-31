# Regression checklist

Every milestone appends its acceptance checks here. The final milestone (M12)
re-runs the whole file top to bottom, so a regression introduced late — say
M9's retry logic breaking M3's failure isolation — actually gets caught.

Check off in a **fresh build** each time, not a dev-server run, unless a step
says otherwise.

---

## M0 · Build pipeline — ✅ PASSED 2026-07-31

Confirmed on an Apple Silicon MacBook Pro: `.dmg` installed, app launched,
status pill read **Bridge OK** with version `0.1.0`, profile `release`,
os `macos`, arch `aarch64`. The build, universal binary, and typecheck steps
were confirmed green on the macOS CI runner. Unticked boxes below were not
exercised individually; re-run the full list at M12.

Environment

- [ ] `./setup.sh` completes with no errors on a clean machine
- [ ] Re-running `./setup.sh` is harmless (idempotent)
- [x] `npm run typecheck` passes
- [x] `npm run build` produces `dist/index.html`

Bundle

- [x] `npm run app:build` completes without errors
- [x] A `.dmg` exists in `src-tauri/target/release/bundle/dmg/`
- [ ] `npm run app:build:universal` completes, and `lipo -archs` on the binary
      inside the bundle reports both `x86_64` and `arm64`

Install and launch

- [x] `.dmg` mounts by double-clicking
- [x] App drags to `/Applications`
- [x] App launches from `/Applications` with no Gatekeeper block
      (locally built binaries carry no quarantine flag)
- [ ] App icon appears correctly in Finder, Dock, and ⌘-Tab
- [ ] Window opens centred at roughly 1100×760
- [x] Window title reads `StreamBridge`
- [ ] Window resizes, and refuses to go below 900×600
- [ ] ⌘Q quits cleanly, no crash dialog, no orphan process
      (`pgrep -fl StreamBridge` returns nothing after quit)

IPC bridge

- [x] Status pill reads **Bridge OK**
- [x] App version shows `0.1.0`
- [x] Build profile shows `release`
- [x] Operating system shows `macos`
- [x] Architecture matches the machine (`aarch64` on Apple Silicon)
- [ ] Values are selectable text (they came from Rust, not hardcoded in JS)

Negative check — proves the bridge indicator is real

- [ ] `npm run dev` then open <http://localhost:1420> in Safari or Chrome:
      the pill reads **No bridge** and the error panel explains why

> **Superseded.** The M0 diagnostic panel was replaced by the main interface in
> the UI direction pass below. IPC is now proven by the version string in the
> status bar rather than by a dedicated pill. At M12, check that instead.

---

## UI direction · Main interface

The shell everything else plugs into. No backend yet — destinations, telemetry,
and problems are simulated, and the status bar says so.

- [ ] Hero button fills the top of the window and reads **Go live** when idle
- [ ] Pressing it turns every destination live; the button turns red and reads
      **Stop everything**
- [ ] Pressing it again returns everything to **Ready**
- [ ] Each destination card starts and stops on its own without disturbing the
      others
- [ ] A card mid-connection shows **Starting…** and cannot be pressed again
- [ ] Live cards show a running timer, upload rate, and dropped-frame count
- [ ] Bandwidth strip totals the active destinations and drops headroom as more
      go live; it turns amber past 65% and red past 85%
- [ ] **Preview a problem** puts TikTok into a failure state with a plain-language
      cause and an **Enter new key** button
- [ ] Version number appears in the status bar (this is now the IPC proof)
- [ ] Window resized narrow: cards reflow to two columns, no horizontal scrolling

Multiple accounts — the core capability

- [ ] Destinations are grouped by platform, with several accounts visible under
      the same platform (two YouTube channels, two Facebook identities)
- [ ] Each account within a platform has its own name, handle, status, and
      controls
- [ ] Header under the hero counts accounts *and* platforms
      ("6 accounts across 4 platforms")
- [ ] Each platform header shows its account count, and how many are live
- [ ] **Start all** on a platform header starts only that platform's accounts,
      leaving the other platforms untouched
- [ ] The hero button starts every account on every platform at once
- [ ] One account failing leaves its same-platform sibling live, and the
      platform header reflects the split ("2 accounts · 1 live")
- [ ] Bandwidth strip totals every live account and labels the count
      ("6 streams uploading")
