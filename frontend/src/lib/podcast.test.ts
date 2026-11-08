import { describe, expect, it } from 'vitest'
import { changedLines, edited, needsTts, toPatch, type ScriptLine } from './dialogue'
import {
  byChapter,
  hostProblem,
  insertPoints,
  minutesFor,
  newHost,
  notesMarkdown,
  stamp,
  stepStates,
  structure,
  wordCount,
  writeBlockedReason,
  type Episode,
  type EpisodeSettings,
  type Host,
} from './podcast'

const line = (id: string, text: string, extra: Partial<ScriptLine> = {}): ScriptLine => ({
  id,
  speaker_id: 'h1',
  text,
  voice_id: null,
  speed: null,
  emotion: null,
  pause_after_s: null,
  para: false,
  status: 'empty',
  ...extra,
})

const SETTINGS: EpisodeSettings = {
  intro: { media_id: 'm-in', lead_s: 4, overlap_s: 3, gain_db: 0 },
  outro: { media_id: 'm-out', overlap_s: 2, gain_db: 0, max_s: 20 },
  bed: { media_id: null, gain_db: -20 },
  duck_db: 12,
  gap_s: 0.5,
  chapter_gap_s: 1,
  loudness: 'stereo',
}

const CHAPTERS = [
  { id: 'c1', title: 'Hello' },
  { id: 'c2', title: 'News' },
]

const LINES = [
  line('a', 'one two three four five', { chapter_id: 'c1' }),
  line('b', 'six seven', { chapter_id: 'c1', kind: 'recording' }),
  line('ad', 'Sponsor', { chapter_id: 'c1', kind: 'ad', media_id: 'm-ad', duration_s: 3 }),
  line('c', 'eight nine ten', { chapter_id: 'c2' }),
  line('p', '', { chapter_id: 'c2', kind: 'break', pause_after_s: 2 }),
]

const host = (over: Partial<Host> = {}): Host => ({ id: 'h1', name: 'Ali', role: 'host', persona: '', voice_id: null, me: false, ...over })

const episode = (over: Partial<Episode> = {}): Episode => ({
  id: 'e1',
  show_id: null,
  show_name: null,
  title: 'Bridge day',
  format: 'conversation',
  medium: 'audio',
  language: 'en',
  target_minutes: 5,
  step: 1,
  cover_media_id: null,
  cover_url: null,
  sources: { topic: '', text: '', docs: [], has_script: false },
  hosts: [host()],
  chapters: [],
  script_id: null,
  settings: SETTINGS,
  notes: null,
  outputs: { mix: null, publish: null, video: null },
  jobs: {},
  words: 0,
  minutes: 0,
  counts: { lines: 0, ready: 0, stale: 0, empty: 0, rendering: 0, failed: 0 },
  created_at: '',
  updated_at: '',
  ...over,
})

