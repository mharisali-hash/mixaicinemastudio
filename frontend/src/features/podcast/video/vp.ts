import type { Generation, QuickStage, QuickStageKey, VideoPodcast, VPAspect, VPProgress, VPQuality, VPSetPreset, VPStageKey, VPTrack } from '@/lib/types'

// the server refuses anything that isn't word for word this (contract-v24)
export const LIKENESS_STATEMENT = "I have the right to use this person's likeness, and they agreed to it being animated."

export type VPStepId = 'hosts' | 'set' | 'look' | 'cameras' | 'render' | 'result'
export type VPStepState = 'done' | 'todo' | 'busy' | 'review'

export const VP_STEPS: { id: VPStepId; label: string; stage: VPStageKey }[] = [
  { id: 'hosts', label: 'Cast', stage: 'cast' },
  { id: 'set', label: 'Set', stage: 'set' },
  { id: 'look', label: 'Look', stage: 'keyframes' },
  { id: 'cameras', label: 'Cameras & cut', stage: 'plan' },
  { id: 'render', label: 'Render', stage: 'render' },
  { id: 'result', label: 'Result', stage: 'assemble' },
]

export const SET_PRESETS: { value: VPSetPreset; label: string }[] = [
  { value: 'modern_studio', label: 'Modern studio' },
  { value: 'cosy_room', label: 'Cosy room' },
  { value: 'news_desk', label: 'News desk' },
  { value: 'rooftop', label: 'Rooftop' },
  { value: 'desi_baithak', label: 'Desi baithak' },
]

export const ASPECTS: { value: VPAspect; label: string }[] = [
  { value: '16:9', label: '16:9 (YouTube)' },
  { value: '9:16', label: '9:16 (Shorts, Reels)' },
  { value: '1:1', label: '1:1 (square)' },
]

// host cameras take these in host order; colour is never the only cue, every block also carries its name
const HOST_SWATCHES = [
  { swatch: 'bg-studio-accent', ink: 'text-studio-accent-fg' },
  { swatch: 'bg-studio-gold', ink: 'text-studio-text' },
  { swatch: 'bg-studio-success/70', ink: 'text-studio-text' },
  { swatch: 'bg-studio-warning/60', ink: 'text-studio-text' },
]
const WIDE = { swatch: 'bg-studio-border-strong', ink: 'text-studio-text' }
const COVER = { swatch: 'bg-studio-darkroom', ink: 'text-studio-on-dark' }

export type CameraLook = { key: string; label: string; swatch: string; ink: string; thumb: string | null }

export function cameraLooks(vp: VideoPodcast): CameraLook[] {
  const cams = vp.plan.cameras.length
    ? vp.plan.cameras
    : [
        ...vp.hosts.map((h) => ({ key: h.host_id, label: h.name, kind: 'host' as const, host_id: h.host_id, thumb_url: null })),
        { key: 'wide', label: 'Wide', kind: 'wide' as const, host_id: null, thumb_url: null },
      ]
  let h = 0
  return cams.map((c) => {
    const look = c.kind === 'wide' ? WIDE : c.kind === 'cover' ? COVER : HOST_SWATCHES[h++ % HOST_SWATCHES.length]
    return { key: c.key, label: c.label, thumb: c.thumb_url, ...look }
  })
}

export const cameraLook = (looks: CameraLook[], key: string): CameraLook =>
  looks.find((c) => c.key === key) ?? { key, label: key === 'wide' ? 'Wide' : key === 'cover' ? 'Cover' : key, thumb: null, ...(key === 'cover' ? COVER : WIDE) }

export const hostName = (vp: VideoPodcast, hostId: string | null | undefined) => vp.hosts.find((h) => h.host_id === hostId)?.name ?? null

export const trackName = (vp: VideoPodcast, t: VPTrack) => (t.kind === 'wide' ? 'Wide' : (hostName(vp, t.host_id) ?? 'Host'))

export const takeReady = (t: VPTrack, q: VPQuality) => {
  const g = t.takes[q]
  return !!g?.media_url && (g.status === 'ready' || g.status === 'approved')
}

