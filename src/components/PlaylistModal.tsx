import { useState } from 'react'
import {
  formatDuration,
  mixedResolutions,
  totalDuration,
  type Destination,
  type LoopMode,
  type VideoClip,
} from '../types'
import { SAMPLE_LIBRARY } from '../mockDestinations'

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
  const [picking, setPicking] = useState(false)

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

  const add = (clip: VideoClip) => {
    // Same file can legitimately appear twice, so give each entry its own id.
    commit([...clips, { ...clip, id: `${clip.id}-${clips.length}-${clip.name}` }])
    setPicking(false)
  }

  const total = totalDuration(clips)
  const mixed = mixedResolutions(clips)

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <div>
            <h2>Videos for {d.label}</h2>
            <p>
              {d.account} &middot; this playlist streams only to this account, independently of
              every other destination
            </p>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>

        <div className="modal-body">
          {clips.length === 0 ? (
            <p className="empty">
              No videos yet. Add one or more files and they will play in order, then loop.
            </p>
          ) : (
            <ol className="cliplist">
              {clips.map((c, i) => (
                <li key={c.id} className="clip">
                  <span className="clip-index">{i + 1}</span>
                  <span className="clip-name" title={c.name}>
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

          {picking ? (
            <div className="picker">
              <p className="picker-head">Choose a file</p>
              {SAMPLE_LIBRARY.map((c) => (
                <button key={c.id} className="picker-row" onClick={() => add(c)}>
                  <span className="clip-name">{c.name}</span>
                  <span className="clip-meta">
                    {c.width}×{c.height} &middot; {formatDuration(c.duration)}
                  </span>
                </button>
              ))}
              <button className="picker-cancel" onClick={() => setPicking(false)}>
                Cancel
              </button>
            </div>
          ) : (
            <button className="btn-add-clip" onClick={() => setPicking(true)}>
              + Add video
            </button>
          )}

          {mixed && (
            <p className="warn-note">
              <strong>Mixed resolutions.</strong> These clips are not all the same size, so they
              cannot be joined without re-encoding. StreamBridge will normalise them once and cache
              the result, which costs disk and a one-off wait, but keeps playback seamless.
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
