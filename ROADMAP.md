# StreamBridge roadmap

Stream to several platforms at once from one machine. One encode, fanned out
locally to N destinations — no relay service, no monthly fee.

Milestones land one at a time. Each has a **gate**: until it passes on real
hardware, the next one does not start. Gate criteria accumulate in
[`REGRESSION.md`](./REGRESSION.md).

---

## Architecture

```
  capture / OBS  ──▶  local relay  ──┬──▶  publisher #1  ──▶  YouTube
                     (MediaMTX)      ├──▶  publisher #2  ──▶  Facebook
                                     └──▶  publisher #3  ──▶  TikTok
```

One process per destination, all reading from a shared local relay. This is
deliberate: ffmpeg's `tee` muxer would put every destination in a single
process, so one platform failing would take down the rest. Separate publishers
make per-destination start/stop/pause and failure isolation fall out naturally.

**The cost of local fan-out is upload bandwidth.** N destinations means N full
copies leaving the machine — three at 6 Mbps needs ~18 Mbps sustained upload.
That is why the bandwidth dashboard is load-bearing rather than decorative, and
why priority-based adaptive bitrate (M9) exists.

---

## Status

| Milestone | Scope | State |
| --- | --- | --- |
| **M0** | Build pipeline → `.dmg` | ✅ passed on hardware |
| **UI** | Main interface direction (simulated data) | 🟡 awaiting sign-off |
| M1 | Local relay + synthetic test source | ⬜ |
| M2 | One destination, real status + error taxonomy | ⬜ |
| M3 | Fan-out with independent control | ⬜ |
| M4 | Bandwidth dashboard | ⬜ |
| M5 | Real inputs (camera / mic / screen, OBS) | ⬜ |
| M6 | Add-destination UX + Keychain | ⬜ |
| M7 | YouTube OAuth + platform truth | ⬜ |
| M8 | Facebook, TikTok, others | ⬜ |
| M9 | Reconnect + priority + pre-flight | ⬜ |
| M10 | Quality of life | ⬜ |
| M11 | Unified chat inbox | ⬜ |
| M12 | Regression + release | ⬜ |

---

## Foundation

### M0 · Build pipeline
Tauri shell, macOS bundle config, `.dmg` target, CI workflow. No streaming
logic at all — this milestone exists so that Xcode tooling, Rust targets,
bundle identifiers, and icon packaging fail *once*, against a hello-world,
rather than confusing every later failure.
**Gate:** `.dmg` installs and launches; the info panel renders values fetched
from Rust over IPC. — *Passed 2026-07-31 on Apple Silicon.*

### UI direction · Main interface
Pulled forward, out of milestone order, on the principle that disagreeing about
the interface is far cheaper to fix now than at M10. One dominant action, big
targets, minimal chrome: a hero **Go live** button that starts everything, one
large card per destination with its own start/stop and status, and a persistent
bandwidth strip. Destinations, telemetry, and problems are simulated; later
milestones replace the mock data with real processes behind the same surface.
**Gate:** sign-off on the look and the interaction model.

### M1 · Local relay + synthetic source
Bundle MediaMTX and ffmpeg, push a generated test pattern into the relay,
preview it in-app. A test pattern rather than the webcam on purpose: it is
deterministic and needs no macOS permissions, so streaming logic gets debugged
without TCC prompts in the way.
**Gate:** preview visible, 10 minutes clean, bundled binaries survive `.dmg`
packaging and still execute.

### M2 · One destination, real status
One pasted RTMP key. Start/stop. ffmpeg progress parsed into the status state
machine: `idle → connecting → live | degraded | reconnecting | failed`.
**Gate:** real key goes live on the platform and stops cleanly; then each
failure is induced deliberately — wrong key, network off, bad URL — and each
produces the correct plain-language diagnosis plus a working fix action.

## Core architecture

### M3 · Fan-out with independent control — *critical gate*
N publishers from one relay, each with its own start/stop/pause.
**Gate:** three destinations live simultaneously; killing one leaves the other
two untouched; restarting it rejoins. If this fails the architecture is wrong,
which is why it comes early.

