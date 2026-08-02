/**
 * Scheduled uploads — the model, kept pure.
 *
 * Publishing is a different act from streaming. A stream pushes bytes at an
 * RTMP endpoint and nobody's permission is involved; an upload puts a title,
 * a description and your name on something, on a schedule, unattended. What
 * that demands is not cleverness but a queue that cannot lose an item, and
 * validation that fails here rather than eight minutes into an upload.
 *
 * Everything in this file is a pure function over plain data, so the rules can
 * be tested without a network, a clock, or an account.
 */

/**
 * Where an item is in its life.
 *
 * `uploaded` is not `published`: a scheduled video sits on YouTube as private
 * until its `publishAt`, which is the whole reason scheduling is delegated to
 * YouTube rather than fired from here. The distinction is the difference
 * between "the bytes are safe" and "the world can see it".
 */
export type UploadStatus =
  | 'draft'
  | 'scheduled'
  | 'uploading'
  | 'uploaded'
  | 'published'
  | 'failed'

export type Metadata = {
  title: string
  description: string
  tags: string[]
  /** YouTube's numeric category, e.g. "24" for Entertainment. */
  categoryId: string
  /** BCP-47, used for both the video and its audio track. */
  language: string
  playlistId: string | null
  /** Required by YouTube on every upload; there is no "unset". */
  madeForKids: boolean
}

export type ScheduledUpload = {
  id: string
  /** The account it publishes to, matching a `Destination`. */
  destinationId: string
  file: string
  name: string
  meta: Metadata
  thumbnailPath: string | null
  /**
   * ISO 8601, or null to go public as soon as the upload finishes.
   *
   * Handed to YouTube as `publishAt`, so the Mac does not need to be awake at
   * the appointed moment. An in-app timer would be strictly worse: a laptop
   * asleep at 03:00 would miss the slot entirely.
   */
  publishAt: string | null
  status: UploadStatus
  percent: number
  /** Set once YouTube has the file, which is what makes Publish now instant. */
  videoId: string | null
  issue?: string
}

/** Defaults for one account, applied to every video added to it. */
export type Template = {
  destinationId: string
  titlePattern: string
  description: string
  tags: string[]
  categoryId: string
  language: string
  playlistId: string | null
  madeForKids: boolean
}

// ----------------------------------------------------------------- limits ---

export const TITLE_MAX = 100
/**
 * Bytes, not characters — YouTube counts UTF-8. A Spanish description of 4,900
 * characters with accents is already over, and finding that out from a 400 at
 * the end of a multi-gigabyte upload is the worst possible moment.
 */
export const DESCRIPTION_MAX_BYTES = 5_000
/** The combined length of every tag, not each one. */
export const TAGS_MAX_CHARS = 500

export function byteLength(s: string): number {
  return new TextEncoder().encode(s).length
}

/**
 * Everything YouTube would reject, in the order a person would fix it.
 *
 * Checked locally so an upload fails before it starts rather than after the
 * bytes are sent. The limits are the API's, not ours.
 */
export function metadataProblems(m: Metadata): string[] {
  const problems: string[] = []

  if (!m.title.trim()) problems.push('The title is empty.')
  else if (m.title.length > TITLE_MAX) {
    problems.push(`The title is ${m.title.length} characters; the limit is ${TITLE_MAX}.`)
  }

  const bytes = byteLength(m.description)
  if (bytes > DESCRIPTION_MAX_BYTES) {
    // Report both when they differ, or "4,912 characters is over 5,000" reads
    // like a bug rather than an explanation.
    const chars = [...m.description].length
    problems.push(
      bytes === chars
        ? `The description is ${bytes} characters; the limit is ${DESCRIPTION_MAX_BYTES}.`
        : `The description is ${bytes} bytes; the limit is ${DESCRIPTION_MAX_BYTES}. ` +
          `It is ${chars} characters — accents and emoji take more than one byte each.`,
    )
  }

  const tagChars = m.tags.join('').length
  if (tagChars > TAGS_MAX_CHARS) {
    problems.push(`The tags total ${tagChars} characters; the limit is ${TAGS_MAX_CHARS} across all of them.`)
  }

  if (!m.categoryId) problems.push('No category is set.')
  if (!m.language) problems.push('No language is set.')
  return problems
}

// --------------------------------------------------------------- templates ---

/** What a title pattern can refer to. */
export type PatternContext = {
  /** The file's name, without its extension. */
  name: string
  /** 1-based position of this video in the batch being added. */
  index: number
  /** Supplied rather than read from the clock, so this stays testable. */
  date: Date
}

const PAD = (n: number) => String(n).padStart(2, '0')

/**
 * Fill in a title pattern.
 *
 * Unknown placeholders are left alone rather than blanked. Someone writing
 * "50% {name}" or "Set {a}" means those characters literally, and silently
 * deleting part of a title is worse than printing it.
 */
