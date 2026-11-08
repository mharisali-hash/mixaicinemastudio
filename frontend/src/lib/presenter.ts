import type { CaptionStyle } from '@/components/generate/CaptionStylePicker'
import { request } from './api'
import type { Generation, Job } from './types'

// Presenter Video: a character presenting a script to camera, scene by scene

export type PresenterAspect = '16:9' | '9:16' | '1:1'
export type PresenterCaptions = CaptionStyle | 'none'
export type PresenterStageKey = 'speech' | 'frames' | 'render' | 'assemble'
export type PresenterStageStatus = 'pending' | 'running' | 'review' | 'done' | 'failed' | 'skipped'

export interface PresenterSettings {
  aspect: PresenterAspect
  caption_style: PresenterCaptions
  background: string
  lower_name: string
  lower_role: string
  title_card: boolean
}

export interface PresenterScene {
  id: string
  text: string
  background: string
  duration_s: number | null
  speech: Generation | null
  frame: Generation | null
  take: Partial<Record<'draft' | 'final', Generation | null>>
}

export interface PresenterOutput {
  media_id: string
  generation_id: string
  quality: 'draft' | 'final'
  duration_s?: number
  width?: number
  height?: number
  built_at?: string
}

export interface PresenterVideo {
  id: string
  title: string
  language: string
  character_id: string | null
  character_name: string | null
  look_id: string | null
  settings: PresenterSettings
  scenes: PresenterScene[]
  stages: { key: PresenterStageKey; label: string; status: PresenterStageStatus; detail?: string | null; started_at?: string | null; finished_at?: string | null }[]
  outputs: Partial<Record<'draft' | 'final', PresenterOutput>>
  job: Job | null
  updated_at: string
}

export interface PresenterSummary {
  id: string
  title: string
  character_id: string | null
  scenes: number
  aspect: PresenterAspect
  outputs: Partial<Record<'draft' | 'final', PresenterOutput>>
  updated_at: string
}

export interface PresenterCreate {
  character_id: string
  look_id?: string | null
  title?: string
  language?: string
  script: string
  review?: boolean
  settings?: Partial<PresenterSettings>
}

export const BACKGROUNDS: { key: string; label: string }[] = [
  { key: 'studio', label: 'Studio' },
  { key: 'office', label: 'Office' },
  { key: 'classroom', label: 'Classroom' },
  { key: 'news_desk', label: 'News desk' },
  { key: 'baithak', label: 'Baithak' },
  { key: 'outdoor', label: 'Outdoors' },
  { key: 'plain', label: 'Plain wall' },
]

export const presenterApi = {
  list: () => request<PresenterSummary[]>('GET', '/presenter'),
  get: (id: string) => request<PresenterVideo>('GET', `/presenter/${id}`),
  create: (body: PresenterCreate) => request<PresenterVideo>('POST', '/presenter', body),
  update: (id: string, body: { title?: string; look_id?: string | null; settings?: Partial<PresenterSettings>; scenes?: { id?: string; text: string; background: string }[] }) =>
    request<PresenterVideo>('PATCH', `/presenter/${id}`, body),
  remove: (id: string) => request<void>('DELETE', `/presenter/${id}`),
  continue: (id: string) => request<Job>('POST', `/presenter/${id}/continue`),
  render: (id: string, quality: 'draft' | 'final') => request<Job>('POST', `/presenter/${id}/render`, { quality }),
  redo: (id: string, sceneId: string, what: 'frame' | 'take') => request<Job>('POST', `/presenter/${id}/scenes/${sceneId}/redo?what=${what}`),
}

export const presenterBusy = (pv: PresenterVideo | undefined) =>
  !!pv && (pv.job?.status === 'queued' || pv.job?.status === 'running' || pv.stages.some((s) => s.status === 'running'))

export const atReview = (pv: PresenterVideo) => pv.stages.some((s) => s.key === 'frames' && s.status === 'review')

// a sentence or two per scene; the server splits long paragraphs the same way
export const scenesIn = (script: string) => script.split(/\n\s*\n/).filter((p) => p.trim()).length
