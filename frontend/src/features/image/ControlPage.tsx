import { useId, useRef, useState } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router'
import { Gauge, Images, PenLine } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { PlaceholderHint } from '@/components/studio/placeholder-hint'
import { EmptyState, ErrorState } from '@/components/studio/states'
import { DockChip } from '@/components/generate/DockChip'
import { AspectChip, CountChip } from '@/components/generate/DockChips'
import { GenerateButton } from '@/components/generate/GenerateButton'
import { GeneratorPage, ResultsHeading } from '@/components/generate/GeneratorPage'
import { PromptDock } from '@/components/generate/PromptDock'
import { RefSlot } from '@/components/generate/RefSlot'
import { SessionResults } from '@/components/generate/SessionResults'
import { StyleDockChip } from '@/components/generate/StyleGallery'
import { useRecordRun } from '@/components/generate/useRecordRun'
import { flatItems, useControlStatus, useImageControl, useMediaList } from '@/hooks/useMedia'
import { placeholdersIn } from '@/lib/images'
import { controlFormFrom, type TemplatePrefill } from '@/lib/templates'
import type { ControlType, ImageControlRequest, Img2ImgAspect } from '@/lib/types'
import { cn, plural } from '@/lib/utils'
import { announce } from '@/stores/ui'
import { CountPicker, ImageAspectTiles } from './controls'

const PAGE = 'image-control'

// what each map is called, and what "use as is" means for it
const KINDS: { id: ControlType; label: string; map: string; photo: string; asIs: string }[] = [
  { id: 'depth', label: 'Depth', map: 'depth map', photo: 'We read near and far from your photo.', asIs: 'A greyscale depth map: near is light.' },
  { id: 'scribble', label: 'Sketch', map: 'sketch', photo: 'We trace the main lines of your photo.', asIs: 'Your drawing; dark lines on white are fine.' },
  { id: 'pose', label: 'Pose', map: 'pose', photo: 'We find the body pose in your photo.', asIs: 'A pose skeleton (coloured sticks on black).' },
  { id: 'canny', label: 'Edges', map: 'edge map', photo: 'We trace crisp outlines from your photo.', asIs: 'A line drawing or edge map.' },
  { id: 'hed', label: 'Soft edges', map: 'soft edge map', photo: 'We trace soft outlines from your photo.', asIs: 'A soft edge map.' },
]

const STRENGTH = { min: 0.65, max: 1, step: 0.05 }
const strengthWord = (v: number) => (v < 0.75 ? 'Loose' : v < 0.9 ? 'Close' : 'Exact')

interface ControlRun {
  body: ImageControlRequest
  summary: string
}

