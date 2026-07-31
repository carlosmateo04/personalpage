import type { Destination, VideoClip } from './types'

const clip = (id: string, name: string, duration: number, w = 1920, h = 1080): VideoClip => ({
  id,
  name,
  duration,
  width: w,
  height: h,
  codec: 'h264',
})

/**
 * Placeholder destinations until M7 persists real ones.
 *
 * Modelled on the actual target setup: two brands, each looping its own video
 * around the clock on its own accounts, running at the same time from one Mac.
 * A live-feed destination is included as well, since both source kinds have to
 * coexist.
 */
export const MOCK_DESTINATIONS: Destination[] = [
  {
    id: 'yt-pipo',
    platform: 'youtube',
    label: 'Pipo y Lula',
    account: '@pipoylula',
    source: {
      kind: 'playlist',
      loop: 'all',
      clips: [
        clip('p1', 'pipo-lula-episodio-1.mp4', 727),
        clip('p2', 'pipo-lula-episodio-2.mp4', 692),
        clip('p3', 'pipo-lula-canciones.mp4', 1284),
      ],
    },
    status: 'idle',
    uptime: 0,
    bitrate: 0,
    dropped: 0,
  },
  {
    id: 'yt-curiora',
    platform: 'youtube',
    label: 'Curiora',
    account: '@curiora',
    source: {
      kind: 'playlist',
      loop: 'all',
      clips: [clip('c1', 'curiora-compilado.mp4', 2145), clip('c2', 'curiora-intro.mp4', 63)],
    },
    status: 'idle',
    uptime: 0,
    bitrate: 0,
    dropped: 0,
  },
  {
    id: 'fb-pipo',
    platform: 'facebook',
    label: 'Pipo y Lula',
    account: 'Pipo y Lula',
    source: { kind: 'playlist', loop: 'all', clips: [clip('p4', 'pipo-lula-mix.mp4', 3600)] },
    status: 'idle',
    uptime: 0,
    bitrate: 0,
    dropped: 0,
  },
  {
    id: 'fb-curiora',
    platform: 'facebook',
    label: 'Curiora',
    account: 'Curiora',
    source: { kind: 'playlist', loop: 'all', clips: [clip('c3', 'curiora-compilado.mp4', 2145)] },
    status: 'idle',
    uptime: 0,
    bitrate: 0,
    dropped: 0,
  },
  {
    id: 'tt-curiora',
    platform: 'tiktok',
    label: 'Curiora',
    account: '@curiora',
    source: {
      kind: 'playlist',
      loop: 'all',
      // Vertical, plus one landscape file to exercise the mismatch warning.
      clips: [
        clip('c4', 'curiora-vertical.mp4', 58, 1080, 1920),
        clip('c5', 'curiora-compilado.mp4', 2145),
      ],
    },
    status: 'idle',
    uptime: 0,
    bitrate: 0,
    dropped: 0,
  },
  {
    id: 'tw-live',
    platform: 'twitch',
    label: 'Directo',
    account: 'carlosmateo',
    source: { kind: 'live' },
    status: 'idle',
    uptime: 0,
    bitrate: 0,
    dropped: 0,
  },
]

/** Stand-in for the file picker until M5 wires up a real one. */
export const SAMPLE_LIBRARY: VideoClip[] = [
  clip('l1', 'pipo-lula-episodio-3.mp4', 731),
  clip('l2', 'curiora-especial.mp4', 1502),
  clip('l3', 'cortinilla-10s.mp4', 10),
  clip('l4', 'pipo-lula-vertical.mp4', 120, 1080, 1920),
  clip('l5', 'archivo-4k.mp4', 940, 3840, 2160),
]