export function renderPattern(pattern: string, ctx: PatternContext): string {
  return pattern.replace(/\{(\w+)\}/g, (whole, key: string) => {
    switch (key) {
      case 'name':
        return ctx.name
      case 'n':
        return String(ctx.index)
      case 'nn':
        return PAD(ctx.index)
      case 'date':
        return `${ctx.date.getFullYear()}-${PAD(ctx.date.getMonth() + 1)}-${PAD(ctx.date.getDate())}`
      case 'time':
        return `${PAD(ctx.date.getHours())}:${PAD(ctx.date.getMinutes())}`
      default:
        return whole
    }
  })
}

/** The file's name without directory or extension, for `{name}`. */
export function baseName(path: string): string {
  const file = path.split('/').pop() ?? path
  const dot = file.lastIndexOf('.')
  return dot > 0 ? file.slice(0, dot) : file
}

export function applyTemplate(t: Template, ctx: PatternContext): Metadata {
  return {
    title: renderPattern(t.titlePattern, ctx),
    description: t.description,
    tags: [...t.tags],
    categoryId: t.categoryId,
    language: t.language,
    playlistId: t.playlistId,
    madeForKids: t.madeForKids,
  }
}

// ------------------------------------------------------------------- queue ---

/**
 * Why an item cannot be scheduled yet, or null when it can.
 *
 * A message rather than a boolean, so the reason can be shown instead of a
 * button merely being dead.
 */
export function scheduleBlocker(u: ScheduledUpload): string | null {
  if (!u.file) return 'No video file is chosen.'
  const problems = metadataProblems(u.meta)
  if (problems.length > 0) return problems[0]!
  return null
}

/**
 * Whether an item may be published immediately, and what that would mean.
 *
 * Two different actions wear the same label, and the difference is minutes:
 * something already on YouTube goes public with one small call, while
 * something not yet uploaded has to be sent first. A button that says "now"
 * and then spends eight minutes uploading has lied, so the caller is told
 * which it is.
 */
export function publishNowKind(u: ScheduledUpload): 'instant' | 'upload-first' | null {
  if (u.status === 'published' || u.status === 'uploading') return null
  if (u.videoId && u.status === 'uploaded') return 'instant'
  if (scheduleBlocker(u)) return null
  return 'upload-first'
}

/**
 * Rough seconds to send `bytes` with `freeKbps` of headroom.
 *
 * Fed by the bandwidth meter, so the estimate accounts for whatever the live
 * streams are already using. Null when there is no headroom to divide by —
 * "∞ minutes" is not an estimate worth showing.
 */
export function uploadSeconds(bytes: number, freeKbps: number): number | null {
  if (freeKbps <= 0) return null
  return Math.ceil((bytes * 8) / (freeKbps * 1000))
}

/**
 * The queue, in the order it should be worked through.
 *
 * There is no "start uploading at" time and there should not be: YouTube holds
 * a private video until its `publishAt`, so the file can go up the moment it is
 * ready and still appear exactly on time. Waiting until the last minute would
 * only add a way to miss the slot.
 *
 * Soonest publication first, and items with no scheduled time last — those are
 * going public the instant they land, so anything with a deadline outranks them.
 * The caller takes the head and nothing else: two uploads at once would compete
 * with each other and with every live stream for the same uplink.
 */
export function uploadQueue(uploads: ScheduledUpload[]): ScheduledUpload[] {
  return uploads
    .filter((u) => u.status === 'scheduled' && !scheduleBlocker(u))
    .sort((a, b) => {
      if (a.publishAt === b.publishAt) return 0
      if (a.publishAt === null) return 1
      if (b.publishAt === null) return -1
      return a.publishAt.localeCompare(b.publishAt)
    })
}

/** Items already on YouTube whose publication time has arrived or passed. */
export function overduePublications(uploads: ScheduledUpload[], now: Date): ScheduledUpload[] {
  return uploads.filter(
    (u) => u.status === 'uploaded' && u.publishAt !== null && new Date(u.publishAt) <= now,
  )
}

/**
 * Fold a backend status change into the queue.
 *
 * Modelled on the stream reducers, and for the same reason: a late progress
 * message must not drag an item back out of a state the backend has already
 * moved it past. An upload that finished and then received a stale "45%"
 * would show as unfinished for ever.
 */
export function applyUploadStatus(
  uploads: ScheduledUpload[],
  change: { id: string; status: UploadStatus; percent?: number; videoId?: string; issue?: string },
): ScheduledUpload[] {
  const ORDER: UploadStatus[] = ['draft', 'scheduled', 'uploading', 'uploaded', 'published']
  return uploads.map((u) => {
    if (u.id !== change.id) return u

    // A failed item leaves that state only by being put back in the queue.
    // Nothing else distinguishes a stale message from a real retry — both
    // arrive as "uploading" — so requiring the trip through `scheduled` is
    // what stops a dead item from appearing to resume on its own.
    if (u.status === 'failed' && change.status !== 'scheduled') return u

    if (change.status !== 'failed') {
      // Otherwise progress only ever moves forward.
      const from = ORDER.indexOf(u.status)
      const to = ORDER.indexOf(change.status)
      if (to >= 0 && from > to) return u
    }

    return {
      ...u,
      status: change.status,
      percent: change.percent ?? (change.status === 'uploaded' ? 100 : u.percent),
      videoId: change.videoId ?? u.videoId,
      issue: change.issue,
    }
  })
}
