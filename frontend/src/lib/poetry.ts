import type { CaptionStyle } from '@/components/generate/CaptionStylePicker'
import { request } from './api'
import type { Generation, Job } from './types'

// contract-v33-poetry-short.md

export type PoetryStageKey = 'poem' | 'song' | 'timing' | 'scenes' | 'render'
export type PoetryStageStatus = 'pending' | 'running' | 'review' | 'done' | 'skipped' | 'failed'
export type PoetryAspect = '9:16' | '16:9' | '1:1'
export type PoetrySceneStatus = 'pending' | 'keyframe' | 'clip' | 'done' | 'failed'

export interface PoetrySettings {
  topic: string
  mood: string
  style: string
  // boy / girl / choir only when the server can change a sung voice
  vocal: PoetryVocal
  // vocal 'voice': the Library voice that sings it
  sing_voice_id?: string | null
  // vocal 'lead_group': who leads, how many children sing with them
  lead_voice?: 'boy' | 'girl' | 'voice'
  group_size?: 3 | 5
  length_s: number
  character_id: string | null
  // one of that character's Looks; null draws from the character sheet
  look_id?: string | null
  character: string
  setting: string
  aspect: PoetryAspect
  quality: 'draft' | 'final'
  image_model: string
  title_card: boolean
  // how the verses appear in the video
  caption_style?: CaptionStyle
  // a Library picture or short clip before / after the song, no captions on it
  intro_media_id?: string | null
  outro_media_id?: string | null
  review: boolean
  whisper: boolean
}

export interface PoetryLine {
  i: number
  text: string
  section: string
  part?: string | null
  start: number | null
  end: number | null
}

export interface PoetryScene {
  id: string
  n: number
  first: number
  last: number
  start: number | null
  end: number | null
  image_prompt: string
  motion_prompt: string
  status: PoetrySceneStatus
  error: string | null
  keyframe: Generation | null
  clip: Generation | null
}

export interface PoetryOutputs {
  media_id: string
  generation_id: string | null
  duration_s: number | null
  width: number | null
  height: number | null
  chapters: { title: string; start: number; end: number }[] | null
  video_url: string | null
  preview_url: string | null
  srt_url: string | null
  stale: boolean
  built_at?: string | null
}

export interface PoetryShort {
  id: string
  title: string
  language: string
  stage: PoetryStageKey | 'done'
  status: 'running' | 'review' | 'failed' | 'done' | 'idle'
  review: PoetryStageKey | null
  stages: { key: PoetryStageKey; label: string; status: PoetryStageStatus; detail: string | null; started_at?: string | null; finished_at?: string | null }[]
  settings: PoetrySettings
  poem: { lyrics: string; tags: string; own: boolean }
  lines: PoetryLine[]
  song: { media_id: string | null; duration_s: number | null; generation: Generation | null }
  timing: { source: 'whisper' | 'phrases' | 'even' | null; edited: boolean; phrases: [number, number][]; separated?: boolean | null }
  scenes: PoetryScene[]
  outputs: PoetryOutputs | null
  job: Job | null
  created_at: string
  updated_at: string
}

export interface PoetrySummary {
  id: string
  title: string
  language: string
  stage: string
  status: PoetryShort['status']
  review: PoetryStageKey | null
  aspect: PoetryAspect
  lines: number
  duration_s: number | null
  thumb_url: string | null
  media_id: string | null
  updated_at: string
}

export type PoetryCreate = Partial<PoetrySettings> & { title?: string; poem?: string; language?: string }
export interface Timing {
  start: number
  end: number
}

