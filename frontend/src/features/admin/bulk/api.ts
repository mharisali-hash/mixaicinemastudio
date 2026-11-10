import { request } from '@/lib/api'
import { PHOTO_EXTS, uploadErrorText, xhrUpload } from '@/lib/upload'

export type BulkAction = 'cutout' | 'edit' | 'remove'
export type BatchStatus = 'uploading' | 'running' | 'paused' | 'done' | 'cancelled'
export type ItemStatus = 'uploaded' | 'queued' | 'running' | 'done' | 'failed' | 'cancelled'

export interface BulkOptions {
  instruction?: string
  magic_prompt?: 'off' | 'auto' | 'on'
  aspect?: string
  feather?: number
  // remove: what should go in every picture
  words?: string
  keywords?: string
}

export interface BulkBatch {
  id: string
  name: string
  action: BulkAction
  options: BulkOptions
  status: BatchStatus
  counts: Record<ItemStatus | 'total', number>
  run_job_id: string | null
  // seconds left at the pace of the last twenty pictures; null until there is a pace to go by
  eta_s: number | null
  created_at: string
  updated_at: string
}

export interface BulkItem {
  id: string
  seq: number
  filename: string
  status: ItemStatus
  error: string | null
  instruction: string | null
  keywords: string | null
  prompt_used: string | null
  sample: boolean
  source_url: string | null
  result_url: string | null
}

export interface BulkExport {
  job_id: string
  status: string
  progress: number
  message: string
  error: string | null
  created_at: string
  options: { originals?: boolean; captions?: boolean }
  parts: { n: number; count: number; bytes: number; url: string }[]
}

export interface PromptsResult {
  rows: number
  matched: number
  unmatched: string[]
  unmatched_count: number
}

export const ACTION_LABEL: Record<BulkAction, string> = { cutout: 'Remove background', edit: 'Edit with an instruction', remove: 'Remove by words' }
export const STATUS: Record<BatchStatus, { label: string; tone: 'neutral' | 'accent' | 'success' | 'warning' | 'danger' }> = {
  uploading: { label: 'Not started', tone: 'neutral' },
  running: { label: 'Running', tone: 'accent' },
  paused: { label: 'Paused', tone: 'warning' },
  done: { label: 'Finished', tone: 'success' },
  cancelled: { label: 'Cancelled', tone: 'neutral' },
}
export const bulkKey = ['admin', 'bulk'] as const

const base = '/admin/bulk'

export const bulkApi = {
  list: () => request<BulkBatch[]>('GET', base),
  create: (body: { name: string; action: BulkAction; options: BulkOptions }) => request<BulkBatch>('POST', base, body),
  get: (id: string) => request<BulkBatch>('GET', `${base}/${id}`),
  patch: (id: string, body: { name?: string; options?: BulkOptions }) => request<BulkBatch>('PATCH', `${base}/${id}`, body),
  remove: (id: string) => request<void>('DELETE', `${base}/${id}`),
  check: (id: string, files: { name: string; size: number }[]) => request<{ have: string[] }>('POST', `${base}/${id}/check`, { files }),
  preview: (id: string) => request<{ items: string[] }>('POST', `${base}/${id}/preview`, {}),
  run: (id: string) => request<BulkBatch>('POST', `${base}/${id}/run`, {}),
  pause: (id: string) => request<BulkBatch>('POST', `${base}/${id}/pause`, {}),
  cancel: (id: string) => request<BulkBatch>('POST', `${base}/${id}/cancel`, {}),
  retryFailed: (id: string) => request<BulkBatch>('POST', `${base}/${id}/retry-failed`, {}),
  items: (id: string, query: { status?: string; after?: number; limit?: number }) =>
    request<{ items: BulkItem[]; next: number | null }>('GET', `${base}/${id}/items`, undefined, query),
  exportStart: (id: string, body: { originals: boolean; captions: boolean }) => request<{ job_id: string }>('POST', `${base}/${id}/export`, body),
  exports: (id: string) => request<{ exports: BulkExport[] }>('GET', `${base}/${id}/exports`),
}

export const uploadBulkFile = (id: string, file: File, name: string) =>
  xhrUpload<{ id: string; duplicate: boolean }>(`${base}/${id}/files?name=${encodeURIComponent(name)}`, file, () => {}, (status, detail, n) =>
    status === 415 && detail ? `“${n}”: ${detail}` : uploadErrorText(status, detail, n))

export const uploadBulkZip = (id: string, file: File, onProgress: (f: number) => void) =>
  xhrUpload<{ job_id: string }>(`${base}/${id}/zip`, file, onProgress, (status, detail, n) => detail || uploadErrorText(status, detail, n))

export const uploadPrompts = (id: string, file: File) =>
  xhrUpload<PromptsResult>(`${base}/${id}/prompts`, file, () => {}, (status, detail, n) => detail || uploadErrorText(status, detail, n))

// a folder pick brings everything in it: only pictures go up, the rest is counted and left behind
export const isPicture = (f: File) => PHOTO_EXTS.includes(f.name.split('.').pop()?.toLowerCase() ?? '')

/** The name the server keys a file by: its path inside the picked folder, so a re-pick finds it again. */
export const pathOf = (f: File) => (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name
