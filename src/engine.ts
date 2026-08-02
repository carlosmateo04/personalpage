import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import { open } from '@tauri-apps/plugin-dialog'
import { useEffect, useRef } from 'react'

/** Mirrors `probe::Severity`. */
export type Severity = 'blocker' | 'warning' | 'note'

/** Mirrors `probe::Finding`. */
export type Finding = {
  severity: Severity
  title: string
  detail: string
}

/** Mirrors `probe::VideoInfo`. */
export type VideoInfo = {
  path: string
  name: string
  duration: number
  width: number
  height: number
  fps: number
  video_codec: string
  audio_codec: string | null
  bitrate_kbps: number
  keyframe_interval: number | null
  can_copy: boolean
  findings: Finding[]
}

/** Mirrors `tools::ToolStatus`. */
export type ToolStatus =
  | {
      state: 'ready'
      ffmpeg: string
      ffprobe: string
      source: 'bundled' | 'homebrew' | 'system'
      version: string
    }
  | { state: 'missing'; searched: string[] }

/** Mirrors `diagnose::Action`. */
export type IssueAction = 'retry' | 'reauth' | 'file' | 'network'

/** Mirrors `diagnose::Issue`. */
export type Issue = {
  title: string
  detail: string
  action: IssueAction
  /** Whether the supervisor will keep trying on its own. */
  retryable: boolean
  raw: string
}

/** Mirrors `stream::Phase`. */
export type Phase = 'connecting' | 'live' | 'reconnecting' | 'failed' | 'stopped'

/** Mirrors `stream::StatusEvent`. */
export type StatusEvent = {
  id: string
  phase: Phase
  attempt: number
  retry_in: number
  issue: Issue | null
  reconnects: number
}

/** Mirrors `stream::Progress`. */
export type Progress = {
  id: string
  frames: number
  fps: number
  bitrate_kbps: number
  total_bytes: number
  out_time_s: number
  dropped: number
  duplicated: number
  speed: number
  reconnects: number
}

/**
 * Mirrors `netmeter::NetSample`.
 *
 * `up_kbps` is everything leaving the machine, not just this app: the number
 * that decides whether another destination fits.
 */
export type NetSample = {
  iface: string
  up_kbps: number
  measured: boolean
}

/** Mirrors `release::Release`. */
export type Release = {
  version: string
  dmg_url: string | null
  page_url: string
}

/** Mirrors `google::ClientStatus`. */
export type GoogleClient = {
  configured: boolean
  preview: string
}

/** Console pages, supplied by the backend so the UI never hard-codes a URL
 *  the allowlist would then refuse. */
export type ConsoleUrls = {
  enableApi: string
  consent: string
  credentials: string
}

/** Mirrors `stream::Incident`. */
export type Incident = {
  id: string
  at: number
  kind: string
  message: string
}

export const engine = {
  toolStatus: () => invoke<ToolStatus>('tool_status'),
  probeVideo: (path: string) => invoke<VideoInfo>('probe_video', { path }),
  runningLoops: () => invoke<string[]>('running_loops'),
  incidents: () => invoke<Incident[]>('incidents'),
  keepingAwake: () => invoke<boolean>('keeping_awake'),

  // Updates. `updaterSigned` decides which of the two paths the UI offers:
  // the plugin installs and restarts, the fallback can only point at a
  // download. See `release.rs` for why there is no third option.
  updaterSigned: () => invoke<boolean>('updater_signed'),
  latestRelease: () => invoke<Release | null>('latest_release'),
  openExternal: (url: string) => invoke<void>('open_external', { url }),

  // Registering the app with Google. Once, for the whole app — distinct from
  // signing a channel in, which is per account and stays a single button.
  googleClientStatus: () => invoke<GoogleClient>('google_client_status'),
  googleConsoleUrls: () => invoke<ConsoleUrls>('google_console_urls'),
  setGoogleClient: (id: string, secret: string) =>
    invoke<void>('set_google_client', { id, secret }),
  forgetGoogleClient: () => invoke<void>('forget_google_client'),

  /**
   * Start a loop. The stream key is deliberately absent: the backend reads it
   * from the Keychain itself, so it never crosses this boundary.
   */
  startLoop: (id: string, file: string, server: string) =>
    invoke<void>('start_loop', { id, file, server }),

  stopLoop: (id: string) => invoke<void>('stop_loop', { id }),

  /** Stop every publisher the backend knows about, whatever the UI thinks. */
  stopAll: () => invoke<void>('stop_all'),

  // Secrets. The frontend can write and test for a key, but never read one.
  secretStore: () => invoke<'keychain' | 'memory'>('secret_store'),
  setSecret: (id: string, key: string) => invoke<void>('set_secret', { id, key }),
  deleteSecret: (id: string) => invoke<void>('delete_secret', { id }),
  whichHaveSecrets: (ids: string[]) => invoke<string[]>('which_have_secrets', { ids }),

  // Configuration, minus anything secret.
  saveConfig: (json: string) => invoke<void>('save_config', { json }),
  loadConfig: () => invoke<string | null>('load_config'),
  configLocation: () => invoke<string>('config_location'),

  /** Native file picker, restricted to containers ffmpeg can stream from. */
  pickVideo: () =>
    open({
      multiple: false,
      directory: false,
      filters: [{ name: 'Video', extensions: ['mp4', 'mov', 'mkv', 'm4v', 'ts', 'flv', 'webm'] }],
    }) as Promise<string | null>,
}

/**
 * Subscribe to backend stream events for the life of the component.
 *
 * Handlers are read through refs so the listeners are registered exactly once.
 * Re-attaching them on every render would drop events, and a dropped
 * reconnection event leaves the UI claiming a stream is live when it is not.
 */
export function useStreamEvents(handlers: {
  onProgress: (p: Progress) => void
  onStatus: (s: StatusEvent) => void
  onIncident: (i: Incident) => void
  onNetSample: (s: NetSample) => void
}): void {
  const ref = useRef(handlers)
  ref.current = handlers

  useEffect(() => {
    const unlisten: Array<() => void> = []
    let cancelled = false

    const attach = <T,>(name: string, pick: (h: typeof handlers) => (payload: T) => void) => {
      listen<T>(name, (e) => pick(ref.current)(e.payload)).then((fn) => {
        if (cancelled) fn()
        else unlisten.push(fn)
      })
    }

    attach<Progress>('stream:progress', (h) => h.onProgress)
    attach<StatusEvent>('stream:status', (h) => h.onStatus)
    attach<Incident>('stream:incident', (h) => h.onIncident)
    attach<NetSample>('net:sample', (h) => h.onNetSample)

    return () => {
      cancelled = true
      unlisten.forEach((fn) => fn())
    }
  }, [])
}