export function progressText(p: VPProgress | null | undefined, pieces: number) {
  if (!p) return null
  if (p.failed) return `piece ${p.failed} of ${p.pieces} failed`
  if (p.current) return `piece ${p.current} of ${p.pieces}`
  if (p.done >= p.pieces && p.pieces > 0) return `${p.pieces} of ${p.pieces} pieces done`
  return p.done ? `${p.done} of ${p.pieces || pieces} pieces done` : null
}

/** "75", "75.5", "1:15" or "01:15.5" → seconds; null when it isn't a time. */
export function parseTime(raw: string): number | null {
  const v = raw.trim()
  if (!v) return null
  const m = /^(\d+):(\d{1,2}(?:\.\d+)?)$/.exec(v)
  if (m) return Number(m[2]) >= 60 ? null : Number(m[1]) * 60 + Number(m[2])
  return /^\d+(\.\d+)?$/.test(v) ? Number(v) : null
}

export function nearestSnap(snaps: number[], at: number): number | null {
  let best: number | null = null
  for (const s of snaps) if (best === null || Math.abs(s - at) < Math.abs(best - at)) best = s
  return best
}

export const aspectClass = (a: VPAspect | undefined) => (a === '9:16' ? 'aspect-[9/16]' : a === '1:1' ? 'aspect-square' : 'aspect-video')

export const picture = (g: Generation | null | undefined) => g?.thumb_url ?? g?.media_url ?? null

export const generating = (g: Generation | null | undefined) => g?.status === 'queued' || g?.status === 'generating'

/** 65.34 → "01:05.3" */
export function clock(s: number) {
  const t = Math.max(0, Math.round(s * 10) / 10)
  const m = Math.floor(t / 60)
  const sec = (t - m * 60).toFixed(1).padStart(4, '0')
  return `${String(m).padStart(2, '0')}:${sec}`
}

export function stepStates(vp: VideoPodcast): Record<VPStepId, VPStepState> {
  const by = new Map(vp.stages.map((s) => [s.key, s.status]))
  const out = {} as Record<VPStepId, VPStepState>
  for (const step of VP_STEPS) {
    const st = by.get(step.stage)
    out[step.id] = st === 'running' ? 'busy' : st === 'done' || st === 'skipped' ? 'done' : st === 'review' ? 'review' : 'todo'
  }
  if (vp.outputs.draft || vp.outputs.final) out.result = by.get('assemble') === 'running' ? 'busy' : 'done'
  return out
}

/** The first earlier step that isn't finished: what this step is waiting on, if anything. */
export function blockingStep(vp: VideoPodcast, stepIndex: number): { step: (typeof VP_STEPS)[number]; index: number; status: string } | null {
  const by = new Map(vp.stages.map((s) => [s.key, s.status]))
  for (let i = 0; i < stepIndex; i++) {
    const status = by.get(VP_STEPS[i].stage) ?? 'pending'
    // render and the cut are started from their own steps, so they never hold up the cut
    if (VP_STEPS[i].stage === 'render' || VP_STEPS[i].stage === 'assemble') continue
    if (status !== 'done' && status !== 'skipped') return { step: VP_STEPS[i], index: i, status }
  }
  return null
}

export function initialStep(vp: VideoPodcast): number {
  if (vp.review === 'hosts') return 1
  if (vp.review === 'look') return 3
  if (vp.stage === 'done') return 6
  const i = VP_STEPS.findIndex((s) => s.stage === vp.stage)
  return i < 0 ? 1 : i + 1
}

// StageTimeline speaks Quick Create's stages; review isn't one of them, so it reads as waiting with a note
export function timelineStages(vp: VideoPodcast): QuickStage[] {
  return vp.stages.map((s) => ({
    key: s.key as unknown as QuickStageKey,
    label: s.status === 'review' ? `${s.label}: your review` : s.label,
    status: s.status === 'review' ? 'pending' : s.status,
    started_at: s.started_at,
    finished_at: s.finished_at,
    detail: s.detail,
  }))
}
