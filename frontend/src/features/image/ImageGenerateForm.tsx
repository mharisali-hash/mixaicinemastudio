import { useId, useRef, useState } from 'react'
import { BookmarkPlus, ChevronDown, ImagePlus, LayoutTemplate, Wand2 } from 'lucide-react'
import { Link } from 'react-router'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { PlaceholderHint } from '@/components/studio/placeholder-hint'
import { ErrorState } from '@/components/studio/states'
import { DockChip } from '@/components/generate/DockChip'
import { AspectChip, BrandDockChip, CountChip, ModelChip } from '@/components/generate/DockChips'
import { GenerateButton } from '@/components/generate/GenerateButton'
import { PromptDock } from '@/components/generate/PromptDock'
import { SaveTemplateDialog } from '@/components/generate/SaveTemplateDialog'
import { StyleDockChip } from '@/components/generate/StyleGallery'
import { useDockModels } from '@/components/generate/useDockModels'
import { useMagicPrompt } from '@/components/generate/useMagicPrompt'
import { useBrandChoice } from '@/hooks/useBrandKits'
import { useGenEstimate } from '@/hooks/useGenEstimate'
import { useImageOptions } from '@/hooks/useMedia'
import { useTemplates } from '@/hooks/useStudio'
import { withBrand } from '@/lib/brand'
import { localEstimate } from '@/lib/estimate'
import { GENERATE_MAX_COUNT, IMAGE_ASPECTS, imagePayload, placeholdersIn, type ImageForm } from '@/lib/images'
import { defaultSpeed, has } from '@/lib/models'
import { MENTION_REFS } from '@/lib/mentions'
import { findStyle } from '@/lib/styles'
import { imageFormFrom, isControlTemplate, isEditTemplate, type TemplateDraft } from '@/lib/templates'
import { TemplatePreview } from '@/features/templates/TemplatePreview'
import type { ImageGenerateRequest, Template } from '@/lib/types'
import { plural } from '@/lib/utils'
import { announce } from '@/stores/ui'
import { CountPicker, ImageAspectTiles } from './controls'

export interface ImageRun {
  form: ImageForm
  body: ImageGenerateRequest
  summary: string
}

interface Props {
  initial: ImageForm
  templateTitle?: string
  modelPicked?: boolean
  onRun: (run: ImageRun) => void
  pending?: boolean
  error?: unknown
}

const sizeOf = (aspect: string) => {
  const s = IMAGE_ASPECTS.find((a) => a.value === aspect)?.size.split('×').map(Number)
  return s ? { width: s[0], height: s[1] } : {}
}

