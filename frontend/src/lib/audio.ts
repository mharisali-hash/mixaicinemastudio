import { isArabicScriptLang } from './aerab'
import { formatDuration } from './duration'
import { AUTO_ID, has } from './models'
import type { AudioCategory, AudioGenerateRequest, AudioKind, AudioVocal, MediaBatch, ModelInfo, Template } from './types'

// contract-v14
export const LYRICS_MAX = 6000
export const BPM_MIN = 40
export const BPM_MAX = 220
export const MAX_TAKES = 4

export const AUDIO_ROUTE: Record<AudioKind, string> = { song: '/audio/song', music: '/audio/music', sfx: '/audio/sfx' }

export const KIND_NOUN: Record<AudioKind, { one: string; many: string }> = {
  song: { one: 'song', many: 'songs' },
  music: { one: 'track', many: 'tracks' },
  sfx: { one: 'sound', many: 'sounds' },
}

export const LYRIC_SECTIONS = ['verse', 'chorus', 'bridge', 'intro', 'outro', 'instrumental'] as const
export type LyricSection = (typeof LYRIC_SECTIONS)[number]

export const VOCALS: { value: AudioVocal; label: string }[] = [
  { value: 'male', label: 'Male' },
  { value: 'female', label: 'Female' },
  { value: 'duet', label: 'Duet' },
  { value: 'none', label: 'None' },
]

export const SONG_LANGUAGES = [
  { value: 'en', label: 'English' },
  { value: 'ur', label: 'Urdu' },
  { value: 'hi', label: 'Hindi' },
  { value: 'pa', label: 'Punjabi' },
  { value: 'ar', label: 'Arabic' },
  { value: 'fa', label: 'Persian' },
  { value: 'es', label: 'Spanish' },
  { value: 'fr', label: 'French' },
  { value: 'zh', label: 'Chinese' },
  { value: 'ja', label: 'Japanese' },
  { value: 'ko', label: 'Korean' },
] as const

// the lyrics writer also knows Roman Urdu; the song itself is still sung in Urdu
export const LYRICS_LANGUAGES = [
  { value: 'en', label: 'English' },
  { value: 'ur', label: 'Urdu (script)' },
  { value: 'ur-latn', label: 'Roman Urdu' },
  { value: 'hi', label: 'Hindi' },
  { value: 'pa', label: 'Punjabi' },
  { value: 'ar', label: 'Arabic' },
  { value: 'fa', label: 'Persian' },
] as const

export const songLanguageOf = (lyricsLanguage: string) => (lyricsLanguage === 'ur-latn' ? 'ur' : lyricsLanguage)

export const MUSIC_CATEGORIES: { value: AudioCategory; label: string }[] = [
  { value: 'music', label: 'Music' },
  { value: 'instrument', label: 'Instrument' },
  { value: 'loop', label: 'Loop' },
]

export const SFX_PRESETS = [
  'Rain on window',
  'City traffic',
  'Crowd murmur',
  'Footsteps on gravel',
  'Door creak',
  'Whoosh',
  'Thunder',
  'Room tone',
  'Birdsong at dawn',
  'Fire crackle',
]

const PRESETS: Record<AudioKind, { value: number; label: string }[]> = {
  song: [
    { value: 30, label: '30 s' },
    { value: 60, label: '1 min' },
    { value: 120, label: '2 min' },
    { value: 180, label: '3 min' },
    { value: 240, label: '4 min' },
  ],
  music: [
    { value: 10, label: '10 s' },
    { value: 30, label: '30 s' },
    { value: 60, label: '1 min' },
    { value: 120, label: '2 min' },
    { value: 180, label: '3 min' },
    { value: 360, label: '6 min' },
  ],
  sfx: [
    { value: 2, label: '2 s' },
    { value: 5, label: '5 s' },
    { value: 10, label: '10 s' },
    { value: 20, label: '20 s' },
    { value: 30, label: '30 s' },
  ],
}

// used until the catalog says otherwise; SFX stays short whatever the model allows
const KIND_MAX: Record<AudioKind, number> = { song: 240, music: 120, sfx: 30 }
const HARD_MAX = 380