### M4 · Bandwidth dashboard
Per-output throughput, aggregate, NIC total, headroom probe, rolling graph.
Critically, network-bound and CPU-bound are distinguished using throughput
together with ffmpeg's `speed=` — the same visible symptom (dropped frames)
with opposite fixes.
**Gate:** figures track Activity Monitor; a deliberately saturated uplink is
diagnosed as saturation; a deliberately overloaded CPU is *not* misreported as
a network problem.

### M5 · Real inputs
AVFoundation camera/mic enumeration, screen capture, TCC permission handling,
and an OBS → relay input path so existing scenes and overlays keep working.
**Gate:** permissions prompt and are handled; camera streams; OBS publishes to
the relay.

## Accounts and platforms

### M6 · Add-destination UX + Keychain
`+ Add Destination` → platform picker → sign-in *or* paste-key. Keys validated
at add time with a short handshake against the endpoint, so a dead key surfaces
immediately instead of five seconds into a broadcast. Labels, avatars,
reordering, persistence.
**Gate:** add/edit/remove/reorder survive a restart; keys live in Keychain and
are verifiably absent from plaintext on disk; a bad key is rejected on add.

### M7 · YouTube OAuth + platform truth
System-browser OAuth with a loopback redirect (embedded webviews are blocked by
Google), token refresh, broadcast create/transition, `healthStatus` polling.
This completes the second half of the status design: *we* think we are pushing,
and *the platform* agrees it is live. Divergence is itself an alert — it is the
failure mode behind streaming to nobody for twenty minutes.
**Gate:** sign-in populates channel and key; going live transitions the
broadcast; a forced divergence raises the warning; multiple accounts coexist.

### M8 · Facebook, TikTok, others
Facebook via persistent stream key first (no app review needed), Graph API
optional later. TikTok manual key, with a clear explanation when an account
does not qualify — RTMP access is gated and their live API needs approval, so
treat breakage as an expected state. Twitch, Kick, Rumble, LinkedIn and X come
free from the custom-RTMP path.
**Gate:** Facebook page goes live from the app; TikTok goes live or explains
itself.

## Resilience

### M9 · Reconnect + priority + pre-flight
Per-destination exponential backoff, destination priority ordering, automatic
bitrate reduction of the lowest-priority destination under saturation, and a
pre-flight check that validates every key and measures headroom before going
live.
**Gate:** mid-stream network loss → each destination reconnects independently;
saturation → lowest priority degrades first while the others hold.

## Polish

### M10 · Quality of life
Always-on local recording, set-once metadata pushed to every platform,
menu-bar mini-controller, macOS notifications on issues, post-stream report.
Independently shippable; order negotiable.

### M11 · Unified chat inbox
YouTube live chat and Facebook comments merged into one source-tagged pane.
Last because it is the largest single feature and touches nothing critical.

## Wrap

### M12 · Regression + release
Re-run the entire accumulated checklist, multi-hour soak with three
destinations, optional signing and notarisation, versioned `.dmg` on a
GitHub release.

---

## Explicitly out of scope

**Scenes, overlays, compositing, plugins.** That is rebuilding OBS and it is
not winnable. The better move is to accept OBS output as an input (M5), so
existing scenes keep working and this app owns what OBS lacks: fan-out,
accounts, per-destination control, and monitoring.

**A cloud relay.** It would solve the bandwidth ceiling by reintroducing the
monthly cost this project exists to avoid.

**Deep TikTok automation.** Access is gated and shifts; manual keys only.

---

## Known constraints

- **Upload bandwidth** is the real ceiling — see Architecture above.
- **Signing:** locally built apps run without Gatekeeper friction. Sharing the
  `.dmg` with anyone else needs an Apple Developer account ($99/yr) for
  signing and notarisation.
- **ffmpeg licensing:** bundling ffmpeg carries GPL obligations. Fine for
  personal use; relevant if this is ever distributed publicly.
- **CSP** is unset while the app loads no remote content. Tighten before M7.
