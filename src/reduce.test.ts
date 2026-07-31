import assert from 'node:assert/strict'
import { test } from 'node:test'
import { applyEnded, applyProgress, maskKey, startBlocker } from './reduce.ts'
import type { Destination } from './types.ts'

function dest(over: Partial<Destination> = {}): Destination {
  return {
    id: 'yt-pipo',
    platform: 'youtube',
    label: 'Pipo y Lula',
    account: 'Stream key',
    auth: { method: 'key', server: 'rtmp://x/live', keyPreview: 'ab…yz', secret: 'abcd1234wxyz' },
    source: {
      kind: 'playlist',
      loop: 'all',
      clips: [
        {
          id: 'c1',
          path: '/v/pipo.mp4',
          name: 'pipo.mp4',
          duration: 727,
          width: 1920,
          height: 1080,
          fps: 30,
          video_codec: 'h264',
          audio_codec: 'aac',
          bitrate_kbps: 4200,
          keyframe_interval: 2,
          can_copy: true,
          findings: [],
        },
      ],
    },
    status: 'connecting',
    uptime: 0,
    bitrate: 0,
    dropped: 0,
    ...over,
  }
}

const progress = (over = {}) => ({
  id: 'yt-pipo',
  frames: 667,
  fps: 30,
  bitrate_kbps: 4210.5,
  total_bytes: 7_357_631,
  out_time_s: 22.7,
  dropped: 0,
  duplicated: 0,
  speed: 1.02,
  ...over,
})

test('frames arriving is what turns connecting into live', () => {
  const [d] = applyProgress([dest()], progress())
  assert.equal(d!.status, 'live')
  assert.equal(d!.uptime, 22)
  assert.equal(d!.bitrate, 4211)
})

test('a progress sample with no frames yet leaves it connecting', () => {
  const [d] = applyProgress([dest()], progress({ frames: 0, speed: 0 }))
  assert.equal(d!.status, 'connecting', 'the process running is not proof the platform accepted it')
})

test('falling behind real time reads as degraded, not live', () => {
  const [d] = applyProgress([dest()], progress({ speed: 0.72 }))
  assert.equal(d!.status, 'degraded')
})

test('speed of exactly zero is not treated as falling behind', () => {
  // ffmpeg reports speed=0 before it has measured anything; treating that as
  // degraded would make every stream flash a warning on startup.
  const [d] = applyProgress([dest()], progress({ speed: 0 }))
  assert.equal(d!.status, 'live')
})

test('progress only touches the destination it names', () => {
  const other = dest({ id: 'yt-curiora', status: 'live', bitrate: 1234 })
  const [mine, theirs] = applyProgress([dest(), other], progress())
  assert.equal(mine!.status, 'live')
  assert.equal(theirs!.bitrate, 1234, 'a sibling must not move')
})

test('a deliberate stop returns to idle and clears the numbers', () => {
  const live = dest({ status: 'live', uptime: 900, bitrate: 4200, dropped: 7 })
  const [d] = applyEnded([live], { id: 'yt-pipo', code: 0, deliberate: true, error: null })
  assert.equal(d!.status, 'idle')
  assert.equal(d!.uptime, 0)
  assert.equal(d!.dropped, 0)
  assert.equal(d!.issue, undefined)
})

test('an unexpected exit surfaces ffmpeg’s reason', () => {
  const [d] = applyEnded([dest({ status: 'live' })], {
    id: 'yt-pipo',
    code: 1,
    deliberate: false,
    error: 'Connection to tcp://a.rtmp.youtube.com:1935 failed: Connection refused',
  })
  assert.equal(d!.status, 'failed')
  assert.match(d!.issue!.detail, /Connection refused/)
  assert.equal(d!.issue!.action!.kind, 'retry')
})

test('an unexpected exit with no stderr still explains itself', () => {
  const [d] = applyEnded([dest({ status: 'live' })], {
    id: 'yt-pipo',
    code: 137,
    deliberate: false,
    error: null,
  })
  assert.equal(d!.status, 'failed')
  assert.match(d!.issue!.detail, /137/)
})

test('a ready destination has no blocker', () => {
  assert.equal(startBlocker(dest()), null)
})

test('each missing prerequisite names itself', () => {
  const noClips = dest({ source: { kind: 'playlist', loop: 'all', clips: [] } })
  assert.match(startBlocker(noClips)!, /Add a video/)

  const noSecret = dest({
    auth: { method: 'key', server: 'rtmp://x/live', keyPreview: '—' },
  })
  assert.match(startBlocker(noSecret)!, /paste its stream key/)

  const liveFeed = dest({ source: { kind: 'live' } })
  assert.match(startBlocker(liveFeed)!, /capture source/)
})

test('masking never reveals the middle of a key', () => {
  assert.equal(maskKey('abcd-1234-efgh-5678'), 'abcd…5678')
  assert.equal(maskKey('short'), 'sh…')
  assert.ok(!maskKey('abcd-1234-efgh-5678').includes('1234-efgh'))
})
