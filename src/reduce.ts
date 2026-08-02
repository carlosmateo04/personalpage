import type { NetSample, Progress, StatusEvent } from './engine'
import type { Destination } from './types'

/** Below this multiple of real time, ffmpeg is not keeping up with the clock. */
export const DEGRADED_SPEED = 0.95

/**
 * Fold one ffmpeg progress sample into the destination list.
 *
 * Kept pure and separate from the component so the state machine can be tested
 * without a browser or a running backend — these transitions are what the whole
 * status display rests on.
 */
export function applyProgress(destinations: Destination[], p: Progress): Destination[] {
  return destinations.map((d) => {
    if (d.id !== p.id) return d

    // Telemetry must never drag a destination out of a failure or a pending
    // reconnection: those phases are the backend's to leave.
    if (d.status === 'failed' || d.status === 'reconnecting') {
      return { ...d, bitrate: Math.round(p.bitrate_kbps), reconnects: p.reconnects }
    }

    const struggling = p.speed > 0 && p.speed < DEGRADED_SPEED
    return {
      ...d,
      status: struggling ? 'degraded' : d.status === 'connecting' ? d.status : 'live',
      uptime: Math.floor(p.out_time_s),
      bitrate: Math.round(p.bitrate_kbps),
      dropped: p.dropped,
      reconnects: p.reconnects,
    }
  })
}

/**
 * Fold a backend phase transition into the destination list.
 *
 * The backend is the single authority on phase — the frontend used to infer
 * "live" from frames arriving, which cannot express reconnecting at all.
 */
export function applyStatus(destinations: Destination[], s: StatusEvent): Destination[] {
  return destinations.map((d) => {
    if (d.id !== s.id) return d

    const base = { ...d, attempt: s.attempt, retryIn: s.retry_in, reconnects: s.reconnects }

    switch (s.phase) {
      case 'connecting':
        return { ...base, status: 'connecting', issue: undefined, bitrate: 0 }

      case 'live':
        return { ...base, status: 'live', issue: undefined, attempt: 0, retryIn: 0 }

      case 'reconnecting':
        // The issue is kept so the card can say *why* it is reconnecting; the
        // stream is not lost, so this is not a failure.
        return {
          ...base,
          status: 'reconnecting',
          bitrate: 0,
          issue: s.issue ?? undefined,
        }

      case 'failed':
        return { ...base, status: 'failed', bitrate: 0, issue: s.issue ?? undefined }

      case 'stopped':
        return {
          ...base,
          status: 'idle',
          uptime: 0,
          bitrate: 0,
          dropped: 0,
          attempt: 0,
          retryIn: 0,
          issue: undefined,
        }
    }
  })
}

/** Count down the retry timer between backend events, purely for display. */
export function tickRetry(destinations: Destination[]): Destination[] {
  return destinations.map((d) =>
    d.status === 'reconnecting' && d.retryIn > 0 ? { ...d, retryIn: d.retryIn - 1 } : d,
  )
}

/**
 * Why a destination cannot stream yet, in the words the user needs, or null
 * when it is ready. Returned as a message rather than a boolean so the reason
 * can be shown instead of the button merely being dead.
 */
export function startBlocker(d: Destination): string | null {
  if (d.source.kind !== 'playlist') {
    return 'A shared live feed needs a capture source, which arrives in M10. Choose a video file for now.'
  }
  if (d.source.clips.length === 0) return 'Add a video for this account first.'
  if (d.auth.method !== 'key') return 'This account has no stream key.'
  if (!d.auth.hasSecret) return 'Open this account and paste its stream key.'
  return null
}

// ------------------------------------------------------------- bandwidth ---

/**
 * Assumed uplink when nothing better is known. Deliberately a round number
 * that looks like a guess, and the UI labels it as one — a headroom figure
 * derived from a fiction should not look like a measurement.
 */
export const DEFAULT_UPLINK_KBPS = 25_000

export type Bandwidth = {
  /** Everything leaving the machine, measured at the interface. */
  totalKbps: number
  /** The part of it this app is responsible for. */
  streamsKbps: number
  /** Everything else running on this Mac. Never negative. */
  otherKbps: number
  capacityKbps: number
  freeKbps: number
  usedPct: number
  /**
   * False when the total is only the sum of our own publishers, because the
   * interface counters could not be read. Then "free" is a guess about a
   * guess, and the UI has to say so.
   */
  measured: boolean
}

