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
- [ ] Bandwidth strip reports what the interface is sending and drops headroom
      as more go live; it turns amber past 65% and red past 85%
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

---

## M2 · Reconnection and error handling

Automatic recovery — the reason 24/7 is possible at all

- [ ] Pull the Wi-Fi mid-stream: the card turns amber, reads **Reconnecting**,
      and shows a countdown and attempt number
- [ ] Put the Wi-Fi back: it returns to **Live** on its own, with no clicking
- [ ] The card then shows a reconnect count for the session
- [ ] Leave the network off for ten minutes: it is still retrying when the
      connection returns, at roughly 30-second intervals
- [ ] Kill ffmpeg externally (`pkill -f 'stream_loop'`): it comes back by itself
- [ ] Stop during a reconnect wait: it stops immediately rather than sitting out
      the countdown

Problems that will not fix themselves

- [ ] A deliberately wrong stream key reads **Stream key rejected**, stays down,
      and offers **Replace key** — it must *not* retry in a loop
- [ ] Deleting or renaming the video file mid-stream reads **Video file is
      missing** and stays down
- [ ] Retryable problems show no fix button, because recovery is already under
      way; only permanent ones do

## M6 · Unattended operation

- [ ] While anything is streaming, the header shows **Sleep held off**
- [ ] `pmset -g assertions` lists an assertion held by `caffeinate`
- [ ] The Mac does not sleep during an overnight run with the display off
- [ ] The indicator disappears once the last stream stops
- [ ] Quitting the app with ⌘Q while streaming leaves no ffmpeg behind
      (`pgrep -fl ffmpeg` returns nothing)
- [ ] The activity log records each disconnect and recovery with a timestamp
- [ ] After an overnight run, the log explains everything that happened

The gate

- [ ] **72 hours unattended**, with a deliberate network drop and a deliberate
      `pkill` of ffmpeg somewhere in the middle, ending live with both incidents
      visible in the log

## M7 · Accounts that survive a restart

- [ ] Adding an account and quitting, then reopening: the account is still there
- [ ] Its video, name, and server survive too
- [ ] The stream key survives — starting works without re-entering it
- [ ] Keychain Access shows an entry under `com.streambridge.desktop`
- [ ] The configuration file contains **no** stream key:
      `grep -i <part of your key> ~/Library/Application\ Support/com.streambridge.desktop/destinations.json`
      finds nothing
- [ ] Removing an account also removes its Keychain entry
- [ ] A restored account never shows as live; it starts idle
- [ ] **Start automatically when StreamBridge opens** makes the stream start on
      launch, with no clicking
- [ ] The status bar reads `keys in Keychain`

---

## Stopping — no terminal, ever

The bar: pressing **Stop** on a card ends that stream, on its own, and the
platform stops waiting for data. Needing a terminal is a bug.

Per-destination

- [ ] **Stop** on one card ends that stream within a couple of seconds
- [ ] Its ffmpeg is gone: `pgrep -fl ffmpeg` no longer lists it
- [ ] YouTube Studio leaves "Preparing stream" and reports the stream ended
- [ ] Other accounts streaming at the same time are completely unaffected
- [ ] Stopping a destination that is mid-reconnect works too, without waiting
      out the countdown

Everything at once

- [ ] **Stop everything** ends every publisher, not merely those the UI lists
- [ ] `pgrep -fl ffmpeg` is empty afterwards

Quitting

- [ ] ⌘Q while streaming: no ffmpeg survives
- [ ] Force Quit while streaming, then reopen: leftovers from the previous run
      are killed on launch, and the platform stops receiving data
- [ ] Killing StreamBridge with `kill -9` and reopening does the same

The property underneath

- [ ] At no point does stopping require a terminal command

---

## M5 · Bandwidth that reflects the machine, not the app

The point of the change: the strip used to add up what ffmpeg *said* it was
sending. That is blind to everything else on the Mac, so headroom was wrong
precisely when something else was eating the uplink.

What it measures

- [ ] With nothing streaming, the strip still moves — open a website, and
      **Other apps** rises while **Streams** stays at 0
- [ ] Start one account: **Streams** matches the card's own rate, and the total
      is slightly *higher* (RTMP and TCP headers are real bytes)
- [ ] The interface name in the corner matches `route -n get default`
- [ ] Total tracks Activity Monitor → Network → "Data sent/sec" within a few
      percent over a minute
- [ ] Upload a large file to Drive or iCloud while streaming: **Other apps**
      climbs, headroom falls, and the streams' own figure does not move

The ceiling

- [ ] **edit** accepts your plan's upload speed in Mbps and the free figure
      recomputes against it
- [ ] The setting survives quitting and reopening the app
- [ ] Clearing the field returns it to the labelled default
- [ ] Push past the set ceiling (a big upload plus streams): the ceiling rises
      to what the link actually carried rather than pinning at 100%

Behaviour under change

- [ ] Unplug Ethernet / switch Wi-Fi networks mid-stream: the graph shows no
      false spike and no fake dropout, and picks up on the new interface
- [ ] Connect a VPN: measurement follows the interface carrying the default
      route
- [ ] Left running overnight, the graph never shows an impossible spike
      (counters wrapping must not read as hundreds of Gbps)
- [ ] Idle for an hour with no streams: CPU use of StreamBridge stays flat
      (the sampler must not be busy-looping)
