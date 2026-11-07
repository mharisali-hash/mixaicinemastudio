import { lazy, Suspense, useId, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { Box, Boxes, Eraser, Heart, Rotate3d } from 'lucide-react'
import { ImageSourceField } from '@/components/media/ImageSourceField'
import { TILE_BTN } from '@/components/generate/TileActions'
import { Button } from '@/components/ui/button'
import { EmptyState, ErrorState } from '@/components/studio/states'
import { SwitchRow } from '@/components/studio/switch-row'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { GenerateButton } from '@/components/generate/GenerateButton'
import { GeneratorPage, ResultsHeading } from '@/components/generate/GeneratorPage'
import { MagicPromptBar } from '@/components/generate/MagicPromptBar'
import { useMagicPrompt } from '@/components/generate/useMagicPrompt'
import { useCharacterSheet } from '@/hooks/useCharacterStudio'
import { useGenEstimate } from '@/hooks/useGenEstimate'
import { flatItems, useMediaList } from '@/hooks/useMedia'
import { useMake3D } from '@/hooks/useModel3D'
import { useModels } from '@/hooks/useModels'
import { useFeature } from '@/hooks/useUsage'
import { betterBackOn, blocked3d, ENGINES, engineLock, isModelReady, local3dEstimate, logoEstimate, make3dPayload, offersBetterBack, QUALITIES, sheetViews, STARTS, startFromQuery, VIEWS, type Form3D } from '@/lib/model3d'
import type { Engine3D, Make3DResponse, MediaItem, Quality3D, Start3D } from '@/lib/types'
import { cn } from '@/lib/utils'
import { announce } from '@/stores/ui'
import { ModelMenu } from './ModelMenu'
import type { ViewerAsk } from './ModelViewer'
import { CharacterPick, LogoOptions, StepsFirst, ViewSlots, type MadeFirst } from './StartFields'
import { useModelActions, type ModelHandlers } from './useModelActions'

// three.js only arrives with the first model on screen
const ModelViewer = lazy(() => import('./ModelViewer').then((m) => ({ default: m.ModelViewer })))

const LIST = { kind: 'model' as const }

export function Studio3DPage() {
  const uid = useId()
  const [params] = useSearchParams()
  const [form, setForm] = useState<Form3D>(() => startFromQuery(params))
  const set = <K extends keyof Form3D>(k: K, v: Form3D[K]) => setForm((f) => ({ ...f, [k]: v }))
  const [notes, setNotes] = useState<string[]>([])
  const [picked, setPicked] = useState<string | null>(null)
  const [steps, setSteps] = useState<{ parts: MadeFirst[]; model: Make3DResponse['job']; after: string } | null>(null)
  const [ask, setAsk] = useState<ViewerAsk | null>(null)
  const viewerBox = useRef<HTMLDivElement>(null)
  const yours = useRef<HTMLHeadingElement>(null)
  // a tile's turntable / thumbnail runs in the viewer, so the model opens there first
  const openInViewer = (m: MediaItem, action?: ViewerAsk['action']) => {
    setPicked(m.id)
    if (action) setAsk({ n: Date.now(), action })
    viewerBox.current?.scrollIntoView?.({ block: 'nearest' })
  }
  const on3d = useFeature('3d')
  const onAds = useFeature('ads')
  const shotsOn = on3d && onAds
  const actions = useModelActions({
    open: (m) => openInViewer(m),
    turntable: (m) => openInViewer(m, 'turntable'),
    thumbnail: (m) => openInViewer(m, 'thumbnail'),
    shots: shotsOn ? (m) => openInViewer(m, 'shots') : undefined,
    // the tile that had focus is gone; land on the list's heading rather than the top of the page
    afterDelete: () => yours.current?.focus(),
  })

  const make = useMake3D()
  const magic = useMagicPrompt(form.prompt, { kind: 'image' })
  const sheet = useCharacterSheet(form.from === 'character' ? (form.characterId ?? undefined) : undefined)
  const characterViews = form.from === 'character' ? sheetViews(sheet.data) : []
  const logo = form.from === 'logo'
  const backOffered = offersBetterBack(form, characterViews)
  const back = betterBackOn(form, characterViews)
  const lock = engineLock(form, characterViews)
  const { models: engines } = useModels('model3d')
  const engineId: Engine3D = lock ? 'pixal3d' : form.engine
  const engine = engines.find((m) => m.id === engineId)
  const viewsWhy = engine && !engine.capabilities.includes('multiview') ? 'Pixal3D multi-view is not installed' : null
  const estimate = useGenEstimate(
    logo ? { kind: 'model3d', model: 'logo' } : { kind: 'model3d', model: engineId, speed: form.quality, ...(back ? { better_back: true } : {}) },
    logo ? logoEstimate() : local3dEstimate(engine, engineId, form.quality, back),
  )
  const results = useMediaList(LIST)
  const items = flatItems(results.data)
  const ready = items.filter(isModelReady)
  const shown = ready.find((m) => m.id === picked) ?? ready[0] ?? null

  const why = blocked3d(form, logo ? undefined : engine, viewsWhy)
  const quality = QUALITIES.find((q) => q.id === form.quality)

  // the server's own default is off, so the dock says which way the toggle points
  const textFields = () => {
    const f = magic.fields()
    return f.prompt_enhanced ? f : { ...f, magic_prompt: f.magic_prompt ?? magic.mode }
  }

  const submit = () => {
    const body = make3dPayload(form, form.from === 'text' ? textFields() : undefined, !!lock, characterViews)
    if (why || make.isPending || !body) return
    make.mutate(body, {
      onSuccess: (res) => {
        setNotes([res.background_note, res.engine_note, res.better_back_note].filter((n): n is string => !!n))
        const first = stepsOf(res)
        setSteps(first)
        setPicked(null)
        announce(first ? `Making ${first.what} first, then your 3D model.` : 'Making your 3D model. It appears below when it finishes.')
        // the picture, views or logo stay; only typed words are cleared once queued
        if (form.from === 'text') setForm((f) => ({ ...f, prompt: '' }))
      },
    })
  }

  const targets =
    form.from === 'views'
      ? VIEWS.map((v) => ({ id: v.id, label: `${v.label} view`, hint: 'One side of the object', use: (m: MediaItem) => setForm((f) => ({ ...f, views: { ...f.views, [v.id]: m.id } })) }))
      : form.from === 'logo'
        ? [{ id: 'logo', label: 'Logo', hint: 'Turned into a 3D badge', use: (m: MediaItem) => setForm((f) => ({ ...f, logoId: m.id })) }]
        : [{ id: 'picture', label: 'Picture', hint: 'Turned into a 3D model', use: (m: MediaItem) => setForm((f) => ({ ...f, from: 'picture', sourceId: m.id })) }]

  return (
    <GeneratorPage label="3D Studio" targets={targets}>
      <form
        aria-labelledby={`${uid}-title`}
        onSubmit={(e) => {
          e.preventDefault()
          submit()
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
            e.preventDefault()
            submit()
          }
        }}
        className="flex flex-col gap-4 rounded-[8px] border border-studio-border-strong bg-studio-panel p-3 shadow-card md:p-4"
      >
        <div className="flex flex-wrap items-center gap-2">
          <Box aria-hidden className="size-4 text-studio-accent-hover" />
          <h1 id={`${uid}-title`} className="font-display text-panel font-semibold md:text-title">
            3D Studio
          </h1>
          <p className="text-small text-studio-muted max-lg:hidden">One object, whole in frame, on a plain background works best.</p>
        </div>

        <fieldset className="flex flex-col gap-1.5">
          <legend className="section-label mb-1.5">From</legend>
          <ToggleGroup type="single" aria-label="From" value={form.from} onValueChange={(v) => v && set('from', v as Start3D)} className="flex w-full max-w-lg">
            {/* five across a phone: a little less padding each so they fit */}
            {STARTS.map((s) => (
              <ToggleGroupItem key={s.id} value={s.id} className="flex-1 px-2 sm:px-3">
                {s.label}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </fieldset>

        {/* the options keep their width; the start fields take the rest, and drop under below ~1100 px */}
        <div className="grid min-w-0 gap-4 min-[1100px]:grid-cols-[minmax(0,1fr)_18rem]">
          {form.from === 'picture' && (
            <ImageSourceField
              label="Picture"
              required
              value={form.sourceId}
              onChange={(id) => set('sourceId', id)}
              aspectClass="aspect-square"
              removeLabel="Remove the picture"
              pickDescription="Pick the picture to turn into a 3D model."
            />
          )}
          {form.from === 'text' && (
            <div className="flex w-full min-w-0 max-w-96 flex-col gap-2">
              <label htmlFor={`${uid}-p`} className="section-label">
                Describe one object
              </label>
              <Textarea
                id={`${uid}-p`}
                dir="auto"
                rows={3}
                value={form.prompt}
                onChange={(e) => set('prompt', e.target.value)}
                placeholder="A brass oil lamp with a glass chimney"
                aria-describedby={`${uid}-ph`}
              />
              <p id={`${uid}-ph`} className="text-small text-studio-muted">
                A picture of it is made first, on a plain background, then the model.
              </p>
              <MagicPromptBar magic={magic} canPreview={form.prompt.trim().length >= 3} />
            </div>
          )}
          {form.from === 'character' && (
            <CharacterPick value={form.characterId} onChange={(id) => set('characterId', id)} views={characterViews} sheetLoading={sheet.isPending && !!form.characterId} />
          )}
          {form.from === 'views' && <ViewSlots value={form.views} onChange={(v, id) => setForm((f) => ({ ...f, views: { ...f.views, [v]: id } }))} />}
          {logo && (
            <div className="flex min-w-0 flex-col gap-2">
              <ImageSourceField
                label="Logo"
                required
                value={form.logoId}
                onChange={(id) => set('logoId', id)}
                aspectClass="aspect-square"
                removeLabel="Remove the logo"
                pickDescription="Pick the logo, sign, icon or sticker to turn into a 3D badge."
              />
              <p className="max-w-96 text-small text-studio-muted">Crisp letters and edges — best for logos, signs, icons, stickers. A see-through PNG works best.</p>
            </div>
          )}

          <div className="flex min-w-0 flex-col gap-3 min-[1100px]:w-72">
            {logo ? (
              <LogoOptions value={form.logo} onChange={(v) => set('logo', v)} />
            ) : (
              <>
                <fieldset className="flex min-w-0 flex-col gap-1.5" aria-describedby={lock ? `${uid}-lock` : undefined}>
                  <legend className="section-label mb-1.5">Engine</legend>
                  <ToggleGroup type="single" aria-label="Engine" value={engineId} onValueChange={(v) => v && set('engine', v as Engine3D)} className="flex w-full max-w-md">
                    {ENGINES.map((e) => {
                      const info = engines.find((m) => m.id === e.id)
                      return (
                        <ToggleGroupItem
                          key={e.id}
                          value={e.id}
                          disabled={(info ? !info.available : false) || (!!lock && e.id !== 'pixal3d')}
                          aria-label={`${e.label}, ${e.hint.toLowerCase()}`}
                          className="h-auto py-1.5"
                        >
                          <span className="flex flex-col items-center leading-tight">
                            <span className="font-medium">{e.label}</span>
                            <span className="text-small opacity-80">{e.hint}</span>
                          </span>
                        </ToggleGroupItem>
                      )
                    })}
                  </ToggleGroup>
                  {lock && (
                    <p id={`${uid}-lock`} className="text-small text-studio-muted">
                      {lock}
                    </p>
                  )}
                </fieldset>

                <fieldset className="flex flex-col gap-1.5">
                  <legend className="section-label mb-1.5">Quality</legend>
                  <ToggleGroup type="single" aria-label="Quality" aria-describedby={`${uid}-q`} value={form.quality} onValueChange={(v) => v && set('quality', v as Quality3D)} className="w-full max-w-xs">
                    {QUALITIES.map((q) => (
                      <ToggleGroupItem key={q.id} value={q.id}>
                        {q.label}
                      </ToggleGroupItem>
                    ))}
                  </ToggleGroup>
                  <p id={`${uid}-q`} className="text-small text-studio-muted">
                    {quality?.hint}
                  </p>
                </fieldset>
              </>
            )}

            {backOffered && (
              <SwitchRow
                id={`${uid}-back`}
                checked={back}
                onChange={(v) => setForm((f) => ({ ...f, betterBack: { ...f.betterBack, [f.from === 'picture' ? 'picture' : 'character']: v } }))}
                icon={<Rotate3d aria-hidden className="size-3.5" />}
                title="Better back"
                hint={form.from === 'picture' ? 'Makes side and back views first, +1–2 min. Turn on for people and figures.' : 'Makes side and back views first, +1–2 min, so the back isn’t a copy of the front.'}
                className="max-w-md"
              />
            )}

            {form.from !== 'text' && (
              <SwitchRow
                id={`${uid}-bg`}
                checked={form.removeBackground}
                onChange={(v) => set('removeBackground', v)}
                icon={<Eraser aria-hidden className="size-3.5" />}
                title="Remove background"
                hint={
                  logo
                    ? 'Cuts the logo out first. Turn off for a logo on a plain colour: that colour is taken away instead.'
                    : `Cuts the object out first. Turn off only for ${form.from === 'views' ? 'views that are' : 'a picture that is'} already see-through.`
                }
                className="max-w-md"
              />
            )}
          </div>
        </div>

        {make.isError && <ErrorState compact title="Couldn't start the model" error={make.error} />}
        {notes.map((n) => (
          <p key={n} role="status" className="text-small text-studio-warning">
            {n}
          </p>
        ))}
        {steps && <StepsFirst parts={steps.parts} modelJob={steps.model} after={steps.after} />}
        <GenerateButton
          verb="Make 3D"
          what={logo ? 'badge' : `${quality?.label ?? 'Final'} model`}
          estimate={estimate}
          icon={<Box aria-hidden />}
          blocked={why}
          note={noteFor(form.from, back)}
          pending={make.isPending}
        />
      </form>

      <ResultsHeading>Viewer</ResultsHeading>
      {shown?.media_url ? (
        <div ref={viewerBox} className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <p dir="auto" className="min-w-0 flex-1 truncate font-medium">
              {shown.title || '3D model'}
            </p>
            <FavouriteButton item={shown} h={actions.handlers} />
            <ModelMenu item={shown} h={actions.handlers} inViewer triggerClassName={cn(TILE_BTN, 'size-8')} />
          </div>
          <Suspense fallback={<Skeleton className="aspect-[4/3] w-full rounded-[8px]" />}>
            <ModelViewer mediaId={shown.id} url={shown.media_url} title={shown.title || '3D model'} ask={ask} onAsked={() => setAsk(null)} />
          </Suspense>
        </div>
      ) : (
        <div className="rounded-[8px] border border-studio-border bg-studio-raised">
          <EmptyState icon={<Box />} title={items.length ? 'Your model is on its way' : 'No 3D models yet'}>
            {items.length ? 'It opens here as soon as it finishes.' : 'Add a picture and press Make 3D. You can turn it, light it and download it here.'}
          </EmptyState>
        </div>
      )}

      <h2 ref={yours} tabIndex={-1} className="section-label -mb-3 focus-visible:outline-none">
        Your models
      </h2>
      {actions.error && <ErrorState compact title="That didn’t work" error={actions.error} />}
      {results.isError ? (
        <ErrorState title="Couldn't load your models" error={results.error} onRetry={() => results.refetch()} />
      ) : results.isPending ? (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className="aspect-square" />
          ))}
        </div>
      ) : items.length ? (
        <ul className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3 pb-8">
          {items.map((m) => (
            <li key={m.id}>
              <ModelTile item={m} selected={m.id === shown?.id} onView={() => setPicked(m.id)} h={actions.handlers} />
            </li>
          ))}
        </ul>
      ) : (
        <p className="pb-8 text-small text-studio-muted">Finished models collect here.</p>
      )}
      {results.hasNextPage && (
        <button type="button" className="self-center pb-8 text-small underline" onClick={() => results.fetchNextPage()}>
          Show more
        </button>
      )}
      {actions.host}
    </GeneratorPage>
  )
}

