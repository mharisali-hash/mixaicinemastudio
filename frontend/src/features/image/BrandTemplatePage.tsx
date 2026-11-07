import { useId, useRef, useState } from 'react'
import { Link, useLocation } from 'react-router'
import { Images, Stamp } from 'lucide-react'
import { fieldClass } from '@/components/ui/input'
import { Chip } from '@/components/studio/chip'
import { PlaceholderHint } from '@/components/studio/placeholder-hint'
import { EmptyState, ErrorState } from '@/components/studio/states'
import { AspectChip, CountChip } from '@/components/generate/DockChips'
import { GenerateButton } from '@/components/generate/GenerateButton'
import { GeneratorPage, ResultsHeading } from '@/components/generate/GeneratorPage'
import { PromptDock } from '@/components/generate/PromptDock'
import { SessionResults } from '@/components/generate/SessionResults'
import { useRecordRun } from '@/components/generate/useRecordRun'
import { useBrandKits } from '@/hooks/useBrandKits'
import { flatItems, useImageBrand, useMediaList } from '@/hooks/useMedia'
import { logoFor, type BrandKit, type LogoVariant } from '@/lib/brand'
import { placeholdersIn } from '@/lib/images'
import { brandFormFrom, fillSlot, surfaceFor, type TemplatePrefill } from '@/lib/templates'
import type { BrandImageRequest, BrandSurface, ImageAspect, MediaItem, TemplateOption } from '@/lib/types'
import { cn, plural } from '@/lib/utils'
import { announce } from '@/stores/ui'
import { CountPicker, ImageAspectTiles } from './controls'
import { EditSources } from './EditSources'
import { TemplateChoices } from './TemplateChoices'

const PAGE = 'image-brand'
type Mode = BrandImageRequest['mode']

interface BrandRun {
  body: BrandImageRequest
  summary: string
}

const VARIANT: Record<LogoVariant, string> = { primary: 'Main logo', light: 'Light logo', dark: 'Dark logo' }

function KitPanel({
  kits,
  kit,
  onKit,
  surface,
  wantsProduct,
  product,
  onProduct,
}: {
  kits: BrandKit[]
  kit: BrandKit
  onKit: (id: string) => void
  surface: BrandSurface
  wantsProduct: boolean
  product: string
  onProduct: (id: string) => void
}) {
  const uid = useId()
  const logo = logoFor(kit, surface)
  return (
    <section aria-labelledby={`${uid}-h`} className="flex flex-wrap items-start gap-3 rounded-[6px] border border-studio-border bg-studio-raised p-2">
      <div className="checker flex size-16 shrink-0 items-center justify-center overflow-hidden rounded-[6px] border border-studio-border">
        {logo?.url ? <img src={logo.url} alt={`${kit.name} logo`} className="max-h-[80%] max-w-[80%] object-contain" /> : <Stamp aria-hidden className="size-6 text-studio-muted" />}
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <h3 id={`${uid}-h`} className="section-label">
          Filled in from your brand kit
        </h3>
        {kits.length > 1 ? (
          <div className="flex items-center gap-2">
            <label htmlFor={`${uid}-kit`} className="text-small font-medium">
              Brand kit
            </label>
            <select id={`${uid}-kit`} value={kit.id} onChange={(e) => onKit(e.target.value)} className={cn(fieldClass, 'h-8 w-auto max-w-60 py-0 pr-7 text-small')}>
              {kits.map((k) => (
                <option key={k.id} value={k.id}>
                  {k.name}
                  {k.is_default ? ' (default)' : ''}
                </option>
              ))}
            </select>
          </div>
        ) : (
          <p className="font-display text-panel font-semibold">{kit.name}</p>
        )}
        {logo && (
          <p className="text-small text-studio-muted">
            {VARIANT[logo.variant]} goes in as picture 2
            {logo.relit ? '; on a dark surface its dark letters are made light' : ''}.
          </p>
        )}
        {kit.palette?.length > 0 && (
          <div className="flex gap-1" role="img" aria-label={`Brand colours: ${kit.palette.map((c) => c.name || c.hex).join(', ')}`}>
            {kit.palette.map((c) => (
              <span key={c.hex} className="h-3 w-6 rounded-[3px] border border-studio-border" style={{ background: c.hex }} />
            ))}
          </div>
        )}
        {kit.tagline && (
          <p dir="auto" className="text-small text-studio-muted">
            “{kit.tagline}”
          </p>
        )}
      </div>
      {wantsProduct && kit.products?.length > 0 && (
        <div className="flex flex-col gap-1">
          <label htmlFor={`${uid}-prod`} className="text-small font-medium">
            Product (optional)
          </label>
          <select id={`${uid}-prod`} value={product} onChange={(e) => onProduct(e.target.value)} className={cn(fieldClass, 'h-8 w-auto max-w-60 py-0 pr-7 text-small')}>
            <option value="">None</option>
            {kit.products.map((p) => (
              <option key={p.media_id} value={p.media_id}>
                {p.name}
              </option>
            ))}
          </select>
        </div>
      )}
    </section>
  )
}