/** Depth / Sketch / Pose / Edge to image (contract v31): a picture steers the layout, the prompt says what to draw. */
export function ControlPage() {
  const uid = useId()
  const navigate = useNavigate()
  const location = useLocation()
  const [params] = useSearchParams()
  const tpl = (location.state as { template?: TemplatePrefill } | null)?.template
  const [initial] = useState(() => controlFormFrom({ control: params.get('type') ?? undefined, ...(tpl?.prefill ?? {}) }))
  const [sourceId, setSourceId] = useState<string | null>(params.get('source'))
  const [control, setControl] = useState<ControlType>(initial.control)
  const [asIs, setAsIs] = useState(initial.useAsIs)
  const [prompt, setPrompt] = useState(initial.prompt)
  const [style, setStyle] = useState<string | null>(initial.style)
  const [strength, setStrength] = useState(initial.strength)
  const [count, setCount] = useState(initial.count)
  const [aspect, setAspect] = useState<Img2ImgAspect>('source')
  const [seed, setSeed] = useState('')
  const field = useRef<HTMLTextAreaElement>(null)
  const status = useControlStatus()
  const run = useImageControl()
  const record = useRecordRun<ControlRun>(PAGE)
  const results = useMediaList({ kind: 'image', origin: 'generated' })

  const net = status.data
  // types the server lists (HED is left out when it's switched off); until it answers, the four safe ones
  const offered = KINDS.filter((k) => (net?.types?.length ? net.types.some((t) => t.id === k.id) : k.id !== 'hed'))
  const kind = KINDS.find((k) => k.id === control) ?? KINDS[0]
  const typeInfo = net?.types?.find((t) => t.id === control)
  const ready = !!net && net.available && typeInfo?.available !== false
  const engineNote = status.isPending
    ? 'Checking whether the ControlNet is installed…'
    : ready
      ? `Runs on ${net.engine}.`
      : `The ControlNet can't run here (${typeInfo?.reason ?? net?.reason ?? 'not reported'}), so Qwen-Image-Edit uses your picture as a guide instead. It follows it more loosely.`

  const slots = placeholdersIn(prompt)
  const blocked = !sourceId
    ? `Add a photo or a ${kind.map} first.`
    : slots.length
      ? `Replace ${slots.join(', ')} first.`
      : prompt.trim().length < 3
        ? 'Describe the picture you want.'
        : null

  // a run from the dock empties the prompt once it's queued; a tile's Retry leaves the dock alone
  const go = (r: ControlRun, fromDock = false) =>
    run.mutate(r.body, {
      onSuccess: ({ items, jobs, tool_fallback }) => {
        record({ prompt: r.body.prompt, summary: r.summary, settings: r, items: items ?? [], jobs, label: plural(items?.length ?? r.body.count, 'image') })
        announce(`Making ${plural(items?.length ?? r.body.count, 'image')}.${tool_fallback ? ' The ControlNet is unavailable, so the edit model is following your picture.' : ''}`)
        if (fromDock) setPrompt('')
      },
    })

  const submit = () => {
    if (blocked || run.isPending || !sourceId) return
    const body: ImageControlRequest = { source_id: sourceId, control, use_as_is: asIs, prompt: prompt.trim(), strength, aspect, count }
    if (style) body.style = style
    const n = Number.parseInt(seed, 10)
    if (Number.isFinite(n) && n >= 0) body.seed = n
    if (tpl?.templateId) body.template_id = tpl.templateId
    const summary = [`${kind.label} to image`, asIs ? 'used as is' : 'from a photo', `${strengthWord(strength)} ${strength.toFixed(2)}`, plural(count, 'image')].join(' · ')
    go({ body, summary }, true)
  }

  const refill = (r: Partial<ControlRun> & { prompt?: string }) => {
    const b = r.body
    if (b) {
      setSourceId(b.source_id)
      setControl(b.control)
      setAsIs(!!b.use_as_is)
      setPrompt(b.prompt)
      setStyle(b.style ?? null)
      setStrength(b.strength)
      setCount(b.count)
      setAspect(b.aspect)
    } else if (r.prompt !== undefined) setPrompt(r.prompt)
    announce('Settings copied into the prompt dock.')
  }

  return (
    <GeneratorPage
      label="Sketch, depth and pose to image"
      targets={[
        { id: 'source', label: 'Control picture', hint: 'The photo, map or sketch that sets the layout', use: (m) => setSourceId(m.id) },
        { id: 'edit', label: 'Edit source', hint: 'Opens Edit Image with this picture', use: (m) => navigate(`/image/edit?sources=${m.id}`) },
      ]}
    >
      <PromptDock
        into="image"
        title={`${kind.label} to image`}
        icon={<PenLine aria-hidden className="size-4 text-studio-accent-hover" />}
        headerExtra={tpl && <span className="rounded-full border border-studio-gold/70 bg-studio-gold/10 px-2 text-small">Prompt template: {tpl.templateTitle}</span>}
        refs={
          <div className="flex flex-wrap items-start gap-4">
            <RefSlot label={asIs ? `Your ${kind.map}` : 'Your photo'} required value={sourceId} onChange={setSourceId} description={asIs ? kind.asIs : kind.photo} />
            <div className="flex min-w-0 flex-1 flex-col gap-3">
              <fieldset className="flex flex-col gap-1.5">
                <legend className="mb-1 text-small font-medium">Follow the picture's</legend>
                <div className="flex flex-wrap gap-1">
                  {offered.map((k) => (
                    <label
                      key={k.id}
                      className={cn(
                        'inline-flex h-8 cursor-pointer items-center rounded-[6px] border px-3 text-small has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-studio-accent',
                        control === k.id ? 'border-studio-accent bg-studio-accent-soft text-studio-text' : 'border-studio-border-strong bg-studio-raised text-studio-muted hover:text-studio-text',
                      )}
                    >
                      <input type="radio" name={`${uid}-kind`} value={k.id} checked={control === k.id} onChange={() => setControl(k.id)} className="sr-only" />
                      {k.label}
                    </label>
                  ))}
                </div>
              </fieldset>
              <fieldset className="flex flex-col gap-1">
                <legend className="mb-1 text-small font-medium">The picture is</legend>
                <label className="flex items-center gap-1.5 text-small">
                  <input type="radio" name={`${uid}-asis`} checked={!asIs} onChange={() => setAsIs(false)} className="accent-studio-accent" />
                  A photo: make the {kind.map} from it
                </label>
                <label className="flex items-center gap-1.5 text-small">
                  <input type="radio" name={`${uid}-asis`} checked={asIs} onChange={() => setAsIs(true)} className="accent-studio-accent" />
                  Already a {kind.map}: use it as is
                </label>
              </fieldset>
              <p role="status" className={cn('text-small', ready ? 'text-studio-muted' : 'text-studio-text')}>
                {engineNote}
              </p>
            </div>
          </div>
        }
        promptLabel="Describe the picture"
        prompt={prompt}
        onPrompt={setPrompt}
        promptRef={field}
        mentions={{ refBudget: 0, namesOnlyNote: 'The picture sets the layout, so mentions add only their names and descriptions.' }}
        placeholder="e.g. A knight in silver armour on a misty hill at dawn"
        belowPrompt={<PlaceholderHint text={prompt} field={field} examples={initial.examples} />}
        chips={
          <>
            <StyleDockChip value={style} onChange={setStyle} modelId="zimage_turbo" />
            <DockChip name="Control strength" value={`${strengthWord(strength)} ${strength.toFixed(2)}`} icon={<Gauge aria-hidden />}>
              <div className="flex flex-col gap-1.5">
                <label htmlFor={`${uid}-s`} className="section-label">
                  Control strength
                </label>
                <input
                  id={`${uid}-s`}
                  type="range"
                  min={STRENGTH.min}
                  max={STRENGTH.max}
                  step={STRENGTH.step}
                  value={strength}
                  onChange={(e) => setStrength(Number(e.target.value))}
                  aria-valuetext={`${strength.toFixed(2)}, ${strengthWord(strength)}`}
                  className="w-full accent-[var(--accent)]"
                />
                <p className="text-small text-studio-muted">Lower lets the prompt wander from the {kind.map}; higher follows it line for line.</p>
              </div>
            </DockChip>
            <AspectChip value={aspect === 'source' ? 'Keep source' : aspect}>
              <ImageAspectTiles value={aspect === 'source' ? null : aspect} onChange={(v) => setAspect(v ?? 'source')} allowAuto autoLabel="Keep source" />
            </AspectChip>
            <CountChip value={`×${count}`}>
              <CountPicker value={count} onChange={setCount} />
            </CountChip>
          </>
        }
        advanced={
          <div className="w-40">
            <Label htmlFor={`${uid}-seed`} className="mb-1.5">
              Seed
            </Label>
            <Input id={`${uid}-seed`} inputMode="numeric" value={seed} onChange={(e) => setSeed(e.target.value.replace(/\D/g, ''))} placeholder="Random" className="font-mono" />
          </div>
        }
        error={run.isError ? <ErrorState compact title="Couldn't start" error={run.error} /> : null}
        footer={
          <GenerateButton verb="Generate" what={plural(count, 'image')} estimate={null} icon={<PenLine aria-hidden />} blocked={blocked} note="Your picture stays as it is; each result is a new image." pending={run.isPending} />
        }
        onSubmit={submit}
      />
      <ResultsHeading>Your images</ResultsHeading>
      <SessionResults<ControlRun>
        page={PAGE}
        label="Results"
        items={flatItems(results.data)}
        loading={results.isPending}
        error={results.isError ? results.error : undefined}
        onRetryLoad={() => results.refetch()}
        hasMore={results.hasNextPage}
        loadingMore={results.isFetchingNextPage}
        onLoadMore={() => results.fetchNextPage()}
        onRetry={(req) => go(req.settings)}
        onReuse={(row) => refill(row.request?.settings ?? { prompt: row.prompt })}
        onUseAsRef={(m) => setSourceId(m.id)}
        refLabel="Use as control picture"
        reuseItem={(m) => refill({ prompt: m.prompt ?? '' })}
        empty={
          <EmptyState icon={<Images />} title="Nothing yet">
            Add a photo or a sketch, say what to draw, and the results land here.
          </EmptyState>
        }
      />
    </GeneratorPage>
  )
}
