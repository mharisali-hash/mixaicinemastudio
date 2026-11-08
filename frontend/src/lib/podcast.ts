import { NARRATOR, needsTts, words, type ScriptCounts, type ScriptLine } from './dialogue'
import type { Job } from './types'

// contract-v20: Podcast Studio

export const WPM = 150
export const MAX_HOSTS = 4

export type Format = 'interview' | 'conversation' | 'debate' | 'solo' | 'news' | 'storytelling' | 'qa'
export type HostRole = 'host' | 'cohost' | 'guest' | 'narrator'
export type EditOp = 'rewrite_chapter' | 'shorter' | 'livelier'
export type Loudness = 'stereo' | 'mono' | 'none'
// audio or video podcast; `format` is the conversation style
export type Medium = 'audio' | 'video'

export const FORMATS: { id: Format; label: string; hint: string; hosts: number }[] = [
  { id: 'conversation', label: 'Conversation', hint: 'Hosts riff on a topic together', hosts: 2 },
  { id: 'interview', label: 'Interview', hint: 'A host asks, a guest answers', hosts: 2 },
  { id: 'solo', label: 'Solo', hint: 'One voice, straight to the listener', hosts: 1 },
  { id: 'news', label: 'News brief', hint: 'Tight segments, one story each', hosts: 2 },
  { id: 'debate', label: 'Debate', hint: 'Two sides, a moderator keeps it fair', hosts: 3 },
  { id: 'storytelling', label: 'Storytelling', hint: 'A narrated story with scenes', hosts: 1 },
  { id: 'qa', label: 'Q&A', hint: 'Listener questions, answered', hosts: 2 },
]

export const ROLE_LABEL: Record<HostRole, string> = { host: 'Host', cohost: 'Co-host', guest: 'Guest', narrator: 'Narrator' }

export const LOUDNESS: { id: Loudness; label: string }[] = [
  { id: 'stereo', label: 'Podcast stereo (−16 LUFS)' },
  { id: 'mono', label: 'Podcast mono (−19 LUFS)' },
  { id: 'none', label: 'As mixed' },
]

export const STEPS = [
  { n: 1, id: 'source', label: 'Source' },
  { n: 2, id: 'hosts', label: 'Hosts' },
  { n: 3, id: 'script', label: 'Script' },
  { n: 4, id: 'voice', label: 'Voice' },
  { n: 5, id: 'produce', label: 'Produce' },
  { n: 6, id: 'publish', label: 'Publish' },
] as const
export type StepId = (typeof STEPS)[number]['id']

export interface Host {
  id: string
  name: string
  role: HostRole
  persona: string
  voice_id: string | null
  me: boolean
  // a Character Studio character, and the look it wears on camera
  character_id?: string | null
  look_id?: string | null
}

export interface Ad {
  id: string
  name: string
  kind: 'jingle' | 'sponsor'
  media_id: string | null
  text: string | null
  host_id: string | null
}

export interface ShowMusic {
  intro_media_id: string | null
  outro_media_id: string | null
  bed_media_id: string | null
}

export interface Show {
  id: string
  name: string
  description: string
  author: string
  language: string
  cover_media_id: string | null
  cover_url: string | null
  hosts: Host[]
  music: ShowMusic
  ads: Ad[]
  episodes: number
  created_at: string
  updated_at: string
}

export interface ShowCreate {
  name: string
  language?: string
  description?: string
  author?: string
  cover_media_id?: string | null
  hosts?: Partial<Host>[]
}

export type ShowPatch = Partial<Omit<ShowCreate, 'hosts'>> & { hosts?: Partial<Host>[]; music?: ShowMusic; ads?: Partial<Ad>[] }

export interface Chapter {
  id: string
  title: string
}

export interface MusicCue {
  media_id: string | null
  gain_db: number
}

export interface EpisodeSettings {
  intro: MusicCue & { lead_s: number; overlap_s: number }
  outro: MusicCue & { overlap_s: number; max_s: number }
  bed: MusicCue
  duck_db: number
  gap_s: number
  chapter_gap_s: number
  loudness: Loudness
}

export interface TimelineItem {
  line_id: string
  kind: string
  speaker_id: string
  name: string
  text: string
  start: number
  end: number
  chapter_id: string | null
}

export interface ChapterMark {
  id: string
  title: string
  start: number
  end: number
}

export interface MixRecord {
  media_id: string | null
  generation_id: string
  url: string | null
  duration_s: number | null
  built_at: string | null
  stale: boolean
  timeline: TimelineItem[]
  chapters: ChapterMark[]
}

export interface FileRecord {
  media_id: string | null
  generation_id: string
  url: string | null
  format?: string | null
  aspect?: string | null
  burned?: boolean | null
  built_at: string | null
}

