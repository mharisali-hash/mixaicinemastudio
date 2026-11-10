import type { GenerationStatus, ImageAspect, ImageEditRequest, ImageGenerateRequest, Job, MediaItem } from './types'

// sizes are the server's ~1 MP snapped to /64 (contract-v5), shown so people know what they'll get
export const IMAGE_ASPECTS: { value: ImageAspect; label: string; size: string; w: number; h: number }[] = [
  { value: '1:1', label: 'Square', size: '1024×1024', w: 20, h: 20 },
  { value: '16:9', label: 'Wide', size: '1344×768', w: 28, h: 16 },
  { value: '9:16', label: 'Tall', size: '768×1344', w: 13, h: 22 },
  { value: '4:3', label: 'Classic', size: '1152×896', w: 24, h: 18 },
  { value: '3:4', label: 'Portrait', size: '896×1152', w: 17, h: 22 },
  { value: '2:3', label: 'Photo tall', size: '832×1216', w: 15, h: 22 },
  { value: '3:2', label: 'Photo wide', size: '1216×832', w: 26, h: 17 },
]

export const MAX_EDIT_SOURCES = 3
export const MAX_COUNT = 4
// Create can go higher: the fast models draw up to 8 in one go
export const GENERATE_MAX_COUNT = 8
// Edit's "separate pictures": a set made under one job, each picture its own version of the instruction
export const SET_MIN = 2
export const SET_MAX = 25
export const SET_PICKS = [9, 16, 25] as const

export const clampSet = (n: number) => Math.min(SET_MAX, Math.max(SET_MIN, Math.round(n) || SET_MIN))

const SHEET = /\b(sheet|grid|collage|composite|mosaic|montage)\b/i
// "25 distinct images", "16 different photos", "9 expressions"
const MANY = /\b(\d{1,2})\s+(?:[a-z-]+\s+){0,3}?(images?|pictures?|photos?|portraits?|versions?|shots?|panels?|faces?|expressions?|variations?|looks?)\b/i
const GRID = /\b([2-5])\s*[x×]\s*([2-5])\b/i

/** How many pictures an instruction asking for a sheet / grid / collage wants, when that's more than
 * Variations can make and no more than a set can; null otherwise. */
export function sheetCount(text: string): number | null {
  if (!SHEET.test(text)) return null
  const many = text.match(MANY)
  const grid = text.match(GRID)
  const n = many ? Number(many[1]) : grid ? Number(grid[1]) * Number(grid[2]) : 0
  return n > MAX_COUNT && n <= SET_MAX ? n : null
}

export interface ImageForm {
  prompt: string
  negative: string
  aspect: ImageAspect
  count: number
  style: string | null
  seed: string
  // where a reused picture sat in the batch it came from; with its seed that makes the same picture again
  batchIndex?: number
  steps: string
  templateId?: string
  // sample values for the template's [slots], shown as hints; never sent
  examples?: Record<string, string>
  // contract-v6; left out of the body when unset so the server picks its default
  model?: string
  speed?: string
}

export const DEFAULT_IMAGE_FORM: ImageForm = {
  prompt: '',
  negative: '',
  aspect: '1:1',
  count: 2,
  style: null,
  seed: '',
  steps: '',
}

const clampCount = (n: number) => Math.min(MAX_COUNT, Math.max(1, Math.round(n) || 1))
const optInt = (s: string) => {
  const n = Number.parseInt(s.trim(), 10)
  return Number.isFinite(n) && n >= 0 ? n : undefined
}

export function imagePayload(f: ImageForm): ImageGenerateRequest {
  const count = Math.min(GENERATE_MAX_COUNT, Math.max(1, Math.round(f.count) || 1))
  const body: ImageGenerateRequest = { prompt: f.prompt.trim(), aspect: f.aspect, count }
  if (f.negative.trim()) body.negative = f.negative.trim()
  if (f.style) body.style = f.style
  const seed = optInt(f.seed)
  if (seed !== undefined) body.seed = seed
  if (seed !== undefined && count === 1 && f.batchIndex) body.batch_index = f.batchIndex
  const steps = optInt(f.steps)
  if (steps) body.steps = Math.min(50, steps)
  if (f.templateId) body.template_id = f.templateId
  if (f.model) body.model = f.model
  if (f.model && f.speed) body.speed = f.speed
  return body
}