export function maxAudioDuration(kind: AudioKind, m: ModelInfo | undefined) {
  const cap = Math.floor(m?.max_duration_s ?? KIND_MAX[kind])
  return Math.max(1, Math.min(cap, kind === 'sfx' ? KIND_MAX.sfx : HARD_MAX))
}

/** Presets that fit the model; a cap that isn't a preset is offered as the last one. */
export function audioDurationPresets(kind: AudioKind, m: ModelInfo | undefined) {
  const max = maxAudioDuration(kind, m)
  const all = PRESETS[kind]
  const fit = all.filter((p) => p.value <= max)
  return fit.some((p) => p.value === max) || max > all[all.length - 1].value ? fit : [...fit, { value: max, label: formatDuration(max) }]
}

export const clampAudioDuration = (s: number, kind: AudioKind, m: ModelInfo | undefined) => Math.min(maxAudioDuration(kind, m), Math.max(1, Math.round(s)))

export interface AudioForm {
  kind: AudioKind
  prompt: string
  model?: string
  durationS: number
  count: number
  seed: string
  steps: string
  bpm: string
  // song only
  lyrics: string
  vocal: AudioVocal | null
  language: string
  timbreId: string | null
  aerab: boolean
  // ACE-Step: '' leaves the choice to the model
  keyscale: string
  timeSignature: '' | '2' | '3' | '4' | '6'
  guidance: string
  think: boolean
  // music only
  category: AudioCategory | null
}

const BASE: Omit<AudioForm, 'kind' | 'durationS'> = {
  prompt: '',
  count: 1,
  seed: '',
  steps: '',
  bpm: '',
  lyrics: '',
  vocal: null,
  language: 'en',
  timbreId: null,
  aerab: true,
  keyscale: '',
  timeSignature: '',
  guidance: '',
  think: true,
  category: null,
}

export const DEFAULT_AUDIO_FORM: Record<AudioKind, AudioForm> = {
  song: { ...BASE, kind: 'song', durationS: 120 },
  music: { ...BASE, kind: 'music', durationS: 30 },
  sfx: { ...BASE, kind: 'sfx', durationS: 5 },
}

export const instrumental = (f: Pick<AudioForm, 'vocal'>) => f.vocal === 'none'

const int = (s: string) => {
  const n = Number.parseInt(s, 10)
  return Number.isFinite(n) ? n : null
}

/** A model fits a page when it says it makes that kind of sound. Auto stands in for the page's default. */
export const modelFits = (m: ModelInfo, kind: AudioKind) => m.id === AUTO_ID || has(m, kind)

/** Why this can't be made yet, in words someone can act on; null when it can. */
export function audioBlockedReason(f: AudioForm, m: ModelInfo | undefined): string | null {
  if (!m) return `No ${KIND_NOUN[f.kind].one} model is available on this server yet.`
  if (!modelFits(m, f.kind)) return `${m.label} doesn't make ${KIND_NOUN[f.kind].many}. Pick another model.`
  if (f.prompt.trim().length < 3) return f.kind === 'song' ? 'Add a few style tags first.' : 'Describe the sound first.'
  if (f.kind === 'song' && f.lyrics.length > LYRICS_MAX) return `The lyrics are ${f.lyrics.length.toLocaleString('en')} characters; the limit is ${LYRICS_MAX.toLocaleString('en')}.`
  const bpm = int(f.bpm)
  if (f.bpm.trim() && (bpm === null || bpm < BPM_MIN || bpm > BPM_MAX)) return `BPM goes from ${BPM_MIN} to ${BPM_MAX}.`
  return null
}

export const ACE_ID = 'ace15_turbo'
const NOTES = ['C', 'C#', 'Db', 'D', 'D#', 'Eb', 'E', 'F', 'F#', 'Gb', 'G', 'G#', 'Ab', 'A', 'A#', 'Bb', 'B']
// the keys ACE-Step's text encoder accepts, majors first
export const KEYSCALES = [...NOTES.map((n) => `${n} major`), ...NOTES.map((n) => `${n} minor`)]

