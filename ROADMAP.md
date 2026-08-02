# StreamBridge roadmap

Run several independent 24/7 live streams from one Mac. Each account loops its
own video files, or takes a shared live feed — no relay service, no monthly fee.

**The driving use case:** *Pipo y Lula* looping around the clock on its own
channel, and *Curiora* looping something different on its own channel, both at
once, from one MacBook. Everything below is ordered to reach that first.

Milestones land one at a time. Each has a **gate**: until it passes on real
hardware, the next one does not start. Gate criteria accumulate in
[`REGRESSION.md`](./REGRESSION.md).

---

## Architecture

Two source kinds, and they matter differently.

**Looping video files — the primary path.** Each account gets its own process
reading its own files:

```
  pipo-y-lula.mp4 ──▶ ffmpeg -stream_loop -1 -re -c copy ──▶ YouTube @pipoylula
  curiora.mp4     ──▶ ffmpeg -stream_loop -1 -re -c copy ──▶ YouTube @curiora
```

No relay, no shared encoder, no dependency between them. Two brands, two
processes, two 24/7 streams.

The important property is `-c copy`: when the file is already H.264/AAC at
sensible settings, nothing is re-encoded — ffmpeg only remuxes into RTMP. CPU
cost is close to nothing, so the number of simultaneous loops a MacBook can run
is limited by **upload bandwidth, not by the processor**.

**A shared live feed — the secondary path.** When several destinations show the
same camera or OBS output, that gets encoded once and copied out:

```
  camera / OBS ──▶ local relay ──┬──▶ publisher ──▶ destination A
                   (MediaMTX)    └──▶ publisher ──▶ destination B
```

The relay exists only for this case. Pure file-loop setups never touch it.

**Either way, bandwidth is the ceiling.** Every destination is a full copy
leaving the machine — six at 6 Mbps needs ~36 Mbps sustained upload, which most
home connections cannot hold. That is why the bandwidth dashboard is
load-bearing rather than decorative, and why priority-based adaptive bitrate
exists.

---

## Status

| Milestone | Scope | State |
| --- | --- | --- |
| **M0** | Build pipeline → `.dmg` | ✅ passed on hardware |
| **UI** | Main interface direction | ✅ signed off |
| **M1** | Loop one video to one destination | 🟡 built, awaiting gate |
| **M2** | Status, error taxonomy, auto-reconnect | 🟡 built, awaiting gate |
| M3 | Independent simultaneous loops | ⬜ |
| M4 | Playlists and normalisation | ⬜ |
| **M5** | Bandwidth dashboard | 🟡 built, awaiting gate |
| **M6** | Unattended 24/7 hardening | 🟡 built, awaiting gate |
| **M7** | Accounts, Keychain, add-destination | 🟡 built, awaiting gate |
| M8 | Facebook, TikTok, others | ⬜ |
| M9 | Quality of life | ⬜ |
| — | *Optional:* platform sign-in, live capture, chat | ⬜ |
| M10 | Regression + release | ⬜ |

---

## Foundation

### M0 · Build pipeline — ✅ passed 2026-07-31
Tauri shell, macOS bundle config, `.dmg` target, CI workflow. No streaming
logic — this milestone existed so Xcode tooling, Rust targets, bundle
identifiers, and icon packaging failed *once*, against a hello-world.

### UI direction · Main interface
Pulled forward, out of milestone order: disagreeing about the interface is far
cheaper to fix now than at M11. One dominant action, big targets, minimal
chrome. Accounts group by platform, each with its own status and controls, and
each carries its own source — a looping playlist or the shared live feed.
Data is simulated; later milestones replace it behind the same surface.
**Gate:** sign-off on the look and the interaction model.

## The core capability

### M1 · Loop one video to one destination
Loop a single file to a single pasted RTMP key, indefinitely.

ffmpeg is resolved from the machine rather than bundled — Homebrew's build is
better maintained than anything shipped here, and using the local copy keeps the
app clear of ffmpeg's GPL distribution obligations. The app searches a bundled
location first anyway, so bundling stays possible later without a rewrite.
Includes a pre-flight probe of the file, because `-c copy` only works when the
source is already stream-shaped: H.264/AAC, sane bitrate, and a keyframe
interval around two seconds. Files that fail the probe get flagged with what to
change rather than silently producing a stream that stutters or refuses to start.
**Gate:** one loop live on a real platform for several hours, unattended, with
the loop point causing no visible break.

### M2 · Status, error taxonomy, auto-reconnect
ffmpeg progress parsed into the status state machine, every failure mapped to
plain language and one fix action, and per-destination exponential-backoff
reconnect. Reconnect sits here rather than late in the plan because for a 24/7
stream it is not a refinement — an unattended stream that cannot recover from a
thirty-second network blip is not a 24/7 stream.
**Gate:** each failure induced deliberately — wrong key, network off, bad URL —
produces the right diagnosis; pulling the network mid-stream results in an
unaided recovery.

### M3 · Independent simultaneous loops — *the driving use case*
Two or more accounts, each looping its own files, running at once with no shared
state.
**Gate:** *Pipo y Lula* and *Curiora* both live simultaneously from one Mac;
killing one process leaves the other completely untouched; restarting it rejoins
without disturbing its neighbour.

### M4 · Playlists and normalisation
Several files per account, played in order and looped. Clips that already share
a codec, resolution, and frame rate concatenate with `-c copy` and stay cheap.
Clips that do not are normalised once into a cache, with the cost shown up front
rather than discovered as a stutter mid-stream.
**Gate:** a multi-clip playlist loops seamlessly for hours; a deliberately
mismatched clip is detected, normalised, and plays without a break.

