import { useState } from 'react'
import {
  formatDuration,
  mixedResolutions,
  totalDuration,
  type Destination,
  type LoopMode,
  type VideoClip,
} from '../types'
import { engine } from '../engine'

const LOOP_LABELS: Record<LoopMode, string> = {
  all: 'Loop the whole playlist',
  one: 'Loop the first video only',
  shuffle: 'Shuffle, then loop',
}

export function PlaylistModal({
  destination: d,
  onClose,
  onChange,
  onUseLive,
}: {
  destination: Destination
  onClose: () => void
  onChange: (clips: VideoClip[], loop: LoopMode) => void
  onUseLive: () => void
}) {
  const initial = d.source.kind === 'playlist' ? d.source : { clips: [], loop: 'all' as LoopMode }
  const [clips, setClips] = useState<VideoClip[]>(initial.clips)
  const [loop, setLoop] = useState<LoopMode>(initial.loop)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  const commit = (nextClips: VideoClip[], nextLoop: LoopMode = loop) => {
    setClips(nextClips)
    setLoop(nextLoop)
    onChange(nextClips, nextLoop)
  }

  const move = (index: number, delta: number) => {
    const target = index + delta
    if (target < 0 || target >= clips.length) return
    const next = [...clips]
    const [moved] = next.splice(index, 1)
    next.splice(target, 0, moved!)
    commit(next)
  }

  /** Pick a file, probe it, and only then add it — a file that cannot be read is not a playlist entry. */
  const addFile = async () => {
    setProblem(null)
    try {
      const path = await engine.pickVideo()
      if (!path) return
      setBusy(true)
      const info = await engine.probeVideo(path)
      commit([
        ...clips,
        { ...info, id: `${info.path}#${clips.length}` },
      ])
    } catch (e) {
      setProblem(String(e))
    } finally {
      setBusy(false)
    }
  }

  const total = totalDuration(clips)
  const mixed = mixedResolutions(clips)
  const first = clips[0]

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <div>
            <h2>Videos for {d.label}</h2>
            <p>
              {d.account} &middot; this video streams only to this account, independently of every
              other destination
            </p>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>

        <div className="modal-body">
          {clips.length === 0 ? (
            <p className="empty">
              No video yet. Choose a file and it will loop endlessly on this account.
            </p>
          ) : (
            <ol className="cliplist">
              {clips.map((c, i) => (
                <li key={c.id} className="clip">
                  <span className="clip-index">{i + 1}</span>
                  <span className="clip-name" title={c.path}>
                    {c.name}
                  </span>
                  <span className="clip-meta">
                    {c.width}×{c.height} &middot; {formatDuration(c.duration)}
                  </span>
                  <span className="clip-actions">
                    <button onClick={() => move(i, -1)} disabled={i === 0} aria-label="Move up">
                      ↑
                    </button>
                    <button
                      onClick={() => move(i, 1)}
                      disabled={i === clips.length - 1}
                      aria-label="Move down"
                    >
                      ↓
                    </button>
                    <button
                      className="clip-remove"
                      onClick={() => commit(clips.filter((x) => x.id !== c.id))}
                      aria-label="Remove"
                    >
                      ✕
                    </button>
                  </span>
                </li>
              ))}
            </ol>
          )}

          <button className="btn-add-clip" onClick={addFile} disabled={busy}>
            {busy ? 'Reading file…' : '+ Choose a video file'}
          </button>

          {problem && <p className="warn-note error-note">{problem}</p>}

          {/* What ffprobe actually found in the file that will be streamed. */}
          {first && (
            <div className="probe">
              <p className="probe-head">
                {first.name} &middot; {first.video_codec.toUpperCase()}
                {first.audio_codec ? ` + ${first.audio_codec.toUpperCase()}` : ' + no audio'}
                {' · '}
                {first.fps.toFixed(first.fps % 1 === 0 ? 0 : 2)} fps
                {first.bitrate_kbps > 0 && ` · ${(first.bitrate_kbps / 1000).toFixed(1)} Mbps`}
                {first.keyframe_interval !== null &&
                  ` · keyframes ${first.keyframe_interval.toFixed(1)}s`}
              </p>
              {first.findings.map((f, i) => (
                <p key={i} className={`finding finding-${f.severity}`}>
                  <strong>{f.title}.</strong> {f.detail}
                </p>
              ))}
            </div>
          )}

          {clips.length > 1 && (
            <p className="warn-note">
              <strong>One video for now.</strong> Only the first file loops in this build. Playing a
              list in sequence is M4; the rest are kept so the order is ready when it lands.
            </p>
          )}

          {mixed && (
            <p className="warn-note">
              <strong>Mixed resolutions.</strong> These clips are not all the same size, so they
              cannot be joined without re-encoding. That gets handled in M4.
            </p>
          )}
        </div>

        <footer className="modal-foot">
          <label className="loop-select">
            Repeat
            <select value={loop} onChange={(e) => commit(clips, e.target.value as LoopMode)}>
              {(Object.keys(LOOP_LABELS) as LoopMode[]).map((m) => (
                <option key={m} value={m}>
                  {LOOP_LABELS[m]}
                </option>
              ))}
            </select>
          </label>

          <span className="modal-total">
            {clips.length} {clips.length === 1 ? 'video' : 'videos'}
            {total > 0 && <> &middot; {formatDuration(total)} per loop</>}
          </span>

          <button className="btn-live-instead" onClick={onUseLive}>
            Use live feed instead
          </button>
        </footer>
      </div>
    </div>
  )
}
