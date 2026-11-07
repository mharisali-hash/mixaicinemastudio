import { useId, useRef, useState } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router'
import { BookmarkPlus, Brush, Images } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { PlaceholderHint } from '@/components/studio/placeholder-hint'
import { EmptyState, ErrorState } from '@/components/studio/states'
import { AspectChip, BrandDockChip, CountChip, ModelChip } from '@/components/generate/DockChips'
import { GenerateButton } from '@/components/generate/GenerateButton'
import { GeneratorPage, ResultsHeading } from '@/components/generate/GeneratorPage'
import { PromptDock } from '@/components/generate/PromptDock'
import { SaveTemplateDialog } from '@/components/generate/SaveTemplateDialog'
import { SessionResults } from '@/components/generate/SessionResults'
import { useMagicPrompt } from '@/components/generate/useMagicPrompt'
import { useDockModels } from '@/components/generate/useDockModels'
import { useRecordRun } from '@/components/generate/useRecordRun'
import { useBrandChoice } from '@/hooks/useBrandKits'
import { useGenEstimate } from '@/hooks/useGenEstimate'
import { flatItems, useImageEdit, useImageTools, useMediaList } from '@/hooks/useMedia'
import { withBrand } from '@/lib/brand'
import { localEstimate } from '@/lib/estimate'
import { addSources, editPayload, MAX_EDIT_SOURCES, placeholdersIn, placeSources } from '@/lib/images'
import { normalizeModelId } from '@/lib/models'
import { editFormFrom, fillSlot, type TemplateDraft, type TemplatePrefill } from '@/lib/templates'
import type { ImageAspect, ImageEditRequest, LightFrom, MediaItem, TemplateOption } from '@/lib/types'
import { plural } from '@/lib/utils'
import { announce } from '@/stores/ui'
import { CountPicker, ImageAspectTiles } from './controls'
import { EditSources } from './EditSources'
import { MaskBrush } from './MaskBrush'
import { TemplateChoices } from './TemplateChoices'
import { ToolChoice } from './ToolChoice'

const PAGE = 'image-edit'
const idsFrom = (raw: string | null) => (raw ?? '').split(',').map((s) => s.trim()).filter(Boolean)
const listOf = (words: string[]) => (words.length < 2 ? words.join('') : `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}`)

interface EditRun {
  ids: string[]
  instruction: string
  count: number
  aspect: ImageAspect | null
  model?: string
  seed: string
  body: ImageEditRequest
  summary: string
}