const DOWNLOADS = 'You get a GLB, plus OBJ, STL and USDZ downloads.'

function noteFor(from: Start3D, back: boolean) {
  if (from === 'text') return `A picture is made first, then the model. ${DOWNLOADS}`
  if (from === 'logo') return `Made in seconds, with the artwork on the front. ${DOWNLOADS}`
  if (back) return `The side and back views are made first, then the model. ${DOWNLOADS}`
  return DOWNLOADS
}

/** The jobs that run before the model, for the progress list; null when the model runs straight away. */
function stepsOf(res: Make3DResponse): { parts: MadeFirst[]; model: Make3DResponse['job']; after: string; what: string } | null {
  if (res.picture_job) return { parts: [{ label: 'Picture', job: res.picture_job }], model: res.job, after: 'After the picture', what: 'the picture' }
  if (res.back_jobs?.length) {
    const parts = res.back_jobs.map((job, i) => ({ label: i ? 'Back view' : 'Side view', job }))
    return { parts, model: res.job, after: 'After the views', what: 'the side and back views' }
  }
  if (res.cutout_job) return { parts: [{ label: 'Cut-out', job: res.cutout_job }], model: res.job, after: 'After the cut-out', what: 'the cut-out' }
  return null
}

function FavouriteButton({ item, h }: { item: MediaItem; h: ModelHandlers }) {
  const on = h.isFavourite(item)
  return (
    <Button type="button" size="icon-sm" variant="ghost" aria-pressed={on} aria-label="Favourite" title={on ? 'Remove from favourites' : 'Favourite'} onClick={() => h.favourite(item)}>
      <Heart aria-hidden className={cn(on && 'fill-current text-studio-accent')} />
    </Button>
  )
}

