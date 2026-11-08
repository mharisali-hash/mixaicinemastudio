import { STEPS, type Medium } from '@/lib/podcast'
import { VP_STEPS } from './video/vp'

export interface EpisodeStep {
  n: number
  id: string
  label: string
  group: 'episode' | 'video'
}

export const EPISODE_STEPS = STEPS.length

// a video episode runs on from Publish into the AI video's six steps, numbered after the episode's own
export function stepsFor(medium: Medium): EpisodeStep[] {
  const own: EpisodeStep[] = STEPS.map((s) => ({ n: s.n, id: s.id, label: s.label, group: 'episode' }))
  if (medium !== 'video') return own
  return [...own, ...VP_STEPS.map((s, i) => ({ n: EPISODE_STEPS + i + 1, id: `vp-${s.id}`, label: s.label, group: 'video' as const }))]
}

// ?step= takes an episode step's id, or a video step's name as people say it ("cast")
const NAMED: Record<string, number> = {
  ...Object.fromEntries(STEPS.map((s) => [s.id, s.n])),
  cast: 7,
  set: 8,
  look: 9,
  cameras: 10,
  render: 11,
  result: 12,
}

export const stepNamed = (name: string | null) => (name ? (NAMED[name] ?? null) : null)