export function editPayload(
  sourceIds: string[],
  instruction: string,
  count: number,
  aspect: ImageAspect | null,
  model?: { id: string; max_refs?: number },
  setSize?: number | null,
): ImageEditRequest {
  const body: ImageEditRequest = {
    source_ids: [...new Set(sourceIds)].slice(0, model?.max_refs ?? MAX_EDIT_SOURCES),
    instruction: instruction.trim(),
    count: clampCount(count),
  }
  if (setSize) body.set_size = clampSet(setSize)
  if (aspect) body.aspect = aspect
  if (model) body.model = model.id
  return body
}

/** Adds ids up to the limit; returns the new list and whether anything was dropped. */
export function addSources(current: string[], incoming: string[], max = MAX_EDIT_SOURCES): { ids: string[]; dropped: number } {
  const ids = [...current]
  let dropped = 0
  for (const id of incoming) {
    if (ids.includes(id)) continue
    if (ids.length >= max) dropped++
    else ids.push(id)
  }
  return { ids, dropped }
}

/**
 * addSources for an edit template's labelled slots: '' marks an empty slot, which is filled first
 * (or the slot `at`, when the user clicked that one) so each picture stays under its label.
 */
export function placeSources(current: string[], incoming: string[], max = MAX_EDIT_SOURCES, at?: number): { ids: string[]; dropped: number } {
  const ids = [...current]
  let dropped = 0
  for (const id of incoming) {
    if (ids.includes(id)) continue
    let i = at !== undefined && !ids[at] ? at : ids.indexOf('')
    if (i < 0) i = ids.length
    at = undefined
    if (i >= max) {
      dropped++
      continue
    }
    while (ids.length < i) ids.push('')
    ids[i] = id
  }
  return { ids, dropped }
}

// "[product]" style slots in template prompts; the user swaps them for their own words.
// An @-mention token, @[Mara](character:id), is not a slot.
const SLOT = /(?<!@)\[[^\]\n]{1,40}\](?!\()/g

export const placeholdersIn = (text: string) => [...new Set(text.match(SLOT) ?? [])]

export function splitPlaceholders(text: string): { text: string; slot: boolean }[] {
  const out: { text: string; slot: boolean }[] = []
  let last = 0
  for (const m of text.matchAll(SLOT)) {
    if (m.index! > last) out.push({ text: text.slice(last, m.index), slot: false })
    out.push({ text: m[0], slot: true })
    last = m.index! + m[0].length
  }
  if (last < text.length) out.push({ text: text.slice(last), slot: false })
  return out
}

export type TileState = 'pending' | 'ready' | 'failed'

/** MediaItem has no status in the contract; combine what the item, its generation and its job say. */
export function tileState(item: MediaItem, gen?: { status: GenerationStatus } | null, job?: Job): TileState {
  const status = gen?.status ?? item.status
  if (status === 'failed' || job?.status === 'failed') return 'failed'
  if (status === 'queued' || status === 'generating') return 'pending'
  if (job && (job.status === 'queued' || job.status === 'running')) return 'pending'
  return item.media_url ? 'ready' : 'pending'
}

export const sizeLabel = (m: Pick<MediaItem, 'width' | 'height'>) => (m.width && m.height ? `${m.width}×${m.height}` : null)

/** Width over height, from the item, else the size the request asked for, else square. */
export function tileRatio(item: Pick<MediaItem, 'width' | 'height' | 'kind'>, hint?: string | null) {
  if (item.width && item.height) return `${item.width} / ${item.height}`
  const m = hint?.match(/^(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)$/)
  if (m) return `${m[1]} / ${m[2]}`
  return item.kind === 'video' ? '16 / 9' : '1 / 1'
}