const STATUS: Record<string, string> = {
  queued: 'Waiting',
  generating: 'Making…',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

function ModelTile({ item, selected, onView, h }: { item: MediaItem; selected: boolean; onView: () => void; h: ModelHandlers }) {
  const done = isModelReady(item)
  const label = STATUS[item.status ?? ''] ?? null
  return (
    <div className="relative">
      <button
        type="button"
        onClick={onView}
        disabled={!done}
        aria-pressed={done ? selected : undefined}
        aria-label={`${item.title || '3D model'}${label ? `, ${label.toLowerCase()}` : ''}`}
        className={cn(
          'group flex w-full flex-col overflow-hidden rounded-[8px] border bg-studio-panel text-left shadow-card transition-colors',
          selected ? 'border-studio-accent ring-2 ring-studio-accent/40' : 'border-studio-border-strong hover:border-studio-border-hover',
          !done && 'cursor-default',
        )}
      >
        <span className="relative flex aspect-square items-center justify-center bg-studio-raised">
          {item.thumb_url ? <img src={item.thumb_url} alt="" loading="lazy" className="size-full object-contain p-2" /> : <Boxes aria-hidden className="size-7 text-studio-faint" />}
          {label && <span className="absolute left-1.5 top-1.5 rounded-[4px] bg-studio-panel/90 px-1.5 text-small text-studio-muted">{label}</span>}
        </span>
        <span className="truncate px-2 py-1.5 text-small">{item.title || '3D model'}</span>
      </button>
      {/* beside the tile's button, not in it: no button inside a button */}
      <ModelMenu item={item} h={h} triggerClassName={cn(TILE_BTN, 'absolute right-1.5 top-1.5')} />
    </div>
  )
}
