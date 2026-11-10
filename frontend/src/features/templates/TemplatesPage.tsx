import { useId, useMemo, useState } from 'react'
import { useNavigate } from 'react-router'
import { ArrowRight, LayoutTemplate, MoreHorizontal, Pencil, Search, Trash2 } from 'lucide-react'
import { ConfirmDialog } from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Chip } from '@/components/studio/chip'
import { EmptyState, ErrorState } from '@/components/studio/states'
import { useImageTools } from '@/hooks/useMedia'
import { useDeleteTemplate, useRenameTemplate, useStartTemplate, useTemplates } from '@/hooks/useStudio'
import { splitPlaceholders } from '@/lib/images'
import { isBrandTemplate, isControlTemplate, isEditTemplate, localStart, TARGET_ROUTE, type TemplatePrefill } from '@/lib/templates'
import { announce } from '@/stores/ui'
import { AUDIO_ROUTE, audioKindOf, KIND_NOUN } from '@/lib/audio'
import type { Template, TemplateStart, TemplateType } from '@/lib/types'
import { formatRuntime } from '@/lib/utils'
import { TemplatePreview } from './TemplatePreview'

function Highlighted({ text, examples }: { text: string; examples?: Record<string, string> }) {
  return (
    <>
      {splitPlaceholders(text).map((p, i) => {
        if (!p.slot) return <span key={i}>{p.text}</span>
        const ex = examples?.[p.text.slice(1, -1)]
        return (
          <mark key={i} title={ex ? `e.g. ${ex}` : undefined} className="rounded-[3px] bg-studio-gold/30 px-0.5 text-studio-text">
            {p.text}
          </mark>
        )
      })}
    </>
  )
}

function meta(t: Template) {
  const d = t.defaults ?? {}
  const bits: string[] = []
  const secs = (d.duration_s ?? d.target_runtime_s) as number | undefined
  if (typeof secs === 'number') bits.push(formatRuntime(secs))
  const aspect = (d.aspect_ratio ?? d.aspect) as string | undefined
  if (aspect) bits.push(aspect)
  if (t.type === 'video') bits.push(d.authoring_mode && d.authoring_mode !== 'quick' ? 'Studio project' : 'Quick video')
  else if (t.type === 'audio') bits.push(KIND_NOUN[audioKindOf(t)].one)
  else if (isBrandTemplate(t)) bits.push('Brand · your logo goes in by itself')
  else if (isEditTemplate(t)) bits.push(`Edit · needs ${(t.ref_labels ?? []).join(' + ') || 'a photo'}`)
  else if (isControlTemplate(t)) bits.push(t.control?.as_is ? 'Needs your sketch' : 'Needs a photo or map')
  else if (typeof d.count === 'number') bits.push(`${d.count} variations`)
  if (d.model === 'qwen_image_2512') bits.push('Qwen text')
  return bits.join(' · ')
}

const scaffoldOf = (t: Template) =>
  String(t.scene?.prompt_scaffold ?? t.defaults?.prompt_scaffold ?? t.defaults?.instruction_scaffold ?? t.defaults?.logline_hint ?? '')

const haystack = (t: Template) =>
  [t.title, t.description, t.category, ...(t.tags ?? []), ...(t.ref_labels ?? []), scaffoldOf(t)].join(' ').toLowerCase()

type Mode = 'generate' | 'edit'
const MODES: { value: Mode; label: string }[] = [
  { value: 'generate', label: 'Generate' },
  { value: 'edit', label: 'Edit my photo' },
]
// sketch, depth and pose cards start from the user's own picture too
const fromPicture = (t: Template) => isEditTemplate(t) || isControlTemplate(t)
// brand cards do both: the user's photo, or a scene made for them
const inModeOf = (t: Template, m: Mode | null) => !m || isBrandTemplate(t) || (m === 'edit') === fromPicture(t)

function MineMenu({ t }: { t: Template }) {
  const uid = useId()
  const rename = useRenameTemplate()
  const remove = useDeleteTemplate()
  const [renaming, setRenaming] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [title, setTitle] = useState(t.title)
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button size="sm" variant="ghost" aria-label={`More for ${t.title}`}>
            <MoreHorizontal aria-hidden />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem
            onSelect={() => {
              setTitle(t.title)
              rename.reset()
              setRenaming(true)
            }}
          >
            <Pencil aria-hidden />
            Rename…
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => setDeleting(true)} className="text-studio-danger">
            <Trash2 aria-hidden />
            Delete…
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <Dialog open={renaming} onOpenChange={setRenaming}>
        <DialogContent className="max-w-md">
          <form
            className="flex flex-col gap-4"
            onSubmit={(e) => {
              e.preventDefault()
              if (!title.trim()) return
              rename.mutate(
                { id: t.id, title: title.trim() },
                {
                  onSuccess: (r) => {
                    setRenaming(false)
                    announce(`Renamed to ${r.title}.`)
                  },
                },
              )
            }}
          >
            <DialogHeader className="mb-0">
              <DialogTitle>Rename template</DialogTitle>
              <DialogDescription>Everyone in this workspace sees the new name.</DialogDescription>
            </DialogHeader>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={`${uid}-n`}>Name</Label>
              <Input id={`${uid}-n`} value={title} maxLength={120} autoFocus onChange={(e) => setTitle(e.target.value)} />
              {rename.isError && (
                <p role="alert" className="text-small text-studio-danger">
                  {rename.error.message}
                </p>
              )}
            </div>
            <DialogFooter className="mt-0">
              <Button type="button" variant="ghost" onClick={() => setRenaming(false)}>
                Cancel
              </Button>
              <Button type="submit" variant="primary" disabled={!title.trim()} loading={rename.isPending}>
                Save name
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
      <ConfirmDialog
        open={deleting}
        onOpenChange={setDeleting}
        title={`Delete “${t.title}”?`}
        description="It goes from My templates for everyone in this workspace. Pictures made with it stay in the Library."
        confirmLabel="Delete"
        tone="danger"
        onConfirm={() => remove.mutate(t.id, { onSuccess: () => announce(`Deleted the ${t.title} template.`) })}
      />
    </>
  )
}

