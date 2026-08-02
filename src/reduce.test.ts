import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  applyProgress,
  applyStatus,
  fromStored,
  startBlocker,
  tickRetry,
  toStored,
} from './reduce.ts'
import type { Destination } from './types.ts'

function dest(over: Partial<Destination> = {}): Destination {
  return {
    id: 'yt-pipo',
    platform: 'youtube',
    label: 'Pipo y Lula',
    account: 'Stream key',
    auth: { method: 'key', server: 'rtmp://x/live', hasSecret: true },
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
    attempt: 0,
    retryIn: 0,
    reconnects: 0,
    autoStart: false,
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
  reconnects: 0,
  ...over,
})

const status = (over = {}) => ({
  id: 'yt-pipo',
  phase: 'live' as const,
  attempt: 0,
  retry_in: 0,
  issue: null,
  reconnects: 0,
  ...over,
})

const issue = (over = {}) => ({
  title: 'Connection dropped',
  detail: 'The platform closed the connection mid-stream.',
  action: 'retry' as const,
  retryable: true,
  raw: 'av_interleaved_write_frame(): Broken pipe',
  ...over,
})

// ---------------------------------------------------------------- progress ---

test('progress updates the numbers a live card shows', () => {
  const [d] = applyProgress([dest({ status: 'live' })], progress())
  assert.equal(d!.uptime, 22)
  assert.equal(d!.bitrate, 4211)
  assert.equal(d!.status, 'live')
})

test('falling behind real time reads as degraded', () => {
  const [d] = applyProgress([dest({ status: 'live' })], progress({ speed: 0.72 }))
  assert.equal(d!.status, 'degraded')
})

test('speed of exactly zero is not treated as falling behind', () => {
  // ffmpeg reports speed=0 before it has measured anything; treating that as
  // degraded would make every stream flash a warning on startup.
  const [d] = applyProgress([dest({ status: 'live' })], progress({ speed: 0 }))
  assert.equal(d!.status, 'live')
})

test('late telemetry cannot resurrect a failed destination', () => {
  // ffmpeg's last progress block can arrive after the process has already died.
  // Letting it overwrite the failure would erase the reason on screen.
  const [d] = applyProgress([dest({ status: 'failed', issue: issue() })], progress())
  assert.equal(d!.status, 'failed')
  assert.ok(d!.issue, 'the explanation must survive')
})

test('late telemetry cannot cancel a pending reconnection', () => {
  const [d] = applyProgress([dest({ status: 'reconnecting', retryIn: 8 })], progress())
  assert.equal(d!.status, 'reconnecting')
  assert.equal(d!.retryIn, 8)
})

test('progress only touches the destination it names', () => {
  const other = dest({ id: 'yt-curiora', status: 'live', bitrate: 1234 })
  const [mine, theirs] = applyProgress([dest({ status: 'live' }), other], progress())
  assert.equal(mine!.bitrate, 4211)
  assert.equal(theirs!.bitrate, 1234, 'a sibling must not move')
})

// ------------------------------------------------------------------ status ---

test('the backend decides when a destination is live', () => {
  const [d] = applyStatus([dest()], status({ phase: 'live' }))
  assert.equal(d!.status, 'live')
  assert.equal(d!.attempt, 0)
})

test('reconnecting keeps the reason and the countdown', () => {
  const [d] = applyStatus(
    [dest({ status: 'live' })],
    status({ phase: 'reconnecting', attempt: 3, retry_in: 8, issue: issue(), reconnects: 3 }),
  )
  assert.equal(d!.status, 'reconnecting')
  assert.equal(d!.attempt, 3)
  assert.equal(d!.retryIn, 8)
  assert.equal(d!.reconnects, 3)
  assert.match(d!.issue!.title, /Connection dropped/)
  assert.equal(d!.issue!.retryable, true, 'a reconnection is not a dead end')
})

test('a failure that will not be retried says so', () => {
  const [d] = applyStatus(
    [dest({ status: 'live' })],
    status({
      phase: 'failed',
      issue: issue({ title: 'Stream key rejected', action: 'reauth', retryable: false }),
    }),
  )
  assert.equal(d!.status, 'failed')
  assert.equal(d!.issue!.retryable, false)
  assert.equal(d!.issue!.action, 'reauth')
})