### M5 · Bandwidth dashboard
The strip measures the network interface — everything leaving the machine, not
just what this app is sending — and splits it into our streams and everything
else. Adding up ffmpeg's reported bitrates answers a different question, and
gets headroom wrong exactly when it matters: when a backup or a video call is
competing for the same uplink.

Network-bound and CPU-bound are distinguished using throughput together with
ffmpeg's `speed=` — the same visible symptom with opposite fixes. Less critical
for `-c copy` loops, which barely touch the CPU, but essential once
normalisation or a live feed is in play.

Not built: an active headroom probe. The ceiling comes from what the user
enters, raised by any total the link is observed to carry, and is labelled with
its provenance so a default never reads as a measurement. An upload test would
compete with the very streams it is measuring.

Still out of reach: other devices on the same Wi-Fi. Nothing on this Mac can
see them without the router.

**Gate:** figures track Activity Monitor; a saturated uplink is diagnosed as
saturation and not as something else.

### M6 · Unattended 24/7 hardening
What separates "it ran overnight" from "it has been up for three weeks":
- A power assertion so the Mac does not sleep the streams away
- A watchdog that restarts a publisher that dies rather than merely reporting it
- Handling platform-side session limits — some platforms cut or rotate a
  broadcast after a fixed number of hours, so a genuine 24/7 stream needs to
  roll over cleanly. Limits differ per platform and get verified per platform,
  not assumed.
- Optional launch-at-login and start-streams-on-launch
**Gate:** 72 hours unattended, display asleep, with a deliberate network drop and
a deliberate process kill somewhere in the middle, ending green with the
incidents logged.

## Accounts and platforms

### M7 · Accounts, Keychain, add-destination
`+ Add Destination` → platform picker → paste a key, validated at add time with
a short handshake so a dead key surfaces immediately. Labels, reordering,
persistence, keys in the macOS Keychain.

Stream keys are the whole authentication story: no developer account, no app
review, no API quota, nothing to renew. YouTube and Twitch persistent keys do
not expire, which is exactly what an unattended around-the-clock stream needs.
**Gate:** add/edit/remove/reorder survive a restart; keys verifiably absent from
plaintext on disk; a bad key is rejected on add.

### M8 · Facebook, TikTok, others
Facebook persistent stream keys for Pages. TikTok manual key, with a clear
explanation when an account does not qualify. Twitch, Kick, Rumble, LinkedIn and
X come free from the custom-RTMP path.

One caveat worth planning around: TikTok issues a fresh key per broadcast, so it
suits scheduled runs rather than unattended 24/7. YouTube, Facebook and Twitch
all offer persistent keys.
**Gate:** a Facebook page goes live from the app and stays up for hours; TikTok
goes live or explains itself.

### M9 · Quality of life
Menu-bar mini-controller, macOS notifications on problems, post-stream reports,
scheduled start and stop.

## Optional, only if wanted later

None of these are needed for the driving use case. They are listed so the
decision to skip them stays deliberate rather than accidental.

### Platform sign-in (OAuth)
Would replace pasted keys with a browser sign-in that fetches and refreshes keys
automatically, and — the part keys cannot give — lets the app read the
broadcast's health from the platform itself rather than inferring it from the
upload alone. Without it, "we are pushing bytes successfully" is the only signal
available, so a broadcast that the platform never took live would look healthy
from here. M2's reconnect logic and M8's key validation cover most of that gap.

Costs a Google Cloud project and OAuth client for YouTube, and a Meta app review
for Facebook Pages. Deferred because pasted keys do the job.

### Live capture and OBS input
AVFoundation camera and microphone, screen capture, TCC permission handling, and
an OBS → relay path so existing scenes keep working. Moved down the plan
deliberately: the driving use case is looping files, and camera capture is the
part that drags in permission prompts and hardware variability.
**Gate:** permissions prompt and are handled; camera streams; OBS publishes to
the relay.

### Unified chat inbox
YouTube live chat and Facebook comments merged into one source-tagged pane.
Needs the platform APIs, so it rides along with sign-in if that ever happens.

## Wrap

### M10 · Regression + release
Re-run the entire accumulated checklist, a multi-day soak, optional signing and
notarisation, versioned `.dmg` on a GitHub release.

---

## Explicitly out of scope

**Scenes, overlays, compositing, plugins.** That is rebuilding OBS and it is not
winnable. Better to accept OBS output as an input (M10) so existing scenes keep
working, and own what OBS lacks: unattended loops, accounts, per-account
control, and monitoring.

**A cloud relay.** It would solve the bandwidth ceiling by reintroducing the
monthly cost this project exists to avoid.

**Deep TikTok automation.** Access is gated and shifts; manual keys only.

---

## Known constraints

- **Upload bandwidth** is the ceiling, not CPU — see Architecture above.
- **Source files must be stream-shaped** for `-c copy` to stay free. Wrong
  keyframe interval or an exotic codec forces a re-encode, which is where the
  CPU cost reappears. M1 probes for this.
- **Platform session limits** vary and are verified per platform at M6, not
  assumed.
- **The Mac must stay awake.** Handled by a power assertion at M6.
- **Signing:** locally built apps run without Gatekeeper friction. Sharing the
  `.dmg` needs an Apple Developer account ($99/yr).
- **ffmpeg licensing:** bundling ffmpeg carries GPL obligations. Fine for
  personal use; relevant if this is ever distributed publicly.
- **CSP** is unset while the app loads no remote content. Tighten if platform
  APIs are ever added.
