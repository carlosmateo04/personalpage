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

Per-account looping video — the driving use case

- [ ] Each account card shows its own source: a playlist chip, or "Live feed"
- [ ] Two accounts can hold completely different playlists at the same time
      (Pipo y Lula and Curiora)
- [ ] Clicking the source chip opens that account's playlist, titled with the
      account name
- [ ] Videos can be added, removed, and reordered; the total loop duration
      updates
- [ ] The same file can appear twice in one playlist without the entries
      interfering
- [ ] Repeat mode offers whole playlist, first video only, and shuffle
- [ ] While live, the chip shows the file currently playing and its position
      ("2/3"), with a progress bar underneath
- [ ] A playlist mixing resolutions raises the normalisation warning
- [ ] **Use live feed instead** switches that account off playlists without
      touching any other account
- [ ] Header under the hero counts how many accounts loop video versus take the
      live feed

---

## Account connection

Add flow

- [ ] **+ Add destination** opens a platform picker with YouTube, Facebook,
      TikTok, Twitch, and Custom RTMP
- [ ] Picking a platform goes straight to the key form — no sign-in step in the
      way
- [ ] The server URL is prefilled for known platforms
- [ ] Help text says where to find the key on that platform
- [ ] The key form refuses to submit until both server and key are filled
- [ ] Adding creates a destination with its own controls and playlist
- [ ] The same platform can be connected repeatedly for different accounts
- [ ] TikTok warns that its keys are per-broadcast, which limits unattended 24/7
- [ ] TikTok offers no sign-in at all
- [ ] Custom RTMP accepts an arbitrary server URL and key
- [ ] Sign-in appears only as an optional extra below the key form, on platforms
      that support it
- [ ] **Back** returns to the previous step; closing mid-flow adds nothing

Account settings

- [ ] Clicking a card's name opens that account's settings
- [ ] OAuth accounts show the provider, the signed-in identity, and that the key
      refreshes automatically
- [ ] Key accounts show the server and a masked key preview, never the full key
- [ ] The full key appears nowhere in the rendered page after it is saved
- [ ] Renaming changes only the local label
- [ ] Removing asks for confirmation first, and says it does not touch the
      channel on the platform
- [ ] A card whose sign-in expired shows a warning badge instead of the check

---

## M1 · Loop one video to one destination

The first milestone where something real leaves the Mac.

Setup

- [ ] `./setup.sh` finds or installs ffmpeg and reports its version
- [ ] Status bar shows `ffmpeg <version> (homebrew)` at the bottom right
- [ ] With ffmpeg removed from PATH, a red banner says so and streaming is
      refused rather than failing obscurely

Choosing a file

- [ ] **Choose a video file** opens the native macOS picker
- [ ] Picking a file shows its real codec, resolution, fps, bitrate, and
      measured keyframe spacing
- [ ] An H.264 + AAC file reports "Ready to stream as-is"
- [ ] A file with no audio track is flagged as a blocker
- [ ] A non-H.264 file (ProRes, HEVC) warns that it needs re-encoding
- [ ] A file with sparse keyframes warns about join latency
- [ ] Picking a non-video file reports a readable error, not a crash

Streaming

- [ ] Starting without a video explains what is missing
- [ ] Starting without a key explains what is missing
- [ ] With both present, **Start** turns the card to Connecting, then Live once
      frames flow
- [ ] Uptime, upload rate, and dropped frames update about once a second and
      match what YouTube Studio reports
- [ ] The stream is visible on the platform
- [ ] **Stop** ends it cleanly, the platform sees the stream end, and no ffmpeg
      process is left behind (`pgrep -fl ffmpeg` returns nothing)
- [ ] Quitting the app while streaming does not leave an orphan ffmpeg

The gate

- [ ] One loop runs **for several hours unattended** on a real platform
- [ ] The video loops back to the start with no visible break for a viewer
- [ ] Dropped frames stay at or near zero for the whole run
- [ ] Speed holds at ~1.0x throughout
- [ ] CPU use stays low, confirming `-c copy` is really in effect and nothing is
      being re-encoded

Failure handling

- [ ] A deliberately wrong stream key fails with ffmpeg's reason shown
- [ ] Killing the ffmpeg process externally surfaces "Stream stopped
      unexpectedly" with a **Start again** button
- [ ] Pulling the network mid-stream surfaces a failure
      (automatic recovery is M2, so manual restart is expected here)
