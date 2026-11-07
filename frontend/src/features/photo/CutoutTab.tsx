import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Eraser, ImageIcon, MousePointerClick, Paintbrush, Scissors } from 'lucide-react'
import { MediaPicker } from '@/components/media/MediaPicker'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { ErrorState } from '@/components/studio/states'
import { photoKeys, useRestoreTools } from '@/hooks/usePhoto'
import { api } from '@/lib/api'
import type { Background, Edge, HistoryVersion } from '@/lib/photo/types'
import type { Job } from '@/lib/types'
import { cn } from '@/lib/utils'
import { trackJobs } from '@/stores/toasts'
import { announce } from '@/stores/ui'
import { DevelopSlider } from './DevelopSlider'
import type { SelectPoints } from './SelectPointsOverlay'

const FEATHER = { min: 0, max: 20, step: 0.5, default: 0.6, label: 'Soften edge' }
const SHIFT = { min: -10, max: 10, step: 1, default: 0, label: 'Grow / shrink' }
const BLUR = { min: 2, max: 60, step: 1, default: 18, label: 'Blur' }
type BgKind = Background['type']

interface Props {
  photoId: string
  routeId: string
  title: string
  version: HistoryVersion
  points: SelectPoints
  onPoints: (p: SelectPoints) => void
  clickMode: 'include' | 'exclude' | null
  onClickMode: (m: 'include' | 'exclude' | null) => void
}

/**
 * Cut-out: BiRefNet finds the subject, SAM 3 adds or takes away by words and clicks, then edge and
 * background are cheap CPU re-composites of the stored mask. Each step is a new version.
 */