function NeedsKit({ kit }: { kit: BrandKit | null }) {
  return (
    <div role="alert" className="flex flex-col gap-1 rounded-[6px] border border-studio-warning/60 bg-studio-raised p-3 text-small">
      <p className="font-medium">{kit ? `“${kit.name}” has no logo yet.` : 'You need a brand kit with your logo first.'}</p>
      <p className="text-studio-muted">This card puts your logo on the picture for you, so it needs one to work from.</p>
      <Link to={kit ? `/brand-kits/${kit.id}` : '/brand-kits'} className="font-medium text-studio-accent underline-offset-2 hover:underline">
        {kit ? `Add a logo to ${kit.name}` : 'Create a brand kit'}
      </Link>
    </div>
  )
}

/** A Brand template: the kit's logo goes in by itself; the user adds one photo, or lets the scene be made first. */
export function BrandTemplatePage() {
  const location = useLocation()
  const state = location.state as { template?: TemplatePrefill } | null
  const [tpl] = useState(() => (state?.template ? { id: state.template.templateId, title: state.template.templateTitle, ...brandFormFrom(state.template.prefill) } : null))
  const { kits, isPending: kitsLoading } = useBrandKits()
  const [kitId, setKitId] = useState<string | null>(null)
  const kit = kits.find((k) => k.id === kitId) ?? kits.find((k) => k.is_default) ?? kits[0] ?? null
  const [mode, setMode] = useState<Mode>('scene')
  const [photo, setPhoto] = useState('')
  const [product, setProduct] = useState('')
  const [instruction, setInstruction] = useState(tpl?.instruction ?? '')
  const [scene, setScene] = useState(tpl?.scenePrompt ?? '')
  const [picked, setPicked] = useState<Record<string, number>>({})
  const [count, setCount] = useState(tpl?.count ?? 1)
  const [aspect, setAspect] = useState<ImageAspect | null>(tpl?.sceneAspect ?? null)
  const field = useRef<HTMLTextAreaElement>(null)
  const make = useImageBrand()
  const results = useMediaList({ kind: 'image', origin: 'generated' })
  const record = useRecordRun<BrandRun>(PAGE)

  if (!tpl) {
    return (
      <GeneratorPage label="Brand template" targets={[]}>
        <EmptyState icon={<Stamp />} title="Pick a brand card first">
          Open <Link to="/image/prompt-templates" className="text-studio-accent underline">Prompt templates</Link> and choose one from Brand &amp; marketing.
        </EmptyState>
      </GeneratorPage>
    )
  }

  const surface = surfaceFor(tpl.options, picked, tpl.surface)
  const logo = kit ? logoFor(kit, surface) : null
  const sceneMode = mode === 'scene'
  const text = sceneMode ? scene : instruction
  const slots = [...new Set([...placeholdersIn(sceneMode ? scene : ''), ...placeholdersIn(instruction)])]

  const pick = (o: TemplateOption, i: number) => {
    const before = picked[o.slot] === undefined ? undefined : o.choices[picked[o.slot]]
    const value = o.choices[i].value
    // one choice fills the same [slot] in the scene and in the logo wording
    setScene((t) => fillSlot(t, o.slot, value, before?.value))
    setInstruction((t) => fillSlot(t, o.slot, value, before?.value))
    setPicked((p) => ({ ...p, [o.slot]: i }))
  }

  const blocked = kitsLoading
    ? 'Loading your brand kits…'
    : !kit || !logo
      ? 'Add a brand kit with your logo first.'
      : !sceneMode && !photo
        ? `Add the ${tpl.label.toLowerCase()} picture first.`
        : slots.length
          ? `Replace ${slots.join(', ')} first.`
          : text.trim().length < 3
            ? sceneMode
              ? 'Describe the scene.'
              : 'Say how the logo goes on.'
            : null

  const run = (r: BrandRun) =>
    make.mutate(r.body, {
      onSuccess: ({ items, jobs }) => {
        record({ prompt: r.body.scene_prompt ?? r.body.instruction ?? '', summary: r.summary, settings: r, items: items ?? [], jobs, label: plural(items?.length ?? 1, 'picture') })
        announce(sceneMode ? `Making the scene, then putting the ${kit?.name} logo on it.` : `Putting the ${kit?.name} logo on your photo.`)
      },
    })

  const submit = () => {
    if (blocked || make.isPending || !kit) return
    const body: BrandImageRequest = { template_id: tpl.id, mode, brand_kit_id: kit.id, surface, instruction, count }
    if (sceneMode) {
      body.scene_prompt = scene
      if (aspect) body.aspect = aspect
    } else body.source_id = photo
    if (product) body.product_id = product
    run({ body, summary: [kit.name, sceneMode ? 'scene + logo' : 'your photo + logo', plural(count, 'result')].join(' · ') })
  }

  return (
    <GeneratorPage
      label="Brand template"
      targets={[{ id: 'ref', label: tpl.label, hint: 'Brands this picture', use: (m) => {
        setMode('photo')
        setPhoto(m.id)
      } }]}
    >
      <PromptDock
        into="image"
        title={tpl.title}
        icon={<Stamp aria-hidden className="size-4 text-studio-accent-hover" />}
        headerExtra={<span className="rounded-full border border-studio-gold/70 bg-studio-gold/10 px-2 text-small">Brand template</span>}
        refs={
          <div className="flex flex-col gap-2">
            <div role="group" aria-label="How to start" className="flex flex-wrap gap-1">
              <Chip selected={sceneMode} onClick={() => setMode('scene')}>
                Make the scene for me
              </Chip>
              <Chip selected={!sceneMode} onClick={() => setMode('photo')}>
                Use my photo
              </Chip>
            </div>
            {kitsLoading ? null : kit && logo ? (
              <KitPanel
                kits={kits}
                kit={kit}
                onKit={(id) => {
                  setKitId(id)
                  setProduct('')
                }}
                surface={surface}
                wantsProduct={tpl.refs.includes('product')}
                product={product}
                onProduct={setProduct}
              />
            ) : (
              <NeedsKit kit={kit} />
            )}
            {!sceneMode && (
              <EditSources
                ids={[photo]}
                max={1}
                labels={[tpl.label]}
                required={1}
                onAdd={(items: MediaItem[]) => items[0] && setPhoto(items[0].id)}
                onRemove={() => setPhoto('')}
              />
            )}
          </div>
        }
        promptLabel={sceneMode ? 'Describe the scene' : 'How the logo goes on'}
        prompt={text}
        onPrompt={sceneMode ? setScene : setInstruction}
        promptRef={field}
        belowPrompt={
          <div className="flex flex-col gap-2">
            <TemplateChoices options={tpl.options} picked={picked} onPick={pick} />
            <PlaceholderHint text={text} field={field} examples={tpl.examples} />
            {sceneMode && (
              <details className="rounded-[6px] border border-studio-border bg-studio-raised px-2 py-1.5 text-small">
                <summary className="cursor-pointer font-medium">Then the logo goes on like this</summary>
                <label className="sr-only" htmlFor="brand-logo-wording">
                  Logo wording
                </label>
                <textarea
                  id="brand-logo-wording"
                  value={instruction}
                  onChange={(e) => setInstruction(e.target.value)}
                  rows={4}
                  className={cn(fieldClass, 'mt-1.5 h-auto py-1.5 text-small')}
                />
              </details>
            )}
            <p className="text-[12px] text-studio-muted">
              Words in {'{braces}'} come from your brand kit: the name, the logo, the colours and the tagline.
            </p>
          </div>
        }
        mentions={false}
        placeholder={sceneMode ? 'The scene, with a plain surface where the logo will go' : 'Where and how the logo goes on your photo'}
        chips={
          <>
            {sceneMode && (
              <AspectChip value={aspect ?? tpl.sceneAspect}>
                <ImageAspectTiles value={aspect} onChange={setAspect} />
              </AspectChip>
            )}
            <CountChip value={`×${count}`}>
              <CountPicker value={count} onChange={setCount} />
            </CountChip>
          </>
        }
        error={make.isError ? <ErrorState compact title="Couldn't start" error={make.error} /> : null}
        footer={
          <GenerateButton
            verb={sceneMode ? 'Make' : 'Brand'}
            estimate={null}
            what={plural(count, 'picture')}
            icon={<Stamp aria-hidden />}
            blocked={blocked}
            note={sceneMode ? 'Makes the scene first, then adds your logo. Both stay as versions of one picture.' : 'The result is a new image; your photo stays as it is.'}
            pending={make.isPending}
          />
        }
        onSubmit={submit}
      />
      <ResultsHeading>Your images</ResultsHeading>
      <SessionResults<BrandRun>
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
        onReuse={(row) => {
          const b = row.request?.settings.body
          if (b?.scene_prompt) setScene(b.scene_prompt)
          if (b?.instruction) setInstruction(b.instruction)
        }}
        onUseAsRef={(m) => {
          setMode('photo')
          setPhoto(m.id)
        }}
        refLabel={`Use as ${tpl.label.toLowerCase()}`}
        empty={
          <EmptyState icon={<Images />} title="Nothing branded yet">
            Pick a card option, then make it; the results land here.
          </EmptyState>
        }
      />
    </GeneratorPage>
  )
}