export interface Notes {
  description: string
  key_points: string[]
  chapters: { title: string; start: number }[]
}

export interface SourceDoc {
  id: string
  name: string
  type: string
  chars: number
  pages?: number | null
  truncated?: boolean
}

export interface Episode {
  id: string
  show_id: string | null
  show_name: string | null
  title: string
  format: Format
  medium: Medium
  language: string
  target_minutes: number
  step: number
  cover_media_id: string | null
  cover_url: string | null
  sources: { topic: string; text: string; docs: SourceDoc[]; has_script: boolean }
  hosts: Host[]
  chapters: Chapter[]
  script_id: string | null
  settings: EpisodeSettings
  notes: Notes | null
  outputs: { mix: MixRecord | null; publish: FileRecord | null; video: FileRecord | null }
  jobs: Partial<Record<'write' | 'mix' | 'publish' | 'video', Job>>
  words: number
  minutes: number
  counts: ScriptCounts
  created_at: string
  updated_at: string
}

export interface EpisodeSummary {
  id: string
  show_id: string | null
  title: string
  format: Format
  medium: Medium
  language: string
  target_minutes: number
  step: number
  minutes: number
  has_mix: boolean
  published: boolean
  created_at: string
  updated_at: string
}

export interface EpisodeCreate {
  title?: string
  show_id?: string | null
  format?: Format
  medium?: Medium
  language?: string
  target_minutes?: number
  hosts?: Partial<Host>[]
  topic?: string
}

export interface EpisodePatch {
  title?: string
  show_id?: string | null
  format?: Format
  medium?: Medium
  language?: string
  target_minutes?: number
  step?: number
  cover_media_id?: string | null
  sources?: { topic?: string; text?: string }
  hosts?: Partial<Host>[]
  chapters?: Partial<Chapter>[]
  settings?: EpisodeSettings
  notes?: Notes | null
}

export interface InsertBody {
  kind: 'ad' | 'music' | 'break'
  after_line_id: string | null
  ad_id?: string
  media_id?: string
  seconds?: number
  speaker_id?: string
  text?: string
}

export interface PublishBody {
  format: 'mp3' | 'm4a'
  cover_media_id?: string | null
  notes?: boolean
}

export interface VideoBody {
  aspect: '16:9' | '1:1'
  burn_subtitles: boolean
  cover_media_id?: string | null
}

// ---------------------------------------------------------------- helpers

const rid = (prefix: string) => `${prefix}${Math.random().toString(16).slice(2, 12).padEnd(10, '0')}`

export function newHost(existing: Host[], over: Partial<Host> = {}): Host {
  const n = existing.length
  const role: HostRole = n === 0 ? 'host' : 'cohost'
  return { id: rid('h_'), name: n === 0 ? 'Host' : `Co-host ${n}`, role, persona: '', voice_id: null, me: false, ...over }
}

export const busy = (j: Job | null | undefined) => j?.status === 'queued' || j?.status === 'running'

export const minutesFor = (count: number) => Math.round((count / WPM) * 10) / 10

/** Words that will be heard: speech, recordings and sponsor reads. */
export const wordCount = (lines: ScriptLine[]) => lines.filter((l) => l.kind !== 'break' && l.kind !== 'music' && (needsTts(l) || l.kind === 'recording')).reduce((n, l) => n + words(l.text), 0)

export function hostProblem(hosts: Host[]): string | null {
  if (hosts.length === 0) return 'Add at least one host'
  if (hosts.length > MAX_HOSTS) return `Up to ${MAX_HOSTS} hosts`
  const names = hosts.map((h) => h.name.trim().toLowerCase())
  if (names.some((n) => !n)) return 'Every host needs a name'
  if (new Set(names).size !== names.length) return 'Two hosts have the same name'
  return null
}

export function sourceReady(ep: Episode): boolean {
  return !!(ep.sources.topic.trim() || ep.sources.text.trim() || ep.sources.docs.length || ep.sources.has_script)
}

export function writeBlockedReason(ep: Episode): string | null {
  if (!ep.sources.topic.trim() && !ep.sources.text.trim() && ep.sources.docs.length === 0) return 'Give the episode a topic or some material in Source'
  return hostProblem(ep.hosts)
}

export type StepState = 'done' | 'todo' | 'busy'

