import { useId, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { Gauge, Images, Shuffle } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { EmptyState, ErrorState } from '@/components/studio/states'
import { DockChip } from '@/components/generate/DockChip'
import { AspectChip, BrandDockChip, CountChip, ModelChip } from '@/components/generate/DockChips'
import { GenerateButton } from '@/components/generate/GenerateButton'
import { GeneratorPage, ResultsHeading } from '@/components/generate/GeneratorPage'
import { PromptDock } from '@/components/generate/PromptDock'
import { RefSlot } from '@/components/generate/RefSlot'
import { SessionResults } from '@/components/generate/SessionResults'
import { StyleDockChip } from '@/components/generate/StyleGallery'
import { useDockModels } from '@/components/generate/useDockModels'
import { useMagicPrompt } from '@/components/generate/useMagicPrompt'
import { useRecordRun } from '@/components/generate/useRecordRun'
import { useBrandChoice } from '@/hooks/useBrandKits'
import { useGenEstimate } from '@/hooks/useGenEstimate'
import { flatItems, useImageOptions, useImg2Img, useMediaItem, useMediaList } from '@/hooks/useMedia'
import { localEstimate } from '@/lib/estimate'
import { DEFAULT_I2I_FORM, i2iModels, img2imgPayload, STRENGTH_DEFAULT, strengthLabel, type Img2ImgForm } from '@/lib/img2img'
import { findStyle, restyleStrength } from '@/lib/styles'
import { defaultSpeed, normalizeModelId } from '@/lib/models'
import type { Img2ImgRequest } from '@/lib/types'
import { plural } from '@/lib/utils'
import { announce } from '@/stores/ui'
import { BeforeAfter } from '@/components/media/BeforeAfter'
import { CountPicker, ImageAspectTiles } from './controls'
import { StrengthSlider } from './StrengthSlider'

const PAGE = 'image-i2i'

interface I2iRun {
  form: Img2ImgForm
  model?: string
  speed?: string
  body: Img2ImgRequest
  summary: string
}

export function Img2ImgPage() {
  const uid = useId()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const wanted = normalizeModelId(params.get('model'))
  const [form, setForm] = useState<Img2ImgForm>({ ...DEFAULT_I2I_FORM, sourceId: params.get('source') })
  const [modelId, setModelId] = useState<string | undefined>(wanted ?? undefined)
  const [speedId, setSpeedId] = useState<string | undefined>()
  const [batch, setBatch] = useState<string[]>([])
  // once the user moves the slider, picking a style no longer moves it for them
  const [strengthSet, setStrengthSet] = useState(false)
  const set = <K extends keyof Img2ImgForm>(k: K, v: Img2ImgForm[K]) => setForm((f) => ({ ...f, [k]: v }))
  const options = useImageOptions()
  const styles = options.data?.styles ?? []
  const style = findStyle(styles, form.style, options.data?.style_aliases)
  const pickStyle = (id: string | null) => {
    const next = findStyle(styles, id)
    setForm((f) => ({ ...f, style: id, strength: strengthSet ? f.strength : restyleStrength(next, options.data?.style_groups ?? [], STRENGTH_DEFAULT) }))
  }
  const setStrength = (v: number) => {
    setStrengthSet(true)
    set('strength', v)
  }

  const { models, model, send, effective } = useDockModels('image', modelId, { only: i2iModels, prompt: form.prompt, count: form.count })
  const speed = model?.speeds?.find((s) => s.id === speedId && s.available !== false) ?? defaultSpeed(model)
  const brand = useBrandChoice()
  const run = useImg2Img()
  const record = useRecordRun<I2iRun>(PAGE)
  const results = useMediaList({ kind: 'image', origin: 'generated' })
  const source = useMediaItem(form.sourceId)
  const items = flatItems(results.data)
  const latest = items.find((m) => batch.includes(m.id) && m.media_url)
  const sourceUrl = source.data?.media_url ?? source.data?.thumb_url
  const magic = useMagicPrompt(form.prompt, { kind: 'image', model: send?.id, brandKitId: brand.sendId })
  const estimate = useGenEstimate(
    effective ? { kind: 'image', model: effective.id, ...(speed ? { speed: speed.id } : {}), count: form.count } : null,
    localEstimate(effective, { count: form.count, speed: speed?.id }),
  )

  // a style on its own is enough: "redraw my photo as watercolour"
  const blocked = !form.sourceId
    ? 'Add your picture first: drop, paste or pick one.'
    : !form.style && form.prompt.trim().length < 3
      ? 'Describe the result or pick a style first.'
      : null

  // a run from the dock empties the prompt once it's queued; a tile's Retry leaves the dock alone
  const go = (r: I2iRun, fromDock = false) =>
    run.mutate(r.body, {
      onSuccess: ({ items: made, jobs }) => {
        setBatch((made ?? []).map((m) => m.id))
        record({ prompt: r.body.prompt, summary: r.summary, settings: r, items: made ?? [], jobs, label: plural(made?.length ?? r.form.count, 'variation') })
        announce(`Making ${plural(made?.length ?? r.form.count, 'variation')}. They appear below as they finish.`)
        if (fromDock) {
          // a fresh start once it's queued: no words and no picture; ?source= goes too, so a reload stays empty
          setForm((f) => ({ ...f, prompt: '', sourceId: null }))
          if (params.has('source')) setParams((p) => (p.delete('source'), p), { replace: true })
        }
      },
    })

  const submit = () => {
    if (blocked || run.isPending || !form.sourceId) return
    const body = { ...img2imgPayload({ ...form, sourceId: form.sourceId }, send, speed?.id, brand.sendId), ...magic.fields() }
    const summary = [model?.label, style?.label, `${strengthLabel(form.strength)} ${form.strength.toFixed(2)}`, form.aspect === 'source' ? 'source shape' : form.aspect, plural(form.count, 'image')]
      .filter(Boolean)
      .join(' · ')
    go({ form, model: model?.id, speed: speed?.id, body, summary }, true)
  }

  const refill = (r: Partial<I2iRun> & { prompt?: string }) => {
    if (r.form) setForm(r.form)
    else if (r.prompt !== undefined) set('prompt', r.prompt)
    if (r.model) setModelId(r.model)
    if (r.speed) setSpeedId(r.speed)
    announce('Settings copied into the prompt dock.')
  }

  return (
    <GeneratorPage
      label="Image to image"
      targets={[
        { id: 'source', label: 'Image to Image source', hint: 'The picture to restyle or vary', use: (m) => set('sourceId', m.id) },
        { id: 'edit', label: 'Edit source', hint: 'Opens Edit Image with this picture', use: (m) => navigate(`/image/edit?sources=${m.id}`) },
      ]}
    >
      <PromptDock
        into="image"
        title="Image to Image"
        icon={<Shuffle aria-hidden className="size-4 text-studio-accent-hover" />}
        headerExtra={<p className="text-small text-studio-muted max-lg:hidden">The layout stays, the look changes.</p>}
        refs={
          <div className="flex items-start gap-3">
            <RefSlot label="Your picture" required value={form.sourceId} onChange={(id) => set('sourceId', id)} description="The picture to restyle or vary." />
          </div>
        }
        promptLabel="Describe the result"
        promptHint={form.style ? 'Optional with a style: leave it empty to restyle the picture as it is.' : undefined}
        prompt={form.prompt}
        onPrompt={(v) => set('prompt', v)}
        mentions={{ refBudget: 0, namesOnlyNote: 'Image to Image redraws one picture, so each mention adds its name and a short description. To put someone in, use Edit Image.' }}
        placeholder="e.g. Oil painting, warm evening light, visible brush strokes"
        chips={
          <>
            <ModelChip
              models={models}
              model={model}
              onChange={(id) => {
                setModelId(id)
                setSpeedId(undefined)
              }}
              speed={speed?.id}
              onSpeed={setSpeedId}
              highlighted={!!wanted}
            />
            <StyleDockChip value={form.style} onChange={pickStyle} modelId={effective?.id} noneHint="Your words only" />
            <DockChip name="Style strength" value={`${strengthLabel(form.strength)} ${form.strength.toFixed(2)}`} icon={<Gauge aria-hidden />}>
              <StrengthSlider value={form.strength} onChange={setStrength} />
              {style && !strengthSet && <p className="text-small text-studio-muted">Set for {style.label}; move it and it stays where you put it.</p>}
            </DockChip>
            <AspectChip value={form.aspect === 'source' ? 'Keep source' : form.aspect}>
              <ImageAspectTiles value={form.aspect === 'source' ? null : form.aspect} onChange={(v) => set('aspect', v ?? 'source')} allowAuto autoLabel="Keep source" />
            </AspectChip>
            <CountChip value={`×${form.count}`}>
              <CountPicker value={form.count} onChange={(n) => set('count', n)} />
            </CountChip>
            <BrandDockChip choice={brand} />
          </>
        }
        advanced={
          <div className="w-40">
            <Label htmlFor={`${uid}-seed`} className="mb-1.5">
              Seed
            </Label>
            <Input id={`${uid}-seed`} inputMode="numeric" value={form.seed} onChange={(e) => set('seed', e.target.value.replace(/\D/g, ''))} placeholder="Random" className="font-mono" />
          </div>
        }
        magic={magic}
        error={run.isError ? <ErrorState compact title="Couldn't start" error={run.error} /> : null}
        footer={
          <GenerateButton
            verb="Generate"
            what={plural(form.count, 'image')}
            estimate={estimate}
            icon={<Shuffle aria-hidden />}
            blocked={blocked}
            note="Your picture stays as it is; each result is a new image."
            pending={run.isPending}
          />
        }
        onSubmit={submit}
      />

      {latest?.media_url && sourceUrl && (
        <section aria-label="Before and after" className="max-w-3xl">
          <h2 className="section-label mb-3">Before and after</h2>
          <BeforeAfter before={sourceUrl} after={latest.media_url} alt={latest.title || form.prompt} aspectClass="aspect-[4/3]" />
        </section>
      )}

      <ResultsHeading>Your images</ResultsHeading>
      <SessionResults<I2iRun>
        page={PAGE}
        label="Results"
        items={items}
        loading={results.isPending}
        error={results.isError ? results.error : undefined}
        onRetryLoad={() => results.refetch()}
        hasMore={results.hasNextPage}
        loadingMore={results.isFetchingNextPage}
        onLoadMore={() => results.fetchNextPage()}
        onRetry={(req) => go(req.settings)}
        onReuse={(row) => refill(row.request?.settings ?? { prompt: row.prompt })}
        onUseAsRef={(m) => set('sourceId', m.id)}
        refLabel="Use as source"
        reuseItem={(m) => refill({ prompt: m.prompt ?? '' })}
        empty={
          <EmptyState icon={<Images />} title="Nothing yet">
            Add a picture, say how it should look, and the results land here.
          </EmptyState>
        }
      />
    </GeneratorPage>
  )
}
