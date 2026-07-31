import type { Destination } from './types'

/**
 * Placeholder destinations until M6 persists real ones.
 *
 * Deliberately includes several accounts on the same platform — two YouTube
 * channels, two Facebook identities — because running multiple accounts *and*
 * multiple platforms at once is the whole point of the app, and a one-per-
 * platform list fails to show it.
 */
export const MOCK_DESTINATIONS: Destination[] = [
  {
    id: 'yt-main',
    platform: 'youtube',
    label: 'Main channel',
    account: '@carlosmateo',
    status: 'idle',
    uptime: 0,
    bitrate: 0,
    dropped: 0,
  },
  {
    id: 'yt-es',
    platform: 'youtube',
    label: 'Canal en español',
    account: '@carlosmateo-es',
    status: 'idle',
    uptime: 0,
    bitrate: 0,
    dropped: 0,
  },
  {
    id: 'fb-page',
    platform: 'facebook',
    label: 'Business page',
    account: 'Carlos Mateo Media',
    status: 'idle',
    uptime: 0,
    bitrate: 0,
    dropped: 0,
  },
  {
    id: 'fb-me',
    platform: 'facebook',
    label: 'Personal profile',
    account: 'Carlos Mateo',
    status: 'idle',
    uptime: 0,
    bitrate: 0,
    dropped: 0,
  },
  {
    id: 'tt',
    platform: 'tiktok',
    label: 'TikTok LIVE',
    account: '@carlosmateo',
    status: 'idle',
    uptime: 0,
    bitrate: 0,
    dropped: 0,
  },
  {
    id: 'tw',
    platform: 'twitch',
    label: 'Twitch',
    account: 'carlosmateo',
    status: 'idle',
    uptime: 0,
    bitrate: 0,
    dropped: 0,
  },
]