function Card({ t, onStart, starting }: { t: Template; onStart: () => void; starting: boolean }) {
  const prompt = scaffoldOf(t)
  return (
    <article aria-labelledby={`tpl-${t.id}`} className="flex flex-col overflow-hidden rounded-[8px] border border-studio-border-strong bg-studio-panel shadow-card">
      <TemplatePreview t={t} className="aspect-[4/3] border-x-0 border-t-0" />
      <div className="flex flex-1 flex-col gap-2 p-3">
        <div className="flex items-start justify-between gap-2">
          <div className="flex min-w-0 flex-col gap-2">
            {t.category && <p className="section-label">{t.category}</p>}
            <h2 id={`tpl-${t.id}`} className="font-display text-panel font-semibold">
              {t.title}
            </h2>
          </div>
          {t.mine && <MineMenu t={t} />}
        </div>
        <p className="text-small text-studio-muted">{t.description}</p>
        {prompt && (
          <p className="line-clamp-3 rounded-[6px] border border-studio-border bg-studio-raised px-2 py-1.5 text-small">
            <Highlighted text={prompt} examples={t.examples} />
          </p>
        )}
        {t.control && (
          <p className="text-[12px] text-studio-muted">
            Runs on Z-Image Turbo + Fun ControlNet when installed, else the edit model
          </p>
        )}
        {/* Remove… says what it does in its description; its engine has no name worth showing */}
        {t.tool && t.tool.id !== 'remove' && (
          <p className="text-[12px] text-studio-muted">
            Runs on {t.tool.engine}
            {t.tool.fallback ? ' when installed' : ''}
          </p>
        )}
        <p className="mt-auto font-mono text-[12px] text-studio-muted">{meta(t)}</p>
        <Button variant="primary" onClick={onStart} loading={starting} aria-label={`Use the ${t.title} prompt template`}>
          {isBrandTemplate(t) ? 'Use with my brand' : isEditTemplate(t) ? 'Use with my photos' : isControlTemplate(t) ? 'Use with my picture' : 'Use prompt template'}
          <ArrowRight aria-hidden />
        </Button>
      </div>
    </article>
  )
}

/** Prompt template cards. Starting one only opens a prefilled form; nothing is generated until the user confirms. */
const NOUN: Record<TemplateType, string> = { video: 'Video prompt templates', image: 'Image prompt templates', audio: 'Audio prompt templates' }

// audio templates open the Song, Music or SFX page; the server's target alone can't say which
const routeFor = (t: Template, s: TemplateStart) =>
  t.type === 'audio' || s.target === 'audio' ? AUDIO_ROUTE[audioKindOf({ category: t.category, defaults: { ...t.defaults, ...(s.prefill ?? {}) } })] : (TARGET_ROUTE[s.target] ?? TARGET_ROUTE.quick)

