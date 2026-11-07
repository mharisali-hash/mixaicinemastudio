import type { Job, MediaItem } from './types'

// contract-v21

export type StemsMode = 'two' | 'four'

export interface StemSplit {
  // the job that made it
  id: string
  mode: StemsMode
  created_at: string
  stems: string[]
  items: MediaItem[]
  // the words heard on the vocal stem, when they were asked for
  lyrics?: string | null
}

export interface StemsResponse {
  source_id: string
  splits: StemSplit[]
  items: MediaItem[]
  // splits of this source still queued or running
  jobs: Job[]
}

export const STEMS_MODES: { id: StemsMode; label: string; stems: string; hint: string }[] = [
  {
    id: 'two',
    label: 'Vocals + instrumental',
    stems: 'Two stems',
    hint: 'The cleanest voice, from the same model Dub uses. About a minute for a song.',
  },
  {
    id: 'four',
    label: 'Vocals, drums, bass, other',
    stems: 'Four stems',
    hint: 'Demucs splits the band too. Takes roughly twice as long, and the voice is a little less clean.',
  },
]

export const modeLabel = (m: StemsMode) => STEMS_MODES.find((x) => x.id === m)?.stems ?? m

export const stemsLink = (id: string) => `/audio/stems?source=${encodeURIComponent(id)}`