export const poetryApi = {
  list: () => request<PoetrySummary[]>('GET', '/poetry'),
  get: (id: string) => request<PoetryShort>('GET', `/poetry/${id}`),
  create: (body: PoetryCreate) => request<PoetryShort>('POST', '/poetry', body),
  remove: (id: string) => request<void>('DELETE', `/poetry/${id}`),
  patchPoem: (id: string, body: { lyrics: string; title?: string }) => request<PoetryShort>('PATCH', `/poetry/${id}/poem`, body),
  patchTimings: (id: string, lines: Timing[]) => request<PoetryShort>('PATCH', `/poetry/${id}/timings`, { lines }),
  patchScenes: (id: string, scenes: { id: string; image_prompt?: string; motion_prompt?: string }[]) =>
    request<PoetryShort>('PATCH', `/poetry/${id}/scenes`, { scenes }),
  continue: (id: string) => request<Job>('POST', `/poetry/${id}/continue`, {}),
  regenerateScene: (id: string, sceneId: string, body: { what: 'keyframe' | 'clip'; image_prompt?: string; motion_prompt?: string }) =>
    request<PoetryShort>('POST', `/poetry/${id}/scenes/${sceneId}/regenerate`, body),
  regenerateSong: (id: string) => request<Job>('POST', `/poetry/${id}/song/regenerate`, {}),
  render: (id: string) => request<Job>('POST', `/poetry/${id}/render`, {}),
}

export const POETRY_STEPS: { id: PoetryStageKey; label: string }[] = [
  { id: 'poem', label: 'Poem' },
  { id: 'song', label: 'Song' },
  { id: 'timing', label: 'Timing' },
  { id: 'scenes', label: 'Scenes' },
  { id: 'render', label: 'Render' },
]

export const POETRY_LANGUAGES = [
  { value: 'ur', label: 'Urdu' },
  { value: 'en', label: 'English' },
  { value: 'hi', label: 'Hindi' },
  { value: 'pa', label: 'Punjabi' },
  { value: 'pnb', label: 'Punjabi (Shahmukhi)' },
  { value: 'ar', label: 'Arabic' },
  { value: 'fa', label: 'Persian' },
]

export const IMAGE_MODELS = [
  { value: 'krea2_turbo', label: 'Krea 2' },
  { value: 'qwen_image_2512', label: 'Qwen-Image 2512' },
  { value: 'zimage_turbo', label: 'Z-Image Turbo' },
]

export type StepState = 'done' | 'todo' | 'busy' | 'review' | 'failed'

export const stageOf = (ps: PoetryShort, key: PoetryStageKey) => ps.stages.find((s) => s.key === key)

export function stepStates(ps: PoetryShort): Record<PoetryStageKey, StepState> {
  const out = {} as Record<PoetryStageKey, StepState>
  for (const s of POETRY_STEPS) {
    const st = stageOf(ps, s.id)?.status ?? 'pending'
    out[s.id] = st === 'running' ? 'busy' : st === 'done' || st === 'skipped' ? 'done' : st === 'review' ? 'review' : st === 'failed' ? 'failed' : 'todo'
  }
  return out
}

/** The first earlier step that isn't finished: what this one is waiting on. */
export function blockingStep(ps: PoetryShort, index: number) {
  for (let i = 0; i < index; i++) {
    const status = stageOf(ps, POETRY_STEPS[i].id)?.status ?? 'pending'
    if (status !== 'done' && status !== 'skipped') return { step: POETRY_STEPS[i], index: i, status }
  }
  return null
}

export function initialStep(ps: PoetryShort): number {
  const key = ps.review ?? (ps.stage === 'done' ? 'render' : ps.stage)
  const i = POETRY_STEPS.findIndex((s) => s.id === key)
  return i < 0 ? 1 : i + 1
}

export const jobBusy = (ps: PoetryShort) => ps.status === 'running' || ps.job?.status === 'queued' || ps.job?.status === 'running'

export const making = (g: Generation | null | undefined) => g?.status === 'queued' || g?.status === 'generating'

export function poetryBusy(ps: PoetryShort | undefined) {
  if (!ps) return false
  return jobBusy(ps) || ps.scenes.some((s) => s.status === 'keyframe' || s.status === 'clip') || making(ps.song.generation)
}

/** 65.34 → "1:05.3" */
export function clock(s: number | null | undefined) {
  if (s == null || !Number.isFinite(s)) return '–'
  const t = Math.max(0, Math.round(s * 10) / 10)
  const m = Math.floor(t / 60)
  return `${m}:${(t - m * 60).toFixed(1).padStart(4, '0')}`
}

const round = (v: number) => Math.round(v * 1000) / 1000

