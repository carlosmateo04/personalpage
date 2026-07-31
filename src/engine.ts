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
}

/** Mirrors `stream::Ended`. */
export type Ended = {
  id: string
  code: number | null
  deliberate: boolean
  error: string | null
}

export const engine = {
  toolStatus: () => invoke<ToolStatus>('tool_status'),
  probeVideo: (path: string) => invoke<VideoInfo>('probe_video', { path }),
  runningLoops: () => invoke<string[]>('running_loops'),

  startLoop: (id: string, file: string, server: string, key: string) =>
    invoke<void>('start_loop', { id, file, server, key }),

  stopLoop: (id: string) => invoke<void>('stop_loop', { id }),

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
 * Handlers are held in refs by the caller, so the listeners are registered once
 * rather than being torn down and re-attached whenever a handler identity
 * changes — losing an event during a re-render would desynchronise the UI from
 * a stream that is genuinely running.
 */
export function useStreamEvents(
  onProgress: (p: Progress) => void,
  onEnded: (e: Ended) => void,
): void {
  const progressRef = useRef(onProgress)
  const endedRef = useRef(onEnded)
  progressRef.current = onProgress
  endedRef.current = onEnded

  useEffect(() => {
    const unlisten: Array<() => void> = []
    let cancelled = false

    const attach = <T,>(name: string, ref: { current: (payload: T) => void }) => {
      listen<T>(name, (e) => ref.current(e.payload)).then((fn) => {
        if (cancelled) fn()
        else unlisten.push(fn)
      })
    }

    attach<Progress>('stream:progress', progressRef)
    attach<Ended>('stream:ended', endedRef)

    return () => {
      cancelled = true
      unlisten.forEach((fn) => fn())
    }
  }, [])
}