export function CutoutTab({ photoId, routeId, title, version, points, onPoints, clickMode, onClickMode }: Props) {
  const qc = useQueryClient()
  const tools = useRestoreTools()
  const avail = (id: string) => tools.data?.tools.find((t) => t.id === id)
  const cut = version.cutout
  const hasMask = !!cut?.has_mask
  const [words, setWords] = useState('')
  const [op, setOp] = useState<'replace' | 'add' | 'subtract'>(hasMask ? 'add' : 'replace')
  const [edge, setEdge] = useState<Edge>(cut?.edge ?? { feather: 0.6, shift: 0 })
  const [bg, setBg] = useState<BgKind>((cut?.background?.type as BgKind) ?? 'transparent')
  const [colour, setColour] = useState(cut?.background?.colour ?? '#f4ede0')
  const [radius, setRadius] = useState(cut?.background?.radius ?? 18)
  const [picture, setPicture] = useState<{ id: string; title: string } | null>(null)
  const [picking, setPicking] = useState(false)
  const [prompt, setPrompt] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [failure, setFailure] = useState<unknown>(null)

  const run = async (tag: string, fn: () => Promise<Job>, label: string): Promise<boolean> => {
    setBusy(tag)
    setFailure(null)
    try {
      const job = await fn()
      trackJobs([job], `${label} · ${title}`, `/image/studio/${routeId}`)
      announce(`${label} started. The result appears in the filmstrip as a new version.`)
      qc.invalidateQueries({ queryKey: photoKeys.history(routeId) })
      qc.invalidateQueries({ queryKey: ['jobs'] })
      return true
    } catch (e) {
      setFailure(e)
      return false
    } finally {
      setBusy(null)
    }
  }

  const nPoints = points.include.length + points.exclude.length
  const canSelect = !!words.trim() || points.include.length > 0
  const select = () =>
    run('select', () => api.photo.restore(photoId, { tool: 'select', words: words.trim() || undefined, include: points.include, exclude: points.exclude, op, edge }), 'Select').then((ok) => {
      if (!ok) return
      onPoints({ include: [], exclude: [] })
      onClickMode(null)
    })

  const background = (): Background =>
    bg === 'colour' ? { type: 'colour', colour } : bg === 'blur' ? { type: 'blur', radius } : bg === 'image' && picture ? { type: 'image', generation_id: picture.id } : { type: bg }

  const unavailable = (id: string) => {
    const t = avail(id)
    return t && !t.available ? t.reason : null
  }

  return (
    <div className="flex flex-col gap-4">
      {failure != null && <ErrorState compact title="That didn't start" error={failure} />}

      <section aria-labelledby="cut-h" className="rounded-[8px] border border-studio-border bg-studio-raised p-3">
        <h3 id="cut-h" className="font-display text-body font-semibold">Remove background</h3>
        <p className="mt-1 text-small text-studio-muted">
          {hasMask
            ? `Cut out · subject covers ${Math.round((cut?.coverage ?? 0) * 100)}% of the frame.`
            : 'Finds the subject and makes everything else transparent.'}
        </p>
        <Button className="mt-2.5 w-full" variant={hasMask ? 'secondary' : 'primary'} loading={busy === 'cutout'} disabled={!!unavailable('cutout')}
          onClick={() => run('cutout', () => api.photo.restore(photoId, { tool: 'cutout', edge }), 'Remove background')}>
          <Scissors aria-hidden /> {hasMask ? 'Detect the subject again' : 'Remove background'}
        </Button>
        {unavailable('cutout') && <p className="mt-1.5 text-[12px] text-studio-danger">Not available: {unavailable('cutout')}</p>}
      </section>

      <section aria-labelledby="sel-h" className="flex flex-col gap-2">
        <h3 id="sel-h" className="section-label">Select by words or clicks</h3>
        <Input aria-label="What to select" placeholder="e.g. the dog, the bottle, the man on the left" value={words} maxLength={200} onChange={(e) => setWords(e.target.value)} />
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Click on the photo to">
          <Button size="sm" variant={clickMode === 'include' ? 'primary' : 'outline'} aria-pressed={clickMode === 'include'}
            onClick={() => onClickMode(clickMode === 'include' ? null : 'include')}>
            <MousePointerClick aria-hidden /> Click to select
          </Button>
          <Button size="sm" variant={clickMode === 'exclude' ? 'primary' : 'outline'} aria-pressed={clickMode === 'exclude'}
            onClick={() => onClickMode(clickMode === 'exclude' ? null : 'exclude')}>
            <Eraser aria-hidden /> Click to leave out
          </Button>
          {nPoints > 0 && (
            <Button size="sm" variant="ghost" onClick={() => onPoints({ include: [], exclude: [] })}>Clear {nPoints} point{nPoints === 1 ? '' : 's'}</Button>
          )}
        </div>
        <div role="radiogroup" aria-label="What to do with the selection" className="flex flex-wrap gap-1.5">
          {(hasMask
            ? ([['add', 'Add to cut-out'], ['subtract', 'Remove from cut-out'], ['replace', 'Keep only this']] as const)
            : ([['replace', 'Keep only this'], ['subtract', 'Remove this']] as const)
          ).map(([v, l]) => (
            <Button key={v} size="sm" role="radio" aria-checked={op === v} variant={op === v ? 'primary' : 'outline'} onClick={() => setOp(v)}>{l}</Button>
          ))}
        </div>
        <Button size="sm" variant="primary" loading={busy === 'select'} disabled={!canSelect || !!unavailable('select')} onClick={select}>
          Select
        </Button>
        {unavailable('select') && <p className="text-[12px] text-studio-danger">Not available: {unavailable('select')}</p>}
      </section>

      <section aria-labelledby="bg-h" className={cn('flex flex-col gap-2', !hasMask && 'opacity-60')}>
        <h3 id="bg-h" className="section-label">Edge &amp; background</h3>
        {!hasMask && <p className="text-[12px] text-studio-muted">Remove the background first.</p>}
        <DevelopSlider name="cut-feather" label="Soften edge" value={edge.feather} range={FEATHER} onChange={(v) => setEdge((e) => ({ ...e, feather: v }))} />
        <DevelopSlider name="cut-shift" label="Grow / shrink" value={edge.shift} range={SHIFT} onChange={(v) => setEdge((e) => ({ ...e, shift: v }))} />
        <div role="radiogroup" aria-label="Background" className="flex flex-wrap gap-1.5">
          {([['transparent', 'Transparent'], ['colour', 'Colour'], ['blur', 'Blurred'], ['image', 'Picture']] as const).map(([v, l]) => (
            <Button key={v} size="sm" role="radio" aria-checked={bg === v} variant={bg === v ? 'primary' : 'outline'} disabled={!hasMask} onClick={() => setBg(v)}>{l}</Button>
          ))}
          {cut?.background?.type === 'generated' && (
            <Button size="sm" role="radio" aria-checked={bg === 'generated'} variant={bg === 'generated' ? 'primary' : 'outline'} onClick={() => setBg('generated')}>Painted</Button>
          )}
        </div>
        {bg === 'colour' && (
          <label className="flex items-center gap-2 text-small">
            <input type="color" value={colour} onChange={(e) => setColour(e.target.value)} className="h-8 w-12 cursor-pointer rounded border border-studio-border-strong bg-transparent" />
            <span className="font-mono text-[12px]">{colour}</span>
          </label>
        )}
        {bg === 'blur' && <DevelopSlider name="cut-blur" label="Blur" value={radius} range={BLUR} onChange={(v) => setRadius(v)} />}
        {bg === 'image' && (
          <Button size="sm" variant="outline" onClick={() => setPicking(true)}>
            <ImageIcon aria-hidden /> {picture ? picture.title : 'Choose a picture…'}
          </Button>
        )}
        <Button size="sm" variant="primary" disabled={!hasMask || (bg === 'image' && !picture)} loading={busy === 'bg'}
          onClick={() => run('bg', () => api.photo.background(photoId, background(), edge), 'New background')}>
          Apply edge &amp; background
        </Button>
      </section>

      <section aria-labelledby="paint-h" className={cn('flex flex-col gap-2', !hasMask && 'opacity-60')}>
        <h3 id="paint-h" className="section-label">Paint a new scene</h3>
        <Textarea aria-label="Describe the new background" rows={2} disabled={!hasMask} placeholder="e.g. a marble kitchen counter in soft morning light"
          value={prompt} onChange={(e) => setPrompt(e.target.value)} />
        <Button size="sm" variant="primary" disabled={!hasMask || !prompt.trim() || !!unavailable('background')} loading={busy === 'paint'}
          onClick={() => run('paint', () => api.photo.restore(photoId, { tool: 'background', prompt: prompt.trim(), edge }), 'Paint background').then((ok) => ok && setPrompt(''))}>
          <Paintbrush aria-hidden /> Paint background
        </Button>
        <p className="text-[12px] text-studio-muted">The subject keeps its original pixels; only the scene behind it is painted.</p>
      </section>

      <MediaPicker
        open={picking}
        onOpenChange={setPicking}
        kind="image"
        max={1}
        title="Background picture"
        description="It fills the frame behind the subject, cropped to fit."
        confirmLabel="Use as background"
        onConfirm={(items) => {
          const it = items[0]
          if (it) setPicture({ id: it.id, title: it.title || 'Picture' })
          setPicking(false)
        }}
      />
    </div>
  )
}
