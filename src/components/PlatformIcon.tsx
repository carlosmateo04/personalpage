import type { Platform } from '../types'

/** Simple monochrome glyphs — recognisable at a glance, no brand assets bundled. */
export function PlatformIcon({ platform, size = 26 }: { platform: Platform; size?: number }) {
  const common = {
    width: size,
    height: size,
    viewBox: '0 0 24 24',
    fill: 'currentColor',
    'aria-hidden': true as const,
  }

  switch (platform) {
    case 'youtube':
      return (
        <svg {...common}>
          <path d="M21.6 7.2c-.2-1.5-.9-2.2-2.4-2.4C17.4 4.5 12 4.5 12 4.5s-5.4 0-7.2.3c-1.5.2-2.2.9-2.4 2.4C2.1 9 2.1 12 2.1 12s0 3 .3 4.8c.2 1.5.9 2.2 2.4 2.4 1.8.3 7.2.3 7.2.3s5.4 0 7.2-.3c1.5-.2 2.2-.9 2.4-2.4.3-1.8.3-4.8.3-4.8s0-3-.3-4.8ZM10 15.5v-7l6 3.5-6 3.5Z" />
        </svg>
      )
    case 'facebook':
      return (
        <svg {...common}>
          <path d="M22 12a10 10 0 1 0-11.6 9.9v-7H7.9V12h2.5V9.8c0-2.5 1.5-3.9 3.8-3.9 1.1 0 2.2.2 2.2.2v2.5h-1.2c-1.2 0-1.6.8-1.6 1.6V12h2.7l-.4 2.9h-2.3v7A10 10 0 0 0 22 12Z" />
        </svg>
      )
    case 'tiktok':
      return (
        <svg {...common}>
          <path d="M16.5 2h-2.9v13.2a2.5 2.5 0 1 1-2.2-2.5V9.7a5.7 5.7 0 1 0 5.1 5.6V8.8a6.6 6.6 0 0 0 3.9 1.3V7.2a3.8 3.8 0 0 1-3.9-3.7V2Z" />
        </svg>
      )
    case 'twitch':
      return (
        <svg {...common}>
          <path d="M4.3 2 2.5 6.4v14.1h4.8V23h2.7l2.5-2.5h3.9l5.1-5.1V2H4.3Zm15.3 12.5-2.9 2.9h-4.8l-2.5 2.5v-2.5H5.9V3.8h13.7v10.7ZM15.7 7.3h1.8v5.2h-1.8V7.3Zm-4.8 0h1.8v5.2h-1.8V7.3Z" />
        </svg>
      )
    case 'custom':
      return (
        <svg {...common} fill="none" stroke="currentColor" strokeWidth={1.9} strokeLinecap="round">
          <circle cx="12" cy="12" r="2.2" fill="currentColor" stroke="none" />
          <path d="M8.2 8.2a5.4 5.4 0 0 0 0 7.6M15.8 15.8a5.4 5.4 0 0 0 0-7.6M5.5 5.5a9.2 9.2 0 0 0 0 13M18.5 18.5a9.2 9.2 0 0 0 0-13" />
        </svg>
      )
  }
}