export function TemplatesPage({ type }: { type: TemplateType }) {
  const navigate = useNavigate()
  const templates = useTemplates(type)
  const start = useStartTemplate()
  const [q, setQ] = useState('')
  const [category, setCategory] = useState<string | null>(null)
  const [mode, setMode] = useState<Mode | null>(null)

  const tools = useImageTools(type === 'image')
  // a tool-only card (try-on, depth map) whose tool this server can't run is left out; the server already
  // drops the ones switched off by licence. Until the tool list answers, cards stay.
  const all = useMemo(
    () => templates.data?.filter((t) => !t.tool || t.tool.fallback || !tools.isSuccess || tools.data.some((x) => x.id === t.tool?.id && x.available)),
    [templates.data, tools.isSuccess, tools.data],
  )
  const hasEdits = !!all?.some((t) => fromPicture(t) || isBrandTemplate(t))
  const inMode = useMemo(() => (all ?? []).filter((t) => inModeOf(t, mode)), [all, mode])
  // the workspace's own get a section of their own; the rest come in category order from the server
  const shipped = useMemo(() => inMode.filter((t) => !t.mine), [inMode])
  const categories = useMemo(() => [...new Set(shipped.map((t) => t.category).filter((c): c is string => !!c))], [shipped])
  const [shown, mine] = useMemo(() => {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean)
    const hit = (t: Template) => words.every((w) => haystack(t).includes(w))
    return [shipped.filter((t) => (!category || t.category === category) && hit(t)), inMode.filter((t) => t.mine && hit(t))]
  }, [shipped, inMode, q, category])
  const chooseMode = (m: Mode | null) => {
    setMode(m)
    // a category from the other mode would leave nothing on screen
    if (category && !all?.some((t) => t.category === category && inModeOf(t, m))) setCategory(null)
  }

  const open = (t: Template) =>
    start.mutate(t.id, {
      onSettled: (res, err) => {
        // an older server without /start: the template's defaults carry the same prefill
        const s = res ?? (err ? localStart(t) : undefined)
        if (!s) return
        const template: TemplatePrefill = { templateId: t.id, templateTitle: t.title, prefill: { examples: t.examples, ...(s.prefill ?? {}) } }
        navigate(routeFor(t, s), { state: { template } })
      },
    })

  const noun = NOUN[type]
  return (
    <main data-f6-region tabIndex={-1} className="relative h-full overflow-y-auto focus-visible:outline-none" aria-labelledby="tpl-title">
      <div className="mx-auto flex max-w-7xl flex-col gap-5 px-4 py-6 md:px-8">
        <header className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 id="tpl-title" className="flex items-center gap-2 font-display text-title font-semibold">
              <LayoutTemplate aria-hidden className="size-5 text-studio-accent" />
              {noun}
            </h1>
            <p className="text-body text-studio-muted">
              Ready-made prompts with the settings filled in. Swap the <mark className="rounded-[3px] bg-studio-gold/30 px-0.5">[highlighted]</mark> words for
              your own before you create.
            </p>
          </div>
          <div className="relative">
            <Search aria-hidden className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-studio-muted" />
            <Input type="search" placeholder="Search prompt templates" aria-label="Search prompt templates" value={q} onChange={(e) => setQ(e.target.value)} className="w-64 pl-8" />
          </div>
        </header>
        {hasEdits && (
          <div role="group" aria-label="Generate or edit" className="flex flex-wrap gap-1">
            <Chip selected={mode === null} onClick={() => chooseMode(null)}>
              Everything
            </Chip>
            {MODES.map((m) => (
              <Chip key={m.value} selected={mode === m.value} onClick={() => chooseMode(mode === m.value ? null : m.value)}>
                {m.label}
              </Chip>
            ))}
          </div>
        )}
        {categories.length > 1 && (
          <div role="group" aria-label="Category" className="flex flex-wrap gap-1">
            <Chip selected={category === null} onClick={() => setCategory(null)}>
              All
            </Chip>
            {categories.map((c) => (
              <Chip key={c} selected={category === c} onClick={() => setCategory(category === c ? null : c)}>
                {c}
              </Chip>
            ))}
          </div>
        )}
        {mine.length > 0 && (
          <section aria-labelledby="tpl-mine" className="flex flex-col gap-3">
            <h2 id="tpl-mine" className="font-display text-panel font-semibold">
              My templates
            </h2>
            <ul className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-4">
              {mine.map((t) => (
                <li key={t.id} className="flex [&>article]:w-full">
                  <Card t={t} onStart={() => open(t)} starting={start.isPending && start.variables === t.id} />
                </li>
              ))}
            </ul>
            <h2 className="mt-2 font-display text-panel font-semibold">{noun}</h2>
          </section>
        )}
        {templates.isPending ? (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-4" role="status" aria-label="Loading prompt templates">
            {Array.from({ length: 6 }, (_, i) => (
              <Skeleton key={i} className="h-80" />
            ))}
          </div>
        ) : templates.isError ? (
          <ErrorState title="Couldn't load the prompt templates" error={templates.error} onRetry={() => templates.refetch()} />
        ) : templates.data.length === 0 ? (
          <EmptyState icon={<LayoutTemplate />} title="No prompt templates yet">
            Prompt templates ship with the server; this one has none for {type === 'audio' ? 'audio' : `${type}s`}.
          </EmptyState>
        ) : shown.length === 0 ? (
          <EmptyState
            icon={<Search />}
            title="Nothing matches"
            action={
              <Button
                size="sm"
                onClick={() => {
                  setQ('')
                  setCategory(null)
                  setMode(null)
                }}
              >
                Show all
              </Button>
            }
          >
            No prompt template matches {q ? `"${q}"` : 'this filter'}
            {category ? ` in ${category}` : ''}
            {mode ? ` among the ${mode === 'edit' ? 'photo edits' : 'generate templates'}` : ''}.
          </EmptyState>
        ) : (
          <>
            <p className="sr-only" role="status">
              {shown.length} of {all?.length ?? 0} prompt templates
            </p>
            <ul className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-4">
              {shown.map((t) => (
                <li key={t.id} className="flex [&>article]:w-full">
                  <Card t={t} onStart={() => open(t)} starting={start.isPending && start.variables === t.id} />
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </main>
  )
}