export function ImageGenerateForm({ initial, templateTitle, modelPicked, onRun, pending, error }: Props) {
  const uid = useId()
  const field = useRef<HTMLTextAreaElement>(null)
  const [form, setForm] = useState<ImageForm>(initial)
  const [fromTemplate, setFromTemplate] = useState(templateTitle)
  const templates = useTemplates('image')
  const brand = useBrandChoice()
  const set = <K extends keyof ImageForm>(k: K, v: ImageForm[K]) => setForm((f) => ({ ...f, [k]: v }))
  const { models, model, send, effective } = useDockModels('image', form.model, { prompt: form.prompt, count: form.count })
  const speed = model?.speeds?.find((s) => s.id === form.speed && s.available !== false) ?? defaultSpeed(model)
  const magic = useMagicPrompt(form.prompt, { kind: 'image', model: send?.id, style: form.style, brandKitId: brand.sendId })
  const estimate = useGenEstimate(
    effective ? { kind: 'image', model: effective.id, ...(speed ? { speed: speed.id } : {}), count: form.count, ...sizeOf(form.aspect) } : null,
    localEstimate(effective, { count: form.count, speed: speed?.id }),
  )

  const slots = placeholdersIn(form.prompt)
  const blocked = slots.length ? `Replace ${slots.join(', ')} first.` : form.prompt.trim().length < 3 ? 'Describe the image first.' : null
  const options = useImageOptions()
  const styleLabel = findStyle(options.data?.styles ?? [], form.style, options.data?.style_aliases)?.label
  const [saving, setSaving] = useState<TemplateDraft | null>(null)
  const saveDraft = (): TemplateDraft => ({
    target: 'generate',
    prompt: form.prompt,
    title: fromTemplate,
    style: form.style,
    model: form.model ?? null,
    aspect: form.aspect,
    count: form.count,
    negative: form.negative,
  })

  const submit = () => {
    if (blocked || pending) return
    const body = withBrand({ ...imagePayload({ ...form, model: send?.id, speed: speed?.id }), ...magic.fields() }, brand.sendId)
    const summary = [model?.label, form.aspect, plural(form.count, 'image'), styleLabel, brand.sendId ? `Brand: ${brand.kit?.name}` : null].filter(Boolean).join(' · ')
    onRun({ form: { ...form, model: model?.id, speed: speed?.id }, body, summary })
  }

  // edit and control templates need pictures; they open their own pages from the gallery instead
  const usable = templates.data?.filter((t) => !isEditTemplate(t) && !isControlTemplate(t)) ?? []
  const mine = usable.filter((t) => t.mine)
  const menuItem = (t: Template) => (
    <DropdownMenuItem
      key={t.id}
      onSelect={() => {
        const next = imageFormFrom({ ...(t.defaults ?? {}), examples: t.examples }, t.id)
        // a text template brings Qwen along; otherwise keep the model the user picked
        setForm({ ...next, model: next.model ?? form.model, speed: next.model ? undefined : form.speed })
        setFromTemplate(t.title)
        announce(`Filled in from the ${t.title} prompt template. Replace the highlighted words, then create.`)
      }}
    >
      <TemplatePreview t={t} decorative className="size-12 shrink-0 rounded-[4px]" iconClassName="size-4" />
      <span className="flex min-w-0 flex-col">
        <span>{t.title}</span>
        <span className="line-clamp-2 text-small text-studio-muted">{t.description || String(t.defaults?.prompt_scaffold ?? '')}</span>
      </span>
    </DropdownMenuItem>
  )

  const templateMenu = (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button type="button" size="sm" variant="ghost">
          <LayoutTemplate aria-hidden />
          <span className="max-sm:sr-only">Start from a prompt template</span>
          <ChevronDown aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="max-h-[70vh] w-80 overflow-y-auto">
        {mine.length > 0 && (
          <DropdownMenuGroup aria-label="My templates">
            <DropdownMenuLabel>My templates</DropdownMenuLabel>
            {mine.map(menuItem)}
          </DropdownMenuGroup>
        )}
        <DropdownMenuGroup aria-label="Image prompt templates">
          <DropdownMenuLabel>Image prompt templates</DropdownMenuLabel>
          {templates.isPending && <p className="px-2 py-1.5 text-small text-studio-muted">Loading…</p>}
          {templates.isError && <p className="px-2 py-1.5 text-small text-studio-muted">Prompt templates aren't available right now.</p>}
          {usable.filter((t) => !t.mine).map(menuItem)}
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )

  return (
    <PromptDock
      into="image"
      title="Create Image"
      icon={<Wand2 aria-hidden className="size-4 text-studio-accent-hover" />}
      headerExtra={
        <>
          {fromTemplate && <span className="rounded-full border border-studio-gold/70 bg-studio-gold/10 px-2 text-small">Prompt template: {fromTemplate}</span>}
          <Button type="button" size="sm" variant="ghost" disabled={!form.prompt.trim()} onClick={() => setSaving(saveDraft())} aria-label="Save this prompt as a template">
            <BookmarkPlus aria-hidden />
            <span className="max-sm:sr-only">Save as template</span>
          </Button>
          <SaveTemplateDialog draft={saving} onOpenChange={(o) => !o && setSaving(null)} />
          {templateMenu}
        </>
      }
      promptLabel="Describe the image"
      prompt={form.prompt}
      onPrompt={(v) => set('prompt', v)}
      mentions={{ refBudget: MENTION_REFS }}
      placeholder="e.g. A weathered lighthouse keeper in a wool coat, golden hour, 85mm portrait"
      promptRef={field}
      promptHint={
        has(effective, 'text_render') ? (
          <>
            Put text in quotes, e.g. a poster that says <bdi>{'"عید مبارک"'}</bdi>
          </>
        ) : undefined
      }
      belowPrompt={<PlaceholderHint text={form.prompt} field={field} examples={form.examples} />}
      chips={
        <>
          <ModelChip
            models={models}
            model={model}
            onChange={(id) => setForm((f) => ({ ...f, model: id, speed: undefined }))}
            speed={speed?.id}
            onSpeed={(id) => set('speed', id)}
            highlighted={modelPicked}
          />
          <AspectChip value={form.aspect}>
            <ImageAspectTiles value={form.aspect} onChange={(v) => v && set('aspect', v)} />
          </AspectChip>
          <CountChip value={`×${form.count}`}>
            <CountPicker value={form.count} onChange={(n) => set('count', n)} max={GENERATE_MAX_COUNT} />
          </CountChip>
          <StyleDockChip
            value={form.style}
            onChange={(v) => set('style', v)}
            modelId={effective?.id}
            onUseModel={(id) => setForm((f) => ({ ...f, model: id, speed: undefined }))}
          />
          <DockChip name="References" value="Refs" icon={<ImagePlus aria-hidden />}>
            <p className="text-small text-studio-muted">Start from a picture you have. You can also drop or paste one anywhere on this page.</p>
            <div className="flex flex-wrap gap-2">
              <Button asChild size="sm" variant="secondary">
                <Link to="/image/edit">Edit with references</Link>
              </Button>
              <Button asChild size="sm" variant="secondary">
                <Link to="/image/img2img">Image to Image</Link>
              </Button>
            </div>
          </DockChip>
          <BrandDockChip choice={brand} />
        </>
      }
      advanced={
        <div className="grid gap-3 sm:grid-cols-[2fr_1fr_1fr]">
          <div>
            <Label htmlFor={`${uid}-neg`} className="mb-1.5">
              Avoid (negative prompt)
            </Label>
            <Input id={`${uid}-neg`} value={form.negative} onChange={(e) => set('negative', e.target.value)} placeholder="e.g. text, watermark, extra fingers" />
          </div>
          <div>
            <Label htmlFor={`${uid}-seed`} className="mb-1.5">
              Seed
            </Label>
            <Input id={`${uid}-seed`} inputMode="numeric" value={form.seed} onChange={(e) => setForm((f) => ({ ...f, seed: e.target.value.replace(/\D/g, ''), batchIndex: undefined }))} placeholder="Random" className="font-mono" />
          </div>
          <div>
            <Label htmlFor={`${uid}-steps`} className="mb-1.5">
              Steps
            </Label>
            <Input id={`${uid}-steps`} inputMode="numeric" value={form.steps} onChange={(e) => set('steps', e.target.value.replace(/\D/g, ''))} placeholder="Default" className="font-mono" />
          </div>
        </div>
      }
      magic={magic}
      error={error ? <ErrorState compact title="Couldn't start the images" error={error} /> : null}
      footer={<GenerateButton verb="Generate" what={plural(form.count, 'image')} estimate={estimate} icon={<Wand2 aria-hidden />} blocked={blocked} pending={pending} />}
      onSubmit={submit}
    />
  )
}