/**
 * Everything the bandwidth strip shows, derived in one place so the arithmetic
 * can be tested without a running interface.
 *
 * `peakKbps` is the highest total seen this session. It only ever raises the
 * ceiling: whatever the configured uplink claims, the link demonstrably
 * carried the peak, so a capacity below it is simply wrong.
 */
export function bandwidth(input: {
  streamsKbps: number
  sample: NetSample | null
  uplinkKbps: number | null
  peakKbps: number
}): Bandwidth {
  const { streamsKbps, sample, uplinkKbps, peakKbps } = input
  const measured = sample?.measured === true

  const totalKbps = measured ? sample!.up_kbps : streamsKbps
  // ffmpeg reports payload bitrate; the interface counts RTMP, TCP and IP
  // headers too, so measured is normally the larger of the two. When a sample
  // lands between two progress updates it can briefly be the smaller one, and
  // a negative "other apps" figure would be nonsense.
  const otherKbps = Math.max(0, totalKbps - streamsKbps)

  const capacityKbps = Math.max(uplinkKbps ?? DEFAULT_UPLINK_KBPS, peakKbps, 1)
  const freeKbps = Math.max(0, capacityKbps - totalKbps)
  const usedPct = Math.min(100, Math.round((totalKbps / capacityKbps) * 100))

  return { totalKbps, streamsKbps, otherKbps, capacityKbps, freeKbps, usedPct, measured }
}

/** Settings that are not about any one destination. */
export type Settings = {
  /** The uplink the user says they have, in kbps, or null to assume. */
  uplinkKbps: number | null
}

export const DEFAULT_SETTINGS: Settings = { uplinkKbps: null }

/**
 * The shape written to disk: everything except anything secret. Stream keys
 * live in the Keychain and are referenced by destination id alone, so a leaked
 * or synced configuration file gives nothing away.
 */
export function toStored(
  destinations: Destination[],
  settings: Settings = DEFAULT_SETTINGS,
): unknown {
  return {
    version: 1,
    settings,
    destinations: destinations.map((d) => ({
      id: d.id,
      platform: d.platform,
      label: d.label,
      account: d.account,
      auth:
        d.auth.method === 'key'
          ? { method: 'key', server: d.auth.server }
          : { method: 'oauth', provider: d.auth.provider, connectedAs: d.auth.connectedAs },
      source: d.source,
      autoStart: d.autoStart,
    })),
  }
}

/**
 * Rebuild destinations from disk. Everything runtime — status, telemetry,
 * whether a key exists — is restored as unknown and filled in afterwards from
 * the backend, so a stale file can never claim a stream is live.
 */
/**
 * Read back the non-destination settings. Anything absent or nonsensical
 * falls back to the default rather than throwing: a configuration file written
 * by an older build must still open.
 */
export function settingsFromStored(json: string): Settings {
  try {
    const raw = (JSON.parse(json) as { settings?: Record<string, unknown> }).settings
    const uplink = Number(raw?.uplinkKbps)
    return { uplinkKbps: Number.isFinite(uplink) && uplink > 0 ? Math.round(uplink) : null }
  } catch {
    return DEFAULT_SETTINGS
  }
}

export function fromStored(json: string, idsWithSecrets: string[]): Destination[] {
  const parsed = JSON.parse(json) as {
    destinations?: Array<Record<string, unknown>>
  }
  const withSecret = new Set(idsWithSecrets)

  return (parsed.destinations ?? []).map((raw) => {
    const id = String(raw.id)
    const auth = raw.auth as Record<string, unknown> | undefined
    return {
      id,
      platform: raw.platform as Destination['platform'],
      label: String(raw.label ?? 'Untitled'),
      account: String(raw.account ?? ''),
      auth:
        auth?.method === 'oauth'
          ? {
              method: 'oauth',
              provider: String(auth.provider ?? ''),
              connectedAs: String(auth.connectedAs ?? ''),
            }
          : {
              method: 'key',
              server: String(auth?.server ?? ''),
              hasSecret: withSecret.has(id),
            },
      source: (raw.source as Destination['source']) ?? { kind: 'playlist', clips: [], loop: 'all' },
      status: 'idle',
      uptime: 0,
      bitrate: 0,
      dropped: 0,
      attempt: 0,
      retryIn: 0,
      reconnects: 0,
      autoStart: Boolean(raw.autoStart),
    }
  })
}
