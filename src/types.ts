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

export type Destination = {
  id: string
  platform: Platform
  /** User-facing label. Multiple accounts on one platform are the normal case. */
  label: string
  account: string
  status: DestinationStatus
  /** Seconds since this destination went live. */
  uptime: number
  /** Actual outbound bitrate in kbps. */
  bitrate: number
  /** Frames dropped since going live. */
  dropped: number
  issue?: Issue
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