export function stepStates(ep: Episode): Record<StepId, StepState> {
  const c = ep.counts
  const playable = c.lines - c.empty - c.stale - c.failed - c.rendering
  const mix = ep.outputs.mix
  return {
    source: sourceReady(ep) ? 'done' : 'todo',
    hosts: hostProblem(ep.hosts) ? 'todo' : 'done',
    script: busy(ep.jobs.write) ? 'busy' : c.lines > 0 ? 'done' : 'todo',
    voice: c.rendering ? 'busy' : c.lines > 0 && playable >= c.lines ? 'done' : 'todo',
    produce: busy(ep.jobs.mix) ? 'busy' : mix && !mix.stale ? 'done' : 'todo',
    publish: busy(ep.jobs.publish) || busy(ep.jobs.video) ? 'busy' : ep.outputs.publish ? 'done' : 'todo',
  }
}

export interface ChapterGroup {
  chapter: Chapter | null
  // index of the group's first line in the whole script
  start: number
  lines: ScriptLine[]
}

/** The script cut into its chapters, in script order; lines before any chapter (or of a deleted one) sit
 * in a group without a chapter. */
export function byChapter(lines: ScriptLine[], chapters: Chapter[]): ChapterGroup[] {
  const known = new Map(chapters.map((c) => [c.id, c]))
  const out: ChapterGroup[] = []
  lines.forEach((l, i) => {
    const ch = (l.chapter_id && known.get(l.chapter_id)) || null
    const last = out[out.length - 1]
    if (last && (last.chapter?.id ?? null) === (ch?.id ?? null)) last.lines.push(l)
    else out.push({ chapter: ch, start: i, lines: [l] })
  })
  return out
}

/** Where an ad can go: the very start, and after the last line of every chapter but the last. */
export function insertPoints(lines: ScriptLine[], chapters: Chapter[]): { label: string; after: string | null }[] {
  const groups = byChapter(lines, chapters)
  const out: { label: string; after: string | null }[] = [{ label: 'Before the first line', after: null }]
  groups.forEach((g, i) => {
    if (i === groups.length - 1) return
    const next = groups[i + 1]
    out.push({ label: `Between “${g.chapter?.title ?? 'Opening'}” and “${next.chapter?.title ?? 'the rest'}”`, after: g.lines[g.lines.length - 1].id })
  })
  return out
}

const lineSeconds = (l: ScriptLine) => (l.duration_s ? l.duration_s : l.kind === 'break' ? (l.pause_after_s ?? 1) : words(l.text) / 2.5)

export interface Block {
  kind: 'intro' | 'chapter' | 'ad' | 'music' | 'break' | 'outro'
  label: string
  seconds: number
}

/** The episode's shape for the Produce timeline: intro, chapters with their inserts, outro. Real take
 * lengths where there are takes, an estimate elsewhere. */
export function structure(lines: ScriptLine[], chapters: Chapter[], st: EpisodeSettings, musicLength: { intro?: number; outro?: number } = {}): Block[] {
  const out: Block[] = []
  if (st.intro.media_id) out.push({ kind: 'intro', label: 'Intro', seconds: st.intro.lead_s })
  for (const g of byChapter(lines, chapters)) {
    let speech = 0
    const flush = () => {
      if (speech > 0) out.push({ kind: 'chapter', label: g.chapter?.title ?? 'Opening', seconds: speech })
      speech = 0
    }
    for (const l of g.lines) {
      if (l.kind === 'ad' || l.kind === 'music' || l.kind === 'break') {
        flush()
        out.push({ kind: l.kind, label: l.kind === 'break' ? 'Pause' : l.text || (l.kind === 'ad' ? 'Ad' : 'Music'), seconds: lineSeconds(l) })
      } else {
        speech += lineSeconds(l) + (l.pause_after_s ?? st.gap_s)
      }
    }
    flush()
  }
  if (st.outro.media_id) out.push({ kind: 'outro', label: 'Outro', seconds: Math.min(musicLength.outro ?? st.outro.max_s, st.outro.max_s) })
  return out
}

export const totalSeconds = (blocks: Block[]) => blocks.reduce((n, b) => n + b.seconds, 0)

export function stamp(t: number) {
  const s = Math.max(0, Math.round(t))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}` : `${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
}

/** Lines the user has to record themselves: their host is "me", or the line was turned into a recording. */
export const recordingLines = (lines: ScriptLine[]) => lines.filter((l) => l.kind === 'recording')

export const isNarrator = (id: string) => id === NARRATOR

export function notesMarkdown(ep: Pick<Episode, 'title' | 'show_name'>, notes: Notes): string {
  const out = [`# ${ep.title}`]
  if (ep.show_name) out.push(`*${ep.show_name}*`)
  if (notes.description) out.push('', notes.description)
  if (notes.chapters.length) out.push('', '## Chapters', '', ...notes.chapters.map((c) => `- ${stamp(c.start)} ${c.title}`))
  if (notes.key_points.length) out.push('', '## Key points', '', ...notes.key_points.map((k) => `- ${k}`))
  return `${out.join('\n')}\n`
}
