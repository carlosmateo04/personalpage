/** Platforms with a dedicated integration. `custom` covers any other RTMP endpoint. */
export type Platform = 'youtube' | 'facebook' | 'tiktok' | 'twitch' | 'custom'

/**
 * Lifecycle of a single destination.
 *
 * `degraded` means connected but not keeping up; `reconnecting` means the
 * connection dropped and a retry is in flight. They are separate states
 * because they call for different messaging and different fixes.
 */
export type DestinationStatus =
  | 'idle'
  | 'connecting'
  | 'live'
  | 'degraded'
  | 'reconnecting'
  | 'failed'

/**
 * A problem, in the terms a user can act on. Never a raw ffmpeg string —
 * `title` says what happened, `detail` says why, `action` is the one button
 * that fixes it. `raw` stays available behind a disclosure for debugging.
 */
export type Issue = {
  title: string
  detail: string
  action?: { label: string; kind: 'retry' | 'reauth' | 'bitrate' | 'settings' }
  raw?: string
}

/** A video file queued on an account's playlist. */
export type VideoClip = {
  id: string
  name: string
  /** Duration in seconds. */
  duration: number
  width: number
  height: number
  /** Video codec as probed from the container. */
  codec: string
}

export type LoopMode = 'all' | 'one' | 'shuffle'

/**
 * Where a destination's video comes from.
 *
 * `live` shares the single capture/OBS feed with every other live destination —
 * encoded once, copied N times. `playlist` gives this account its own looping
 * video files, independent of every other destination.
 */
export type Source =
  | { kind: 'live' }
  | { kind: 'playlist'; clips: VideoClip[]; loop: LoopMode }

/**
 * How this destination proves it may publish.
 *
 * `oauth` means the platform's own sign-in granted a token, and the stream key
 * is fetched (and refreshed) automatically. `key` means a key was pasted by
 * hand — always available, and the only route on platforms whose API is gated.
 */
export type Auth =
  | { method: 'oauth'; provider: string; connectedAs: string; needsReauth?: boolean }
  | { method: 'key'; server: string; keyPreview: string }

/**
 * What a platform supports for connecting an account.
 *
 * Pasting a key is the primary path everywhere: it needs no developer account,
 * no app review, and no API quota. Signing in is offered only where the
 * platform has a usable API, and only as an optional extra.
 */
export type PlatformAuth = {
  /** Label for the optional sign-in button, absent where there is no usable API. */
  oauth?: string
  /** Where to find the key on the platform. */
  keyHelp: string
  /** Anything the user should know before relying on this platform. */
  note?: string
  defaultServer: string
}

export const PLATFORM_AUTH: Record<Platform, PlatformAuth> = {
  youtube: {
    oauth: 'Sign in with Google',
    keyHelp: 'YouTube Studio → Go Live → Stream → “Stream key”. Use the persistent key: it does not expire, which is what makes an around-the-clock stream possible.',
    defaultServer: 'rtmp://a.rtmp.youtube.com/live2',
  },
  facebook: {
    oauth: 'Continue with Facebook',
    keyHelp: 'Page → Live producer → Streaming software. Turn on “Use a persistent stream key” so the key survives between broadcasts.',
    defaultServer: 'rtmps://live-api-s.facebook.com:443/rtmp',
  },
  tiktok: {
    keyHelp: 'TikTok Live Studio → Settings. Requires LIVE permission on the account.',
    note: 'TikTok issues a fresh key for each broadcast, so a stream that runs for days needs the key re-pasted whenever it restarts. Better suited to scheduled runs than to unattended 24/7.',
    defaultServer: 'rtmp://push-rtmp-l1-va01.tiktokcdn.com/live',
  },
  twitch: {
    oauth: 'Sign in with Twitch',
    keyHelp: 'Creator Dashboard → Settings → Stream → Primary Stream Key.',
    defaultServer: 'rtmp://live.twitch.tv/app',
  },
  custom: {
    keyHelp: 'Copy the RTMP server URL and key from the platform’s streaming settings.',
    note: 'Works with Kick, Rumble, LinkedIn, X, or anything else that speaks RTMP.',
    defaultServer: '',
  },
}

export type Destination = {
  id: string
  platform: Platform
  /** User-facing label. Multiple accounts on one platform are the normal case. */
  label: string
  account: string
  auth: Auth
  /** Per-account: one destination can loop videos while another takes the live feed. */
  source: Source
  status: DestinationStatus
  /** Seconds since this destination went live. */
  uptime: number
  /** Actual outbound bitrate in kbps. */
  bitrate: number
  /** Frames dropped since going live. */
  dropped: number
  issue?: Issue
}

export function totalDuration(clips: VideoClip[]): number {
  return clips.reduce((sum, c) => sum + c.duration, 0)
}

export function formatDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.round(seconds % 60)
  const pad = (n: number) => n.toString().padStart(2, '0')
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`
}

/**
 * Which clip is on air, derived from elapsed time rather than stored, so the
 * display cannot drift out of step with the playhead.
 *
 * `shuffle` is treated as sequential here; the real player will pick an order
 * when the playlist starts and follow it.
 */
export function currentClip(
  clips: VideoClip[],
  loop: LoopMode,
  elapsed: number,
): { clip: VideoClip; index: number; into: number } | null {
  if (clips.length === 0) return null
  if (loop === 'one') {
    const first = clips[0]!
    return { clip: first, index: 0, into: elapsed % first.duration }
  }
  const total = totalDuration(clips)
  if (total === 0) return null
  let t = elapsed % total
  for (let i = 0; i < clips.length; i++) {
    const clip = clips[i]!
    if (t < clip.duration) return { clip, index: i, into: t }
    t -= clip.duration
  }
  return { clip: clips[0]!, index: 0, into: 0 }
}

/**
 * Clips that do not share one resolution cannot be concatenated without
 * re-encoding. Flagging it up front turns a mid-stream stutter into a decision
 * made before going live.
 */
export function mixedResolutions(clips: VideoClip[]): boolean {
  if (clips.length < 2) return false
  const first = clips[0]!
  return clips.some((c) => c.width !== first.width || c.height !== first.height)
}

export const PLATFORM_NAMES: Record<Platform, string> = {
  youtube: 'YouTube',
  facebook: 'Facebook',
  tiktok: 'TikTok',
  twitch: 'Twitch',
  custom: 'Custom RTMP',
}

/** A destination is "occupied" whenever it is not sitting idle or failed. */
export function isActive(status: DestinationStatus): boolean {
  return status === 'connecting' || status === 'live' || status === 'degraded' || status === 'reconnecting'
}

export type PlatformGroup = { platform: Platform; destinations: Destination[] }

/**
 * Group destinations by platform, preserving the order platforms first appear.
 * Several accounts on one platform is the normal case, not an edge case, so the
 * interface leads with that grouping.
 */
export function groupByPlatform(destinations: Destination[]): PlatformGroup[] {
  const groups: PlatformGroup[] = []
  for (const d of destinations) {
    const existing = groups.find((g) => g.platform === d.platform)
    if (existing) existing.destinations.push(d)
    else groups.push({ platform: d.platform, destinations: [d] })
  }
  return groups
}

export function formatUptime(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  const pad = (n: number) => n.toString().padStart(2, '0')
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`
}

export function formatBitrate(kbps: number): string {
  return kbps >= 1000 ? `${(kbps / 1000).toFixed(1)} Mbps` : `${kbps} kbps`
}
