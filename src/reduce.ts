import type { Progress, StatusEvent } from './engine'
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

/**
 * The shape written to disk: everything except anything secret. Stream keys
 * live in the Keychain and are referenced by destination id alone, so a leaked
 * or synced configuration file gives nothing away.
 */
export function toStored(destinations: Destination[]): unknown {
  return {
    version: 1,
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
