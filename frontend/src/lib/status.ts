import type {
  ChunkStatus,
  Generation,
  GenerationKind,
  GenerationStatus,
  JobStatus,
  MezzanineStatus,
  ProjectStatus,
  ShotStatus,
} from './types'
import { humanize } from './utils'

export type Tone = 'neutral' | 'accent' | 'success' | 'warning' | 'danger'
export type StatusIcon = 'clock' | 'spinner' | 'eye' | 'check' | 'x' | 'alert' | 'pencil' | 'film' | 'ban' | 'stale'

export interface StatusView {
  label: string
  tone: Tone
  icon: StatusIcon
}

const pct = (p?: number) => (typeof p === 'number' ? ` ${Math.round(Math.min(1, Math.max(0, p)) * 100)}%` : '')

// Plain language only — raw codes like "frame_generated" never reach the screen (design-system §4).
export function generationStatus(status: GenerationStatus, progress?: number): StatusView {
  switch (status) {
    case 'queued':
      return { label: 'Waiting in queue', tone: 'neutral', icon: 'clock' }
    case 'generating':
      return { label: `Generating${pct(progress)}`, tone: 'accent', icon: 'spinner' }
    case 'ready':
      return { label: 'Ready for review', tone: 'success', icon: 'check' }
    case 'approved':
      return { label: 'Approved', tone: 'success', icon: 'check' }
    case 'rejected':
      return { label: 'Rejected', tone: 'neutral', icon: 'x' }
    case 'failed':
      return { label: 'Failed', tone: 'danger', icon: 'alert' }
  }
}

export function jobStatus(status: JobStatus, progress?: number): StatusView {
  switch (status) {
    case 'queued':
      return { label: 'Waiting', tone: 'neutral', icon: 'clock' }
    case 'running':
      return { label: `Running${pct(progress)}`, tone: 'accent', icon: 'spinner' }
    case 'done':
      return { label: 'Done', tone: 'success', icon: 'check' }
    case 'failed':
      return { label: 'Failed', tone: 'danger', icon: 'alert' }
    case 'cancelled':
      return { label: 'Cancelled', tone: 'neutral', icon: 'ban' }
  }
}

export function projectStatus(status: ProjectStatus): StatusView {
  switch (status) {
    case 'draft':
      return { label: 'Draft', tone: 'neutral', icon: 'pencil' }
    case 'in_progress':
      return { label: 'In progress', tone: 'accent', icon: 'clock' }
    case 'rendering':
      return { label: 'Rendering', tone: 'warning', icon: 'film' }
    case 'done':
      return { label: 'Finished', tone: 'success', icon: 'check' }
  }
}

export function shotStatus(status: ShotStatus): StatusView {
  switch (status) {
    case 'draft':
      return { label: 'Frames needed', tone: 'neutral', icon: 'pencil' }
    case 'frames_ready':
      return { label: 'Frames approved', tone: 'success', icon: 'check' }
    case 'rendering':
      return { label: 'Rendering', tone: 'accent', icon: 'spinner' }
    case 'take_ready':
      return { label: 'Takes to review', tone: 'accent', icon: 'eye' }
    case 'approved':
      return { label: 'Take approved', tone: 'success', icon: 'film' }
  }
}

export const staleStatus: StatusView = { label: 'Stale', tone: 'warning', icon: 'stale' }

const KIND_LABELS: Record<GenerationKind, string> = {
  portrait: 'Portrait',
  sheet_view: 'Character sheet view',
  establishing: 'Establishing frame',
  keyframe_start: 'Start frame',
  keyframe_end: 'End frame',
  keyframe_mid: 'Mid frame',
  take: 'Video take',
  tile: 'Long-take tile',
  render: 'Film render',
  mezzanine: 'Scene mezzanine',
  scene_text: 'Scene draft',
  image: 'Image',
  video: 'Video',
  model3d: '3D model',
  song: 'Song',
  music: 'Music',
  sfx: 'Sound effect',
  speech: 'Speech',
  upload: 'Upload',
}

export const kindLabel = (kind: GenerationKind) => KIND_LABELS[kind] ?? humanize(kind)

// Job types are backend-defined strings; known ones get a friendly name, the rest are humanised.
const JOB_LABELS: Record<string, string> = {
  generation: 'Generation',
  generate: 'Generation',
  portrait: 'Portrait',
  generate_portrait: 'Portrait',
  render: 'Render',
  ai_outline: 'AI Director outline',
  ai_write_scene: 'AI scene draft',
  ai_write_missing: 'Write missing scenes',
  ai_continue: 'Continue story',
  ai_assist: 'AI Assist',
  ai_extract_characters: 'Extract characters',
  ai_portrait_prompt: 'Portrait prompt',
  ai_summarize: 'Scene summary',
  ai_extract_locations: 'Extract locations',
  ai_suggest_shots: 'Suggest shots',
  ai_compile_prompts: 'Shot prompts',
  storyboard: 'Storyboard from script',
  ai_storyboard: 'Storyboard from script',
  take: 'Video take',
  ai_beats: 'Long-take beats',
  ai_write_beats: 'Long-take beats',
  regenerate_chunk: 'Re-roll chunks',
  reel_assemble: 'Assemble film',
  assemble: 'Assemble film',
  mezzanine: 'Scene mezzanine',
  autopilot: 'Quick video',
  quick: 'Quick video',
  quick_autopilot: 'Quick video',
  upscale: 'Upscale',
  media_zip: 'Zip download',
  photo_render: 'Photo Studio save',
  look_video: 'Look on video',
  image_generate: 'Create image',
  image_edit: 'Edit image',
  edit_set: 'Picture set',
}

export const jobLabel = (type: string) => JOB_LABELS[type] ?? humanize(type)

export const isActiveJob = (s: JobStatus) => s === 'queued' || s === 'running'
export const isPendingGeneration = (s: GenerationStatus) => s === 'queued' || s === 'generating'

export function scoreText(score: Generation['score']) {
  if (score == null) return null
  if (typeof score === 'number') return score.toFixed(1)
  const overall = score.overall ?? Object.values(score)[0]
  return typeof overall === 'number' ? overall.toFixed(1) : null
}

export function chunkStatus(status: ChunkStatus, progress?: number | null): StatusView {
  switch (status) {
    case 'queued':
      return { label: 'Waiting', tone: 'neutral', icon: 'clock' }
    case 'generating':
      return { label: `Generating${pct(progress ?? undefined)}`, tone: 'accent', icon: 'spinner' }
    case 'done':
      return { label: 'Done', tone: 'success', icon: 'check' }
    case 'failed':
      return { label: 'Failed', tone: 'danger', icon: 'alert' }
  }
}

export function mezzanineStatus(status: MezzanineStatus): StatusView {
  switch (status) {
    case 'fresh':
      return { label: 'Up to date', tone: 'success', icon: 'check' }
    case 'stale':
      return { label: 'Rebuild needed', tone: 'warning', icon: 'stale' }
    case 'building':
      return { label: 'Building', tone: 'accent', icon: 'spinner' }
    case 'missing':
      return { label: 'Not built yet', tone: 'neutral', icon: 'clock' }
  }
}