export function ImageEditPage() {
  const uid = useId()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const location = useLocation()
  const state = location.state as { sources?: string[]; template?: TemplatePrefill } | null
  const fromState = state?.sources ?? []
  // an edit template: its instruction, model, and the labelled pictures it needs (fixed for this visit)
  const [tpl] = useState(() =>
    state?.template ? { id: state.template.templateId, title: state.template.templateTitle, ...editFormFrom(state.template.prefill) } : null,
  )
  const labels = tpl?.labels ?? []
  // ?sources=a,b from a tile's Edit button or the References page; never more than three.
  // With a template, '' holds an empty labelled slot open.
  const [ids, setIds] = useState<string[]>(() => addSources([], [...idsFrom(params.get('sources')), ...fromState]).ids)
  const sources = ids.filter(Boolean)
  const [notice, setNotice] = useState<string | null>(null)
  const [instruction, setInstruction] = useState(tpl?.instruction ?? '')
  const [picked, setPicked] = useState<Record<string, number>>({})
  const field = useRef<HTMLTextAreaElement>(null)
  const [count, setCount] = useState(tpl?.count ?? 1)
  const [aspect, setAspect] = useState<ImageAspect | null>(tpl?.aspect ?? null)
  const [seed, setSeed] = useState('')
  const wanted = normalizeModelId(params.get('model'))
  const [modelId, setModelId] = useState<string | undefined>(wanted ?? tpl?.model)
  const edit = useImageEdit()
  const results = useMediaList({ kind: 'image', origin: 'generated' })
  const brand = useBrandChoice()
  const record = useRecordRun<EditRun>(PAGE)
  const { models, model, send, effective } = useDockModels('edit', modelId)
  // prompt correction clarifies the instruction (what changes, where, what stays); it never pads it
  const magic = useMagicPrompt(instruction, { kind: 'edit', model: send?.id, brandKitId: brand.sendId })
  const max = effective?.max_refs ?? MAX_EDIT_SOURCES
  const over = Math.max(0, sources.length - max)
  const estimate = useGenEstimate(effective ? { kind: 'image', model: effective.id, count } : null, localEstimate(effective, { count }))
  const name = model?.label ?? 'This model'
  // v30: a template with its own tool (LaMa, IC-Light, CatVTON, line art...) runs on it when the server can
  const tools = useImageTools()
  const tool = tpl?.tool
  const toolInfo = !tool || !tools.isSuccess ? undefined : (tools.data.find((t) => t.id === tool.id) ?? null)
  const [useTool, setUseTool] = useState(true)
  // the mask belongs to the photo it was painted on
  const [painted, setPainted] = useState<{ on: string; png: string | null } | null>(null)
  const mask = painted && painted.on === ids[0] ? painted.png : null
  const [light, setLight] = useState<LightFrom>('none')
  const [saving, setSaving] = useState<TemplateDraft | null>(null)
  const saveDraft = (): TemplateDraft => ({
    target: 'edit',
    prompt: instruction,
    title: tpl?.title,
    model: model?.id ?? null,
    aspect,
    count,
    refLabels: labels.length ? labels : sources.map((_, i) => `Image ${i + 1}`).slice(0, 3),
  })
  const toolOn = !!tool && !!toolInfo?.available && useTool
  // LaMa needs a mask; without one the edit model reads the instruction
  const toolRuns = toolOn && (!tool.needs_mask || !!mask)
  const wordsNeeded = !toolRuns || tool.id === 'relight'

  const add = (items: MediaItem[], at?: number) => {
    const next = placeSources(ids, items.map((m) => m.id), max, at)
    setIds(next.ids)
    setNotice(next.dropped ? `${name} takes ${max === 1 ? '1 image' : `up to ${max} images`}, so ${next.dropped === 1 ? "1 image wasn't" : `${next.dropped} images weren't`} added.` : null)
  }
  const remove = (id: string) => {
    setIds((cur) => {
      const at = cur.indexOf(id)
      // a labelled slot stays where it is, empty, so the other pictures keep their labels
      const next = at < labels.length ? cur.map((x) => (x === id ? '' : x)) : cur.filter((x) => x !== id)
      while (next.length && !next[next.length - 1]) next.pop()
      return next
    })
    setNotice(null)
  }

  const pick = (o: TemplateOption, i: number) => {
    const before = picked[o.slot] === undefined ? undefined : o.choices[picked[o.slot]]
    const choice = o.choices[i]
    setInstruction((text) => fillSlot(text, o.slot, choice.value, before?.value))
    setPicked((p) => ({ ...p, [o.slot]: i }))
    // e.g. the 3×3 grid wants a square; going back to a plain choice restores the template's shape
    if (choice.aspect) setAspect(choice.aspect)
    else if (before?.aspect) setAspect(tpl?.aspect ?? null)
  }

  const missing = labels.slice(0, tpl?.required ?? 0).filter((_, i) => !ids[i])
  const slots = placeholdersIn(instruction)
  const toolDown = tool && !tool.fallback && toolInfo !== undefined && !toolInfo?.available
  const blocked = over
    ? `Too many source images for ${name}.`
    : missing.length
      ? `Add the ${listOf(missing.map((l) => l.toLowerCase()))} ${missing.length > 1 ? 'pictures' : 'picture'} first.`
      : !sources.length
        ? 'Add at least one source image.'
        : toolDown
          ? `${tool.engine} isn't available on this server.`
          : wordsNeeded && slots.length
            ? `Replace ${slots.join(', ')} first.`
            : wordsNeeded && instruction.trim().length < 3
              ? 'Say what should change.'
              : null

  // a run from the dock empties the prompt once it's queued; a tile's Retry leaves the dock alone
  const run = (r: EditRun, fromDock = false) =>
    edit.mutate(r.body, {
      onSuccess: ({ items, jobs, tool_fallback }) => {
        record({ prompt: r.body.instruction, summary: r.summary, settings: r, items: items ?? [], jobs, label: plural(items?.length ?? r.count, 'edit') })
        announce(`Editing. ${plural(items?.length ?? r.count, 'result')} on the way.${tool_fallback ? ` Ran on the edit model: ${tool_fallback}.` : ''}`)
        if (fromDock) setInstruction('')
      },
    })

  const submit = () => {
    if (blocked || edit.isPending) return
    // images/edit treats a missing choice as off (API and MCP edits keep their words), so the dock always says which
    const f = magic.fields()
    const said = f.prompt_enhanced ? { prompt_enhanced: true } : { magic_prompt: f.magic_prompt ?? magic.mode }
    const base = { ...editPayload(sources, f.prompt, count, aspect, send && { id: send.id, max_refs: max }), ...said }
    const n = Number.parseInt(seed, 10)
    const body = withBrand({ ...base, ...(Number.isFinite(n) && n >= 0 ? { seed: n } : {}) }, brand.sendId)
    if (tpl?.id) body.template_id = tpl.id
    if (tool) {
      body.engine = useTool ? 'auto' : 'edit'
      if (toolRuns && mask) body.mask = mask
      if (toolOn && tool.id === 'relight') body.tool_options = { light }
    }
    const summary = [model?.label, plural(sources.length, 'source'), aspect ?? 'source shape', plural(count, 'result')].filter(Boolean).join(' · ')
    run({ ids: sources, instruction, count, aspect, model: model?.id, seed, body, summary }, true)
  }

  const refill = (r: Partial<EditRun>) => {
    if (r.ids) setIds(r.ids)
    if (r.instruction !== undefined) setInstruction(r.instruction)
    if (r.count) setCount(r.count)
    if (r.aspect !== undefined) setAspect(r.aspect)
    if (r.model) setModelId(r.model)
    if (r.seed !== undefined) setSeed(r.seed)
    announce('Settings copied into the prompt dock.')
  }

  return (
    <GeneratorPage
      label="Edit image"
      targets={[
        { id: 'ref', label: 'Reference', hint: 'Adds it to the source images', use: (m) => add([m]) },
        { id: 'i2i', label: 'Image to Image source', hint: 'Restyle or vary this picture instead', use: (m) => navigate(`/image/img2img?source=${m.id}`) },
      ]}
    >
      <PromptDock
        into="image"
        title="Edit Image"
        icon={<Brush aria-hidden className="size-4 text-studio-accent-hover" />}
        headerExtra={
          <>
            {tpl && <span className="rounded-full border border-studio-gold/70 bg-studio-gold/10 px-2 text-small">Prompt template: {tpl.title}</span>}
            <Button type="button" size="sm" variant="ghost" disabled={!instruction.trim()} onClick={() => setSaving(saveDraft())} aria-label="Save this instruction as a template">
              <BookmarkPlus aria-hidden />
              <span className="max-sm:sr-only">Save as template</span>
            </Button>
            <SaveTemplateDialog draft={saving} onOpenChange={(o) => !o && setSaving(null)} />
          </>
        }
        refs={
          <EditSources
            ids={ids}
            max={max}
            labels={labels}
            required={tpl?.required}
            notice={over ? `${name} takes ${max === 1 ? '1 image' : `up to ${max} images`}. Remove ${over === 1 ? '1 image' : `${over} images`} or pick another model.` : notice}
            onAdd={add}
            onRemove={remove}
          />
        }
        promptLabel="What should change?"
        prompt={instruction}
        onPrompt={setInstruction}
        promptRef={field}
        belowPrompt={
          tpl && (
            <div className="flex flex-col gap-2">
              {tool && <ToolChoice tool={tool} info={toolInfo} useTool={useTool} onUseTool={setUseTool} light={light} onLight={setLight} />}
              {tool?.needs_mask && toolOn && ids[0] && <MaskBrush key={ids[0]} sourceId={ids[0]} onChange={(png) => setPainted({ on: ids[0], png })} />}
              <TemplateChoices options={tpl.options} picked={picked} onPick={pick} />
              <PlaceholderHint text={instruction} field={field} examples={tpl.examples} />
            </div>
          )
        }
        mentions={{ refBudget: max, refsUsed: sources.length }}
        magic={magic}
        placeholder={sources.length > 1 ? 'e.g. Put the woman from image 1 in the jacket from image 2, on the street from image 3' : 'What should change? e.g. Make it night, with rain on the window'}
        chips={
          <>
            <ModelChip models={models} model={model} onChange={setModelId} highlighted={!!wanted} />
            <AspectChip value={aspect ?? 'Source shape'}>
              <ImageAspectTiles value={aspect} onChange={setAspect} allowAuto />
            </AspectChip>
            <CountChip value={`×${count}`}>
              <CountPicker value={count} onChange={setCount} />
            </CountChip>
            <BrandDockChip choice={brand} />
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
        error={edit.isError ? <ErrorState compact title="Couldn't start the edit" error={edit.error} /> : null}
        footer={
          <GenerateButton
            verb="Edit"
            what={plural(count, 'result')}
            estimate={estimate}
            icon={<Brush aria-hidden />}
            blocked={blocked}
            note="The result is a new image; your sources stay as they are."
            pending={edit.isPending}
          />
        }
        onSubmit={submit}
      />
      <ResultsHeading>Your images</ResultsHeading>
      <SessionResults<EditRun>
        page={PAGE}
        label="Results"
        items={flatItems(results.data)}
        loading={results.isPending}
        error={results.isError ? results.error : undefined}
        onRetryLoad={() => results.refetch()}
        hasMore={results.hasNextPage}
        loadingMore={results.isFetchingNextPage}
        onLoadMore={() => results.fetchNextPage()}
        onRetry={(req) => run(req.settings)}
        onReuse={(row) => refill(row.request?.settings ?? { instruction: row.prompt })}
        onUseAsRef={(m) => add([m])}
        refLabel="Add as source"
        reuseItem={(m) => refill({ instruction: m.prompt ?? '' })}
        empty={
          <EmptyState icon={<Images />} title="Nothing edited yet">
            Pick a source, say what to change, and the results land here.
          </EmptyState>
        }
      />
    </GeneratorPage>
  )
}
