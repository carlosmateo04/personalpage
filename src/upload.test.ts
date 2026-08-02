import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  applyTemplate,
  applyUploadStatus,
  baseName,
  byteLength,
  metadataProblems,
  publishNowKind,
  renderPattern,
  scheduleBlocker,
  uploadQueue,
  uploadSeconds,
  overduePublications,
  DESCRIPTION_MAX_BYTES,
  type Metadata,
  type ScheduledUpload,
  type Template,
} from './upload.ts'

const meta = (over: Partial<Metadata> = {}): Metadata => ({
  title: 'Pipo y Lula — Episodio 1',
  description: 'Un video para dormir.',
  tags: ['pipo', 'lula', 'infantil'],
  categoryId: '24',
  language: 'es',
  playlistId: null,
  madeForKids: true,
  ...over,
})

const upload = (over: Partial<ScheduledUpload> = {}): ScheduledUpload => ({
  id: 'u1',
  destinationId: 'yt-pipo',
  file: '/videos/ep1.mp4',
  name: 'ep1.mp4',
  meta: meta(),
  thumbnailPath: null,
  publishAt: null,
  status: 'scheduled',
  percent: 0,
  videoId: null,
  ...over,
})

// ------------------------------------------------------------------ limits ---

test('a valid set of metadata has nothing to report', () => {
  assert.deepEqual(metadataProblems(meta()), [])
})

test('the description limit is counted in bytes, not characters', () => {
  // This is the whole reason the limit is expressed in bytes. 4,000 accented
  // characters is comfortably under 5,000 characters and comfortably over
  // 5,000 bytes — and Spanish descriptions are full of them.
  const accented = 'á'.repeat(4_000)
  assert.equal([...accented].length, 4_000, 'under the limit if you count characters')
  assert.equal(byteLength(accented), 8_000, 'over it once encoded')

  const [problem] = metadataProblems(meta({ description: accented }))
  assert.match(problem!, /8000 bytes/)
  assert.match(problem!, /4000 characters/, 'says both, or the number looks wrong')
  assert.match(problem!, /accents and emoji/)
})

test('a plain ASCII description is reported in one number, not two', () => {
  const [problem] = metadataProblems(meta({ description: 'a'.repeat(DESCRIPTION_MAX_BYTES + 1) }))
  assert.match(problem!, /5001 characters/)
  assert.doesNotMatch(problem!, /bytes/, 'quoting bytes and characters separately would be noise')
})

test('titles over a hundred characters are caught before the upload starts', () => {
  assert.deepEqual(metadataProblems(meta({ title: 'x'.repeat(100) })), [], 'exactly at the limit')
  const [problem] = metadataProblems(meta({ title: 'x'.repeat(101) }))
  assert.match(problem!, /101 characters.*limit is 100/)
})

test('the tag limit is the total across every tag, not each one', () => {
  const many = Array.from({ length: 20 }, () => 'x'.repeat(26)) // 520 chars in total
  const [problem] = metadataProblems(meta({ tags: many }))
  assert.match(problem!, /520 characters/)
  assert.match(problem!, /across all of them/)
})

test('an empty title and missing required fields are all reported', () => {
  const problems = metadataProblems(meta({ title: '  ', categoryId: '', language: '' }))
  assert.equal(problems.length, 3, 'one message per fix, not one per upload attempt')
})

// ---------------------------------------------------------------- patterns ---

const at = new Date('2026-08-02T21:05:00')

test('a title pattern fills in the file, the position and the date', () => {
  assert.equal(
    renderPattern('{name} — parte {n}', { name: 'Bosque', index: 3, date: at }),
    'Bosque — parte 3',
  )
  assert.equal(renderPattern('{date} {time}', { name: '', index: 1, date: at }), '2026-08-02 21:05')
  assert.equal(renderPattern('Ep {nn}', { name: '', index: 7, date: at }), 'Ep 07')
})

test('an unknown placeholder is left alone rather than blanked', () => {
  // "50% {off}" means those characters. Deleting part of someone's title
  // because it looked like a placeholder is worse than printing it.
  assert.equal(
    renderPattern('{name} {unknown} 50%', { name: 'A', index: 1, date: at }),
    'A {unknown} 50%',
  )
})

test('the file name drops its folder and extension', () => {
  assert.equal(baseName('/Users/carlos/Videos/pipo y lula.mp4'), 'pipo y lula')
  assert.equal(baseName('clip.final.mov'), 'clip.final')
  assert.equal(baseName('noextension'), 'noextension')
  assert.equal(baseName('/a/.hidden'), '.hidden', 'a leading dot is not an extension')
})

test('a template produces metadata for a specific video', () => {
  const t: Template = {
    destinationId: 'yt-pipo',
    titlePattern: 'Pipo y Lula — {name}',
    description: 'Suscríbete',
    tags: ['pipo'],
    categoryId: '24',
    language: 'es',
    playlistId: 'PL123',
    madeForKids: true,
  }
  const m = applyTemplate(t, { name: 'El bosque', index: 1, date: at })
  assert.equal(m.title, 'Pipo y Lula — El bosque')
  assert.equal(m.playlistId, 'PL123')
  assert.deepEqual(metadataProblems(m), [])

  // The template's own tags must not be shared with what it produces, or
  // editing one video's tags would silently edit every future video's.
  m.tags.push('nuevo')
  assert.deepEqual(t.tags, ['pipo'])
})