export function audioPayload(f: AudioForm, m: ModelInfo): AudioGenerateRequest {
  const body: AudioGenerateRequest = {
    kind: f.kind,
    prompt: f.prompt.trim(),
    model: m.id,
    duration_s: clampAudioDuration(f.durationS, f.kind, m),
    count: Math.min(MAX_TAKES, Math.max(1, Math.round(f.count))),
  }
  if (f.kind === 'song') {
    body.language = f.language || 'en'
    if (f.vocal) body.vocal = f.vocal
    // empty lyrics already mean instrumental to the server
    const lyrics = f.lyrics.trim()
    if (lyrics && !instrumental(f)) body.lyrics = lyrics.slice(0, LYRICS_MAX)
    if (f.timbreId && has(m, 'timbre_ref')) body.timbre_ref_id = f.timbreId
    if (isArabicScriptLang(body.language)) body.aerab = f.aerab
    if (m.id === ACE_ID) {
      if (f.keyscale) body.keyscale = f.keyscale
      if (f.timeSignature) body.time_signature = f.timeSignature
      const g = Number.parseFloat(f.guidance)
      if (f.guidance.trim() && Number.isFinite(g) && g >= 0 && g <= 10) body.guidance = g
      if (!f.think) body.think = false
    }
  }
  if (f.kind === 'music' && f.category) body.category = f.category
  const bpm = int(f.bpm)
  if (f.kind !== 'sfx' && bpm !== null && bpm >= BPM_MIN && bpm <= BPM_MAX) body.bpm = bpm
  const seed = int(f.seed)
  if (seed !== null && seed >= 0) body.seed = seed
  const steps = int(f.steps)
  if (steps !== null && steps >= 1 && steps <= 100) body.steps = steps
  return body
}

/** Puts "[chorus]" on a line of its own at the cursor, and says where the cursor goes next. */
export function insertSection(text: string, at: number, section: LyricSection) {
  const pos = Math.max(0, Math.min(at, text.length))
  const before = text.slice(0, pos)
  const after = text.slice(pos)
  const lead = before && !before.endsWith('\n') ? '\n' : ''
  const tag = `[${section}]\n`
  const next = `${before}${lead}${tag}${after}`
  return { text: next, cursor: before.length + lead.length + tag.length }
}

export const sectionCount = (lyrics: string) => (lyrics.match(/^\s*\[[a-z ]+\]\s*$/gim) ?? []).length

export function audioResult(res: Partial<MediaBatch> | null | undefined) {
  return { items: res?.items ?? [], jobs: res?.jobs ?? [], modelResolved: res?.model_resolved ?? null }
}

const CATEGORY_KIND: Record<string, AudioKind> = { song: 'song', score: 'music', music: 'music', 'sound effects': 'sfx', sfx: 'sfx', ambience: 'sfx' }

/** Which page an audio prompt template opens: its own kind, else what its category suggests. */
export function audioKindOf(t: Pick<Template, 'category' | 'defaults'>): AudioKind {
  const k = t.defaults?.kind
  if (k === 'song' || k === 'music' || k === 'sfx') return k
  return CATEGORY_KIND[(t.category ?? '').toLowerCase()] ?? 'music'
}

/** "1:32"; the player and the waveform's text alternative use it. */
export function clock(s: number | null | undefined) {
  const t = Math.max(0, Math.floor(s ?? 0))
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`
}

const str = (v: unknown) => (typeof v === 'string' ? v : undefined)
const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : undefined)

/** A form from a prompt template's prefill or a result's params; anything unknown keeps the default. */
export function audioFormFrom(kind: AudioKind, p: Record<string, unknown>): AudioForm {
  const d = DEFAULT_AUDIO_FORM[kind]
  const vocal = VOCALS.find((v) => v.value === p.vocal)?.value
  const category = MUSIC_CATEGORIES.find((c) => c.value === p.category)?.value
  const bpm = num(p.bpm)
  return {
    ...d,
    prompt: str(p.prompt) ?? str(p.prompt_scaffold) ?? '',
    lyrics: str(p.lyrics) ?? '',
    durationS: num(p.duration_s) ?? d.durationS,
    count: Math.min(MAX_TAKES, Math.max(1, Math.round(num(p.count) ?? 1))),
    vocal: vocal ?? null,
    language: str(p.language) ?? d.language,
    aerab: p.aerab !== false,
    category: category ?? null,
    bpm: bpm ? String(Math.round(bpm)) : '',
    ...(str(p.model) && p.model !== AUTO_ID ? { model: str(p.model) } : {}),
  }
}
