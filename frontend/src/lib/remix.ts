import type { Job, MediaItem } from './types'

// contract-v43: Remix a song (M24)

export type RemixRecipeId =
  | 'slowed_reverb'
  | 'sped_up'
  | 'nightcore'
  | 'lofi'
  | 'bass_boost'
  | 'eight_d'
  | 'key_tempo'
  | 'new_beat'
  | 'new_voice'
  | 'mashup'
  | 'medley'

export type RemixVocal = 'boy' | 'girl' | 'choir' | 'voice'

export interface RemixRecipe {
  id: RemixRecipeId
  label: string
  hint: string
  min_sources: number
  max_sources: number
  available: boolean
  reason: string | null
}

export interface RemixRecipes {
  recipes: RemixRecipe[]
  styles: string[]
  max_sources: number
}

export interface RemixOptions {
  style?: string
  vocal?: RemixVocal
  voice_id?: string
  semitones?: number
  tempo?: number
  vocals_from?: number
  music_from?: number
  then?: RemixRecipeId
}

export interface RemixStep {
  recipe: RemixRecipeId
  options?: RemixOptions
}

export interface RemixRequest {
  sources: string[]
  auto?: boolean
  recipes?: RemixStep[]
  folder_id?: string
}

export interface RemixAnalysis {
  item_id: string
  bpm: number | null
  key: string | null
  // the DJ wheel code (8A = A minor); neighbours mix well in a mashup
  camelot?: string | null
  duration_s: number
}

export interface RemixesResponse {
  source_id: string
  items: MediaItem[]
  jobs: Job[]
}

export const MAX_SONGS = 3

export const VOCALS: { id: RemixVocal; label: string }[] = [
  { id: 'boy', label: 'A boy' },
  { id: 'girl', label: 'A girl' },
  { id: 'choir', label: "Children's choir" },
  { id: 'voice', label: 'A Library voice' },
]

/** "~96 BPM · A minor (8A)", or null while nothing was heard. */
export function feel(a: Pick<RemixAnalysis, 'bpm' | 'key' | 'camelot'> | undefined): string | null {
  if (!a) return null
  const key = a.key && a.camelot ? `${a.key} (${a.camelot})` : a.key
  const parts = [a.bpm ? `~${Math.round(a.bpm)} BPM` : null, key].filter(Boolean)
  return parts.length ? parts.join(' · ') : null
}

/** Whether a recipe fits this many songs: one-song effects, or two-or-more mashups and medleys. */
export function fits(r: Pick<RemixRecipe, 'min_sources' | 'max_sources'>, songs: number): boolean {
  return songs >= r.min_sources && songs <= r.max_sources
}

export interface Steer {
  picked: RemixRecipeId[]
  style: string
  vocal: RemixVocal
  voiceId: string
  semitones: number
  tempo: number
  vocalsFrom: number
  musicFrom: number
}

export const DEFAULT_STEER: Steer = { picked: [], style: '', vocal: 'boy', voiceId: '', semitones: 0, tempo: 1, vocalsFrom: 0, musicFrom: 1 }

export function steps(s: Steer): RemixStep[] {
  return s.picked.map((recipe) => {
    if (recipe === 'new_beat') return { recipe, options: s.style.trim() ? { style: s.style.trim() } : {} }
    if (recipe === 'new_voice') return { recipe, options: { vocal: s.vocal, ...(s.vocal === 'voice' && s.voiceId ? { voice_id: s.voiceId } : {}) } }
    if (recipe === 'key_tempo') return { recipe, options: { semitones: s.semitones, tempo: s.tempo } }
    if (recipe === 'mashup') return { recipe, options: { vocals_from: s.vocalsFrom, music_from: s.musicFrom } }
    return { recipe }
  })
}