// ------------------------------------------------------------------- queue ---

test('an item with unusable metadata cannot be scheduled', () => {
  assert.equal(scheduleBlocker(upload()), null)
  assert.match(scheduleBlocker(upload({ file: '' }))!, /No video file/)
  assert.match(scheduleBlocker(upload({ meta: meta({ title: '' }) }))!, /title is empty/)
})

test('the queue puts deadlines before things with no deadline', () => {
  const q = uploadQueue([
    upload({ id: 'whenever', publishAt: null }),
    upload({ id: 'friday', publishAt: '2026-08-07T09:00:00Z' }),
    upload({ id: 'tomorrow', publishAt: '2026-08-03T09:00:00Z' }),
  ])
  assert.deepEqual(
    q.map((u) => u.id),
    ['tomorrow', 'friday', 'whenever'],
  )
})

test('the queue skips anything not ready, so a bad item cannot block the rest', () => {
  const q = uploadQueue([
    upload({ id: 'broken', meta: meta({ title: '' }) }),
    upload({ id: 'fine' }),
    upload({ id: 'done', status: 'uploaded' }),
    upload({ id: 'failed', status: 'failed' }),
  ])
  assert.deepEqual(
    q.map((u) => u.id),
    ['fine'],
  )
})

test('publish now means two different things and says which', () => {
  // Already on YouTube: one small call flips it public. Genuinely instant.
  assert.equal(publishNowKind(upload({ status: 'uploaded', videoId: 'abc' })), 'instant')
  // Not uploaded yet: the file has to go up first, which takes minutes.
  assert.equal(publishNowKind(upload({ status: 'scheduled' })), 'upload-first')
  // Nothing to do, or nothing that can be done.
  assert.equal(publishNowKind(upload({ status: 'published' })), null)
  assert.equal(publishNowKind(upload({ status: 'uploading' })), null)
  assert.equal(publishNowKind(upload({ meta: meta({ title: '' }) })), null)
})

test('the upload estimate uses the headroom left, not the whole link', () => {
  // 100 MB with 10 Mbps free is 80 seconds.
  assert.equal(uploadSeconds(100_000_000, 10_000), 80)
  // A saturated link has no honest estimate to give.
  assert.equal(uploadSeconds(100_000_000, 0), null)
})

test('publications whose moment has passed are surfaced', () => {
  const now = new Date('2026-08-03T10:00:00Z')
  const due = overduePublications(
    [
      upload({ id: 'past', status: 'uploaded', publishAt: '2026-08-03T09:00:00Z' }),
      upload({ id: 'future', status: 'uploaded', publishAt: '2026-08-04T09:00:00Z' }),
      upload({ id: 'not-up-yet', status: 'scheduled', publishAt: '2026-08-01T09:00:00Z' }),
    ],
    now,
  )
  assert.deepEqual(
    due.map((u) => u.id),
    ['past'],
  )
})

// ---------------------------------------------------------------- progress ---

test('a late message cannot drag an item backwards', () => {
  // The same hazard as the stream reducers: an upload that finished and then
  // received a stale "45%" would show as unfinished for ever.
  const done = [upload({ status: 'uploaded', percent: 100, videoId: 'abc' })]
  const after = applyUploadStatus(done, { id: 'u1', status: 'uploading', percent: 45 })
  assert.equal(after[0]!.status, 'uploaded')
  assert.equal(after[0]!.percent, 100)
})

test('failure interrupts from any state, and is not overwritten by stale progress', () => {
  const failed = applyUploadStatus([upload({ status: 'uploading', percent: 60 })], {
    id: 'u1',
    status: 'failed',
    issue: 'The daily upload limit was reached.',
  })
  assert.equal(failed[0]!.status, 'failed')
  assert.match(failed[0]!.issue!, /daily upload limit/)

  const stale = applyUploadStatus(failed, { id: 'u1', status: 'uploading', percent: 70 })
  assert.equal(stale[0]!.status, 'failed', 'only a real retry clears a failure')
})

test('finishing an upload records the video id, which is what makes publish instant', () => {
  const after = applyUploadStatus([upload({ status: 'uploading', percent: 99 })], {
    id: 'u1',
    status: 'uploaded',
    videoId: 'dQw4w9WgXcQ',
  })
  assert.equal(after[0]!.percent, 100, 'uploaded implies complete without being told')
  assert.equal(publishNowKind(after[0]!), 'instant')
})

test('a status change touches only the item it names', () => {
  const both = [upload({ id: 'a' }), upload({ id: 'b' })]
  const after = applyUploadStatus(both, { id: 'a', status: 'uploading', percent: 10 })
  assert.equal(after[1]!.status, 'scheduled')
  assert.equal(after[1]!.percent, 0)
})

test('a failed item comes back by being re-queued, which is what a retry does', () => {
  const failed = [upload({ status: 'failed', percent: 60, issue: 'Network dropped.' })]
  const requeued = applyUploadStatus(failed, { id: 'u1', status: 'scheduled', percent: 0 })
  assert.equal(requeued[0]!.status, 'scheduled')
  assert.equal(requeued[0]!.issue, undefined, 'the old reason must not linger')
  // And from there it proceeds normally.
  const going = applyUploadStatus(requeued, { id: 'u1', status: 'uploading', percent: 5 })
  assert.equal(going[0]!.status, 'uploading')
})
