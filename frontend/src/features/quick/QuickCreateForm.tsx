import { useId, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { Clapperboard, MessageSquareText, RectangleHorizontal, SlidersHorizontal, Timer, Wand2 } from 'lucide-react'
import { AspectTiles } from '@/components/studio/aspect-tile'
import { DurationFields } from '@/components/studio/duration-picker'
import { PlaceholderHint } from '@/components/studio/placeholder-hint'
import { ErrorState } from '@/components/studio/states'
import { SwitchRow } from '@/components/studio/switch-row'
import { DockChip } from '@/components/generate/DockChip'
import { BrandDockChip, StyleChip } from '@/components/generate/DockChips'
import { GenerateButton } from '@/components/generate/GenerateButton'
import { PromptDock } from '@/components/generate/PromptDock'
import { useMagicPrompt } from '@/components/generate/useMagicPrompt'
import { useBrandChoice } from '@/hooks/useBrandKits'
import { useCreateQuick } from '@/hooks/useQuick'
import { useUpscaleOptions } from '@/hooks/useUpscale'
import { withBrand } from '@/lib/brand'
import { formatDuration } from '@/lib/duration'
import { spread } from '@/lib/estimate'
import { placeholdersIn } from '@/lib/images'
import { DEFAULT_QUICK_FORM, QUICK_ASPECTS, QUICK_MAX_S, QUICK_MIN_S, QUICK_PRESETS, QUICK_STYLES, quickEstimateS, quickPayload, type QuickForm } from '@/lib/quick'
import type { QuickAspect, QuickStyle } from '@/lib/types'
import { pickEngine } from '@/lib/upscale'
import { trackJobs } from '@/stores/toasts'
import { announce } from '@/stores/ui'
import { QuickAdvanced } from './QuickAdvanced'

/**
 * One prompt → finished video. On the Quick Video page the dock heads the page above the recent films
 * and scrolls with them; on Home it sits inline.
 */
export function QuickCreateForm({
  expanded = false,
  className,
  initial,
  note,
}: {
  expanded?: boolean
  className?: string
  // from a template; still waits for the user to press Create
  initial?: Partial<QuickForm>
  note?: React.ReactNode
}) {
  const uid = useId()
  const field = useRef<HTMLTextAreaElement>(null)
  const navigate = useNavigate()
  const create = useCreateQuick()
  const options = useUpscaleOptions()
  const brand = useBrandChoice()
  const [form, setForm] = useState<QuickForm>({ ...DEFAULT_QUICK_FORM, ...initial })
  const set = <K extends keyof QuickForm>(k: K, v: QuickForm[K]) => setForm((f) => ({ ...f, [k]: v }))
  const magic = useMagicPrompt(form.prompt, { kind: 'quick', style: form.style, brandKitId: brand.sendId })

  const engines = options.data?.engines ?? []
  const engineId = pickEngine(engines, options.data?.default_engine)
  const engine = engines.find((e) => e.id === engineId)
  const upscaleHint = options.isError
    ? "Upscaling isn't available on this server yet."
    : options.data && !engine
      ? 'No upscale engine is installed on the server.'
      : `Adds a ${engine?.label ?? 'default engine'} pass at the end.`
  const slots = placeholdersIn(form.prompt)
  const blocked = slots.length ? `Replace ${slots.join(', ')} first.` : form.prompt.trim().length < 3 ? 'Describe your video first.' : null
  // the whole pipeline has no measured history to lean on, so it's always a rough range
  const estimate = spread(quickEstimateS(form.durationS, form.upscale ? engine : undefined), 'rough')
  const extras = [form.dialogue ? 'Narration' : 'No narration', form.upscale && engine ? '1080p' : null].filter(Boolean).join(' · ')

  const submit = () => {
    if (blocked || create.isPending) return
    const { prompt, ...magicFields } = magic.fields()
    create.mutate(withBrand({ ...quickPayload({ ...form, prompt }, engineId), ...magicFields }, brand.sendId), {
      onSuccess: ({ project, job }) => {
        trackJobs([job], `“${project.title}”`, `/quick/${project.id}`)
        announce(`Started “${project.title}”. Writing the script.`)
        navigate(`/quick/${project.id}`)
      },
    })
  }

  return (
    <PromptDock
      docked={expanded}
      className={className}
      title="Quick Create"
      icon={<Wand2 aria-hidden className="size-4 shrink-0 text-studio-accent-hover" />}
      headerExtra={
        <>
          <p className="text-small text-studio-muted max-lg:hidden">One prompt, a finished video.</p>
          {note}
        </>
      }
      promptLabel="Describe your video"
      prompt={form.prompt}
      onPrompt={(v) => set('prompt', v)}
      placeholder="Describe your video… e.g. A lighthouse keeper rescues a stray fox during a winter storm"
      promptRef={field}
      belowPrompt={<PlaceholderHint text={form.prompt} field={field} examples={form.examples} />}
      chips={
        <>
          <DockChip name="Length" value={formatDuration(form.durationS)} icon={<Timer aria-hidden />}>
            <DurationFields
              label="Length"
              value={form.durationS}
              onChange={(v) => set('durationS', v)}
              presets={QUICK_PRESETS}
              min={QUICK_MIN_S}
              max={QUICK_MAX_S}
              limitNoun=""
              estimateLine={null}
            />
          </DockChip>
          <DockChip name="Aspect" value={form.aspect} icon={<RectangleHorizontal aria-hidden />} wide>
            <div id={`${uid}-aspect`} className="section-label">
              Aspect
            </div>
            <AspectTiles value={form.aspect} onChange={(v) => set('aspect', v as QuickAspect)} labelledBy={`${uid}-aspect`} only={QUICK_ASPECTS} />
          </DockChip>
          <StyleChip options={QUICK_STYLES} value={form.style} allowEmpty={false} onChange={(v) => v && set('style', v as QuickStyle)} />
          <DockChip name="Sound and finish" value={extras} icon={<SlidersHorizontal aria-hidden />}>
            <SwitchRow
              id={`${uid}-dialogue`}
              checked={form.dialogue}
              onChange={(v) => set('dialogue', v)}
              icon={<MessageSquareText aria-hidden className="size-3.5" />}
              title="Dialogue / narration"
              hint="Characters speak, or a narrator tells it. Off: music and ambience only."
            />
            <SwitchRow
              id={`${uid}-upscale`}
              checked={form.upscale && !!engine}
              disabled={!engine}
              onChange={(v) => set('upscale', v)}
              icon={<Clapperboard aria-hidden className="size-3.5" />}
              title="Upscale to 1080p when done"
              hint={upscaleHint}
            />
          </DockChip>
          <BrandDockChip choice={brand} />
        </>
      }
      advanced={<QuickAdvanced form={form} onChange={(patch) => setForm((f) => ({ ...f, ...patch }))} />}
      magic={magic}
      error={create.isError ? <ErrorState compact title="Couldn't start the video" error={create.error} /> : null}
      footer={
        <GenerateButton
          verb="Create video"
          what={`${formatDuration(form.durationS)} film`}
          estimate={estimate}
          icon={<Wand2 aria-hidden />}
          blocked={blocked}
          note="You can close the page while it works."
          pending={create.isPending}
        />
      }
      onSubmit={submit}
    />
  )
}
