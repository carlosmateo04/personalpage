import type { Ended, Progress } from './engine'
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

    // Frames arriving is what actually proves the platform accepted the
    // connection, so that — not the process starting — ends "connecting".
    const connected = p.frames > 0
    const struggling = p.speed > 0 && p.speed < DEGRADED_SPEED

    return {
      ...d,
      status: connected ? (struggling ? 'degraded' : 'live') : d.status,
      uptime: Math.floor(p.out_time_s),
      bitrate: Math.round(p.bitrate_kbps),
      dropped: p.dropped,
    }
  })
}

/**
 * Fold a process exit into the destination list.
 *
 * A deliberate stop returns the destination to idle; anything else is a
 * failure that has to say what happened and offer a way back.
 */
export function applyEnded(destinations: Destination[], e: Ended): Destination[] {
  return destinations.map((d) => {
    if (d.id !== e.id) return d

    if (e.deliberate) {
      return { ...d, status: 'idle', uptime: 0, bitrate: 0, dropped: 0, issue: undefined }
    }

    return {
      ...d,
      status: 'failed',
      bitrate: 0,
      issue: {
        title: 'Stream stopped unexpectedly',
        detail:
          e.error ??
          `ffmpeg exited with code ${e.code ?? 'unknown'}. Automatic recovery arrives in M2; for now, start it again.`,
        action: { label: 'Start again', kind: 'retry' },
        raw: e.error ?? undefined,
      },
    }
  })
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
  if (!d.auth.secret) return 'Open this account and paste its stream key.'
  return null
}

/** Mask a key for display. The full value never leaves memory. */
export function maskKey(secret: string): string {
  return secret.length > 8 ? `${secret.slice(0, 4)}…${secret.slice(-4)}` : `${secret.slice(0, 2)}…`
}