describe('podcast helpers', () => {
  it('groups lines by chapter in script order', () => {
    const groups = byChapter(LINES, CHAPTERS)
    expect(groups.map((g) => [g.chapter?.title, g.start, g.lines.map((l) => l.id)])).toEqual([
      ['Hello', 0, ['a', 'b', 'ad']],
      ['News', 3, ['c', 'p']],
    ])
    // a line of a deleted chapter (or none) gets a group of its own
    expect(byChapter([line('x', 'hi'), ...LINES], CHAPTERS)[0]).toMatchObject({ chapter: null, start: 0 })
  })

  it('offers the start and every chapter boundary for an insert', () => {
    expect(insertPoints(LINES, CHAPTERS)).toEqual([
      { label: 'Before the first line', after: null },
      { label: 'Between “Hello” and “News”', after: 'ad' },
    ])
  })

  it('counts the words that will be heard', () => {
    expect(wordCount(LINES)).toBe(10)
    expect(minutesFor(150)).toBe(1)
    expect(minutesFor(225)).toBe(1.5)
  })

  it('lays out the episode structure', () => {
    const blocks = structure(LINES, CHAPTERS, SETTINGS)
    expect(blocks.map((b) => b.kind)).toEqual(['intro', 'chapter', 'ad', 'chapter', 'break', 'outro'])
    expect(blocks[0].seconds).toBe(4)
    // 7 words at 2.5 a second plus a 0.5 s gap after each of the two lines
    expect(blocks[1].seconds).toBeCloseTo(5 / 2.5 + 0.5 + 2 / 2.5 + 0.5)
    expect(blocks[2]).toMatchObject({ label: 'Sponsor', seconds: 3 })
    expect(blocks[4]).toMatchObject({ label: 'Pause', seconds: 2 })
    expect(blocks[5].seconds).toBe(20)
    expect(structure(LINES, CHAPTERS, { ...SETTINGS, intro: { ...SETTINGS.intro, media_id: null } })[0].kind).toBe('chapter')
  })

  it('checks hosts and what writing needs', () => {
    expect(hostProblem([])).toBe('Add at least one host')
    expect(hostProblem([host(), host({ id: 'h2', name: ' ali ' })])).toBe('Two hosts have the same name')
    expect(hostProblem([host({ name: '' })])).toBe('Every host needs a name')
    expect(hostProblem([host(), host({ id: 'h2', name: 'Sara' })])).toBeNull()
    const second = newHost([host()])
    expect(second).toMatchObject({ role: 'cohost', name: 'Co-host 1', me: false })
    expect(writeBlockedReason(episode())).toMatch(/topic or some material/)
    expect(writeBlockedReason(episode({ sources: { topic: 'Bridges', text: '', docs: [], has_script: false } }))).toBeNull()
    expect(writeBlockedReason(episode({ sources: { topic: 'Bridges', text: '', docs: [], has_script: false }, hosts: [] }))).toBe('Add at least one host')
  })

  it('marks the steps that are done', () => {
    const fresh = stepStates(episode())
    expect(fresh).toEqual({ source: 'todo', hosts: 'done', script: 'todo', voice: 'todo', produce: 'todo', publish: 'todo' })
    const along = stepStates(
      episode({
        sources: { topic: 'x', text: '', docs: [], has_script: true },
        counts: { lines: 4, ready: 4, stale: 0, empty: 0, rendering: 0, failed: 0 },
        outputs: { mix: { media_id: 'm', generation_id: 'g', url: '/m.flac', duration_s: 60, built_at: null, stale: false, timeline: [], chapters: [] }, publish: null, video: null },
        jobs: { publish: { id: 'j', type: 'podcast_publish', status: 'running' } as Episode['jobs']['publish'] },
      }),
    )
    expect(along).toEqual({ source: 'done', hosts: 'done', script: 'done', voice: 'done', produce: 'done', publish: 'busy' })
  })

  it('formats timestamps and the notes', () => {
    expect(stamp(0)).toBe('00:00')
    expect(stamp(75.4)).toBe('01:15')
    expect(stamp(3725)).toBe('1:02:05')
    const md = notesMarkdown({ title: 'Bridge day', show_name: 'River Talk' }, { description: 'Two friends.', key_points: ['It opened'], chapters: [{ title: 'Hello', start: 0 }, { title: 'News', start: 65 }] })
    expect(md).toBe('# Bridge day\n*River Talk*\n\nTwo friends.\n\n## Chapters\n\n- 00:00 Hello\n- 01:05 News\n\n## Key points\n\n- It opened\n')
  })
})

describe('clip lines in the Dialogue model', () => {
  it('keeps recordings, breaks and clips away from TTS', () => {
    expect(needsTts(line('a', 'x'))).toBe(true)
    expect(needsTts(line('a', 'x', { kind: 'recording' }))).toBe(false)
    expect(needsTts(line('a', 'x', { kind: 'ad' }))).toBe(true)
    expect(needsTts(line('a', 'x', { kind: 'ad', media_id: 'm' }))).toBe(false)
    expect(changedLines(LINES).map((l) => l.id)).toEqual(['a', 'c'])
  })

  it('sends kind, clip and chapter with a save, and a missing kind reads as speech', () => {
    expect(toPatch(LINES[1])).toMatchObject({ id: 'b', kind: 'recording', chapter_id: 'c1' })
    expect(edited(line('a', 'x'), line('a', 'x', { kind: 'speech' }))).toBe(false)
    expect(edited(line('a', 'x'), line('a', 'x', { kind: 'recording' }))).toBe(true)
  })
})