/** Move one edge of a line, keeping it inside its neighbours and at least 0.1 s long. */
export function nudge(rows: Timing[], i: number, edge: 'start' | 'end', by: number, duration: number): Timing[] {
  const out = rows.map((r) => ({ ...r }))
  const r = out[i]
  if (edge === 'start') {
    const lo = i > 0 ? out[i - 1].end : 0
    r.start = round(Math.min(Math.max(lo, r.start + by), r.end - 0.1))
  } else {
    const hi = i + 1 < out.length ? out[i + 1].start : duration
    r.end = round(Math.max(Math.min(hi, r.end + by), r.start + 0.1))
  }
  return out
}

/** Tap to time: line i starts at `at`; the line before ends there (it never runs into this one). */
export function tap(rows: Timing[], i: number, at: number): Timing[] {
  const out = rows.map((r) => ({ ...r }))
  const t = round(Math.max(i > 0 ? out[i - 1].start + 0.1 : 0, at))
  out[i].start = t
  if (out[i].end <= t) out[i].end = round(i + 1 < out.length ? Math.max(t + 0.1, Math.min(out[i + 1].start, t + 2)) : t + 2)
  if (i > 0) out[i - 1].end = t
  // the lines after a late tap get pushed along instead of overlapping
  for (let k = i + 1; k < out.length && out[k].start < out[k - 1].end; k++) {
    const len = out[k].end - out[k].start
    out[k].start = out[k - 1].end
    out[k].end = round(out[k].start + Math.max(0.1, len))
  }
  return out
}

/** The first problem with a timing list, in the server's words, or null. */
export function timingProblem(rows: Timing[], duration: number | null): string | null {
  for (let i = 0; i < rows.length; i++) {
    const r = rows[i]
    if (!(r.end > r.start)) return `Line ${i + 1} ends before it starts`
    if (i > 0 && r.start < rows[i - 1].end - 0.001) return `Line ${i + 1} starts before line ${i} ends`
    if (duration && r.end > duration + 0.05) return `Line ${i + 1} ends after the song (${duration.toFixed(1)} s)`
  }
  return null
}

export const SECTION_LABEL: Record<string, string> = { verse: 'Verse', chorus: 'Chorus', bridge: 'Bridge', intro: 'Intro', outro: 'Outro' }
export const sectionLabel = (s: string) => SECTION_LABEL[s] ?? s.charAt(0).toUpperCase() + s.slice(1)

export type PoetryVocal = 'female' | 'male' | 'boy' | 'girl' | 'choir' | 'voice' | 'lead_group'
export const CHILD_VOCALS: { value: PoetryVocal; label: string }[] = [
  { value: 'boy', label: 'Boy (child)' },
  { value: 'girl', label: 'Girl (child)' },
  { value: 'choir', label: "Children's choir" },
  { value: 'voice', label: 'A voice of mine' },
  { value: 'lead_group', label: "Lead child + children's group" },
]

// past 90 s it reads better in minutes: a 4-minute Episode, not 240 s
export const lengthLabel = (s: number) => {
  const r = Math.round(s)
  return r <= 90 ? `${r} s` : `${Math.floor(r / 60)} min${r % 60 ? ` ${r % 60} s` : ''}`
}

// lead_group and 'voice' only mean something with the extra fields, so they're spelled out
export function voiceSummary(st: Pick<PoetrySettings, 'vocal' | 'lead_voice' | 'group_size'>, voiceName?: string) {
  if (st.vocal === 'female' || st.vocal === 'male') return `${st.vocal === 'female' ? 'Female' : 'Male'} voice`
  if (st.vocal === 'voice') return voiceName ? `Sung as ${voiceName}` : 'A voice of mine'
  if (st.vocal === 'lead_group') {
    const lead = st.lead_voice === 'voice' ? (voiceName ?? 'my voice') : st.lead_voice === 'boy' ? 'boy' : 'girl'
    return `Lead ${lead} + ${st.group_size ?? 3} children`
  }
  return CHILD_VOCALS.find((v) => v.value === st.vocal)?.label ?? st.vocal
}

export const bookendSummary = (st: Pick<PoetrySettings, 'intro_media_id' | 'outro_media_id'>) =>
  st.intro_media_id && st.outro_media_id ? 'intro + outro' : st.intro_media_id ? 'intro' : st.outro_media_id ? 'outro' : ''