test('going live after reconnecting clears the attempt counters', () => {
  const reconnecting = dest({ status: 'reconnecting', attempt: 4, retryIn: 16, issue: issue() })
  const [d] = applyStatus([reconnecting], status({ phase: 'live', reconnects: 4 }))
  assert.equal(d!.status, 'live')
  assert.equal(d!.attempt, 0)
  assert.equal(d!.retryIn, 0)
  assert.equal(d!.issue, undefined)
  assert.equal(d!.reconnects, 4, 'but the session total is still worth showing')
})

test('stopping clears everything back to idle', () => {
  const live = dest({ status: 'live', uptime: 9000, bitrate: 4200, dropped: 7, reconnects: 2 })
  const [d] = applyStatus([live], status({ phase: 'stopped' }))
  assert.equal(d!.status, 'idle')
  assert.equal(d!.uptime, 0)
  assert.equal(d!.dropped, 0)
  assert.equal(d!.issue, undefined)
})

test('the retry countdown ticks down and stops at zero', () => {
  let list = [dest({ status: 'reconnecting', retryIn: 2 })]
  list = tickRetry(list)
  assert.equal(list[0]!.retryIn, 1)
  list = tickRetry(list)
  assert.equal(list[0]!.retryIn, 0)
  list = tickRetry(list)
  assert.equal(list[0]!.retryIn, 0, 'must not go negative')
})

test('the countdown leaves destinations that are not reconnecting alone', () => {
  const [d] = tickRetry([dest({ status: 'live', retryIn: 5 })])
  assert.equal(d!.retryIn, 5)
})

// ---------------------------------------------------------------- blockers ---

test('a ready destination has no blocker', () => {
  assert.equal(startBlocker(dest()), null)
})

test('each missing prerequisite names itself', () => {
  assert.match(
    startBlocker(dest({ source: { kind: 'playlist', loop: 'all', clips: [] } }))!,
    /Add a video/,
  )
  assert.match(
    startBlocker(dest({ auth: { method: 'key', server: 'rtmp://x', hasSecret: false } }))!,
    /paste its stream key/,
  )
  assert.match(startBlocker(dest({ source: { kind: 'live' } }))!, /capture source/)
})

// ------------------------------------------------------------- persistence ---

test('nothing secret is written to disk', () => {
  const stored = JSON.stringify(toStored([dest()]))
  assert.ok(!stored.includes('hasSecret'), 'key presence is re-derived, not stored')
  assert.ok(!/secret|password|key"\s*:/i.test(stored.replace(/"Stream key"/g, '')))
  assert.ok(stored.includes('rtmp://x/live'), 'the server is not a secret')
})

test('a saved configuration round-trips', () => {
  const original = dest({ autoStart: true })
  const json = JSON.stringify(toStored([original]))
  const [restored] = fromStored(json, ['yt-pipo'])

  assert.equal(restored!.id, original.id)
  assert.equal(restored!.label, original.label)
  assert.equal(restored!.autoStart, true)
  assert.deepEqual(restored!.source, original.source)
  assert.equal(restored!.auth.method, 'key')
  assert.equal((restored!.auth as { hasSecret: boolean }).hasSecret, true)
})

test('restoring never claims a stream is still live', () => {
  const wasLive = dest({ status: 'live', uptime: 90_000, bitrate: 4200, reconnects: 5 })
  const [restored] = fromStored(JSON.stringify(toStored([wasLive])), [])
  assert.equal(restored!.status, 'idle')
  assert.equal(restored!.uptime, 0)
  assert.equal(restored!.reconnects, 0)
})

test('a key missing from the Keychain leaves the destination unready', () => {
  const [restored] = fromStored(JSON.stringify(toStored([dest()])), [])
  assert.equal((restored!.auth as { hasSecret: boolean }).hasSecret, false)
  assert.match(startBlocker(restored!)!, /paste its stream key/)
})

test('an empty or unfamiliar configuration does not throw', () => {
  assert.deepEqual(fromStored('{"version":1,"destinations":[]}', []), [])
  assert.deepEqual(fromStored('{}', []), [])
})
