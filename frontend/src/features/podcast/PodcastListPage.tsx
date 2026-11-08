import { useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { AudioLines, Clapperboard, Plus, Podcast, Radio } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { EmptyState, ErrorState } from '@/components/studio/states'
import { useCreateEpisode, useCreateShow, useEpisodes, useShows } from '@/hooks/usePodcast'
import { useVoiceEngines } from '@/hooks/useVoices'
import { FORMATS, newHost, type EpisodeSummary, type Format, type Host, type Medium, type Show } from '@/lib/podcast'
import { allLanguages, languageName } from '@/lib/voice'
import { cn } from '@/lib/utils'
import { SELECT } from '@/features/audio/voice/AddVoiceDialog'
import { MediumBadge } from './EpisodePage'
import { stepsFor } from './steps'

export function PodcastListPage() {
  const shows = useShows()
  const episodes = useEpisodes()
  const { engines } = useVoiceEngines()
  const [newEpisode, setNewEpisode] = useState<{ showId: string | null; medium?: Medium } | null>(null)
  const [newShow, setNewShow] = useState(false)
  // the rail menu's "New audio / video podcast" links
  const [params, setParams] = useSearchParams()
  const asked = params.get('new')
  const fromLink = asked === 'audio' || asked === 'video' ? asked : null
  const dialog = newEpisode ?? (fromLink ? { showId: null, medium: fromLink } : null)
  const closeDialog = () => {
    setNewEpisode(null)
    if (fromLink)
      setParams(
        (p) => {
          p.delete('new')
          return p
        },
        { replace: true },
      )
  }
  const showList = shows.data ?? []
  const list = episodes.data ?? []
  const showName = (id: string | null) => showList.find((s) => s.id === id)?.name ?? null

  return (
    <main data-f6-region tabIndex={-1} aria-labelledby="podcast-title" className="relative h-full overflow-y-auto focus-visible:outline-none">
      <div className="mx-auto flex max-w-5xl flex-col gap-5 px-4 py-6 md:px-8">
        <header className="flex flex-wrap items-end gap-3">
          <div className="min-w-0 flex-1">
            <h1 id="podcast-title" className="font-display text-title font-semibold">
              Podcast Studio
            </h1>
            <p className="text-small text-studio-muted">From a topic, an article or your own script to a finished episode: hosts, music and chapters, as audio or with your hosts on camera.</p>
          </div>
          <Button type="button" onClick={() => setNewShow(true)}>
            <Radio aria-hidden />
            New show
          </Button>
          <Button type="button" variant="primary" onClick={() => setNewEpisode({ showId: null })}>
            <Plus aria-hidden />
            New episode
          </Button>
        </header>

        {showList.length > 0 && (
          <section aria-labelledby="shows-heading" className="flex flex-col gap-2">
            <h2 id="shows-heading" className="section-label">
              Shows
            </h2>
            <ul className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-3">
              {showList.map((s) => (
                <ShowCard key={s.id} show={s} language={languageName(s.language, engines)} onNew={() => setNewEpisode({ showId: s.id })} />
              ))}
            </ul>
          </section>
        )}

        <section aria-labelledby="episodes-heading" className="flex flex-col gap-2">
          <h2 id="episodes-heading" className="section-label">
            Episodes
          </h2>
          {episodes.isPending ? (
            <div className="flex flex-col gap-2">
              {Array.from({ length: 3 }, (_, i) => (
                <Skeleton key={i} className="h-14" />
              ))}
            </div>
          ) : episodes.isError ? (
            <ErrorState error={episodes.error} onRetry={() => episodes.refetch()} />
          ) : list.length === 0 ? (
            <EmptyState
              icon={<Podcast />}
              title="No episodes yet"
              action={
                <Button type="button" variant="primary" onClick={() => setNewEpisode({ showId: null })}>
                  <Plus aria-hidden />
                  New episode
                </Button>
              }
            >
              Pick a topic or bring an article, choose your hosts, and the studio writes, voices and mixes it.
            </EmptyState>
          ) : (
            <ul aria-label="Episodes" className="divide-y divide-studio-border rounded-[6px] border border-studio-border bg-studio-panel">
              {list.map((ep) => (
                <EpisodeRow key={ep.id} ep={ep} show={showName(ep.show_id)} language={languageName(ep.language, engines)} />
              ))}
            </ul>
          )}
        </section>
      </div>
      <NewEpisodeDialog open={!!dialog} onOpenChange={(o) => !o && closeDialog()} shows={showList} showId={dialog?.showId ?? null} medium={dialog?.medium ?? 'audio'} />
      <NewShowDialog open={newShow} onOpenChange={setNewShow} />
    </main>
  )
}

function ShowCard({ show, language, onNew }: { show: Show; language: string; onNew: () => void }) {
  return (
    <li className="flex gap-3 rounded-[6px] border border-studio-border-strong bg-studio-panel p-3 shadow-card">
      {show.cover_url ? (
        <img src={show.cover_url} alt="" className="size-14 shrink-0 rounded-[4px] object-cover" />
      ) : (
        <span aria-hidden className="flex size-14 shrink-0 items-center justify-center rounded-[4px] border border-studio-border bg-studio-raised text-studio-accent">
          <Radio className="size-5" />
        </span>
      )}
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <p className="truncate text-body font-medium">{show.name}</p>
        <p className="text-[11px] text-studio-muted">
          {language} · {show.episodes} episode{show.episodes === 1 ? '' : 's'}
          {show.author ? ` · ${show.author}` : ''}
        </p>
        <Button type="button" size="sm" variant="ghost" className="self-start" onClick={onNew} aria-label={`New episode of ${show.name}`}>
          <Plus aria-hidden />
          Episode
        </Button>
      </div>
    </li>
  )
}

function EpisodeRow({ ep, show, language }: { ep: EpisodeSummary; show: string | null; language: string }) {
  const steps = stepsFor(ep.medium)
  const step = steps.find((s) => s.n === ep.step) ?? steps[0]
  const format = FORMATS.find((f) => f.id === ep.format)?.label ?? ep.format
  return (
    <li className="flex items-center gap-3 px-3 py-2.5">
      {ep.medium === 'video' ? <Clapperboard aria-hidden className="size-4 shrink-0 text-studio-muted" /> : <Podcast aria-hidden className="size-4 shrink-0 text-studio-muted" />}
      <div className="min-w-0 flex-1">
        <Link to={`/podcast/${ep.id}`} className="block truncate text-body font-medium hover:underline">
          {ep.title}
        </Link>
        <p className="text-[11px] text-studio-muted">
          {[show, format, language, ep.minutes ? `≈ ${ep.minutes} min written` : `${ep.target_minutes} min planned`].filter(Boolean).join(' · ')}
        </p>
      </div>
      <MediumBadge medium={ep.medium} />
      <span className={cn('shrink-0 rounded-[4px] border px-1.5 text-[11px] leading-5', ep.published ? 'border-studio-success text-studio-success' : 'border-studio-border text-studio-muted')}>
        {ep.published ? 'Published' : `Step ${step.n}: ${step.label}`}
      </span>
    </li>
  )
}

const LANGS_FALLBACK = [
  { code: 'en', name: 'English' },
  { code: 'ur', name: 'Urdu' },
]

function NewEpisodeDialog({ open, onOpenChange, shows, showId, medium }: { open: boolean; onOpenChange: (o: boolean) => void; shows: Show[]; showId: string | null; medium: Medium }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">{open && <NewEpisodeForm key={medium} shows={shows} showId={showId} medium={medium} onDone={() => onOpenChange(false)} />}</DialogContent>
    </Dialog>
  )
}

const MEDIA: { id: Medium; label: string; hint: string; icon: typeof Podcast }[] = [
  { id: 'audio', label: 'Audio podcast', hint: 'A mixed episode to listen to', icon: AudioLines },
  { id: 'video', label: 'Video podcast', hint: 'The same episode, then your hosts on camera', icon: Clapperboard },
]

function NewEpisodeForm({ shows, showId, medium: start, onDone }: { shows: Show[]; showId: string | null; medium: Medium; onDone: () => void }) {
  const create = useCreateEpisode()
  const navigate = useNavigate()
  const { engines } = useVoiceEngines()
  const languages = allLanguages(engines.map((e) => e.languages))
  const [show, setShow] = useState(showId ?? '')
  const picked = shows.find((s) => s.id === show)
  const [title, setTitle] = useState('')
  const [topic, setTopic] = useState('')
  const [format, setFormat] = useState<Format>('conversation')
  const [language, setLanguage] = useState(picked?.language ?? 'en')
  const [minutes, setMinutes] = useState(5)
  const [medium, setMedium] = useState<Medium>(start)

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    // the show's hosts when it has some, else as many as the format usually has
    let hosts: Host[] | undefined
    if (!picked?.hosts.length) {
      hosts = []
      const n = FORMATS.find((f) => f.id === format)?.hosts ?? 2
      for (let i = 0; i < n; i++) hosts.push(newHost(hosts))
    }
    create.mutate(
      { title: title.trim() || 'New episode', topic: topic.trim(), format, medium, language, target_minutes: minutes, show_id: show || null, hosts },
      {
        onSuccess: (ep) => {
          onDone()
          navigate(`/podcast/${ep.id}`)
        },
      },
    )
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3">
      <DialogHeader>
        <DialogTitle>New episode</DialogTitle>
        <DialogDescription>A topic is enough to start; you can bring material or your own script in the next step.</DialogDescription>
      </DialogHeader>
      <fieldset className="flex flex-col gap-1">
        <legend className="section-label mb-1">Kind of podcast</legend>
        <div className="grid grid-cols-2 gap-2">
          {MEDIA.map((m) => (
            <label
              key={m.id}
              className={cn(
                'flex cursor-pointer items-start gap-2 rounded-[6px] border p-2.5 text-small has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-1 has-[:focus-visible]:outline-studio-accent',
                medium === m.id ? 'border-studio-accent bg-studio-accent-soft' : 'border-studio-border-strong hover:bg-studio-panel-hover',
              )}
            >
              <input type="radio" name="medium" value={m.id} checked={medium === m.id} onChange={() => setMedium(m.id)} className="sr-only" />
              <m.icon aria-hidden className="mt-0.5 size-4 shrink-0 text-studio-accent" />
              <span className="flex flex-col">
                <span className="font-medium text-studio-text">{m.label}</span>
                <span className="text-[11px] text-studio-muted">{m.hint}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>
      <label className="flex flex-col gap-1 text-small">
        <span className="section-label">Topic</span>
        <Textarea value={topic} onChange={(e) => setTopic(e.target.value)} rows={2} placeholder="Why Lahore's old city is getting its gates back" dir="auto" />
      </label>
      <label className="flex flex-col gap-1 text-small">
        <span className="section-label">Title (optional)</span>
        <Input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} placeholder="The writer suggests one" dir="auto" />
      </label>
      <div className="grid grid-cols-2 gap-3">
        <label className="flex flex-col gap-1 text-small">
          <span className="section-label">Format</span>
          <select value={format} onChange={(e) => setFormat(e.target.value as Format)} className={SELECT}>
            {FORMATS.map((f) => (
              <option key={f.id} value={f.id}>
                {f.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-small">
          <span className="section-label">Language</span>
          <select value={language} onChange={(e) => setLanguage(e.target.value)} className={SELECT}>
            {(languages.length ? languages : LANGS_FALLBACK).map((l) => (
              <option key={l.code} value={l.code}>
                {l.name}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-small">
          <span className="section-label">Length (minutes)</span>
          <Input type="number" min={1} max={120} step={1} value={minutes} onChange={(e) => setMinutes(Math.min(120, Math.max(1, Number(e.target.value) || 1)))} />
        </label>
        <label className="flex flex-col gap-1 text-small">
          <span className="section-label">Show</span>
          <select
            value={show}
            onChange={(e) => {
              setShow(e.target.value)
              const s = shows.find((x) => x.id === e.target.value)
              if (s) setLanguage(s.language)
            }}
            className={SELECT}
          >
            <option value="">No show</option>
            {shows.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p className="text-[11px] text-studio-muted">{FORMATS.find((f) => f.id === format)?.hint}</p>
      {create.isError && (
        <p role="alert" className="text-small text-studio-danger">
          {create.error.message}
        </p>
      )}
      <DialogFooter>
        <Button type="submit" variant="primary" loading={create.isPending}>
          Create episode
        </Button>
      </DialogFooter>
    </form>
  )
}

function NewShowDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">{open && <NewShowForm onDone={() => onOpenChange(false)} />}</DialogContent>
    </Dialog>
  )
}

function NewShowForm({ onDone }: { onDone: () => void }) {
  const create = useCreateShow()
  const { engines } = useVoiceEngines()
  const languages = allLanguages(engines.map((e) => e.languages))
  const [name, setName] = useState('')
  const [author, setAuthor] = useState('')
  const [language, setLanguage] = useState('en')
  const [description, setDescription] = useState('')
  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim()) return
    create.mutate({ name: name.trim(), author: author.trim(), language, description: description.trim() }, { onSuccess: onDone })
  }
  return (
    <form onSubmit={submit} className="flex flex-col gap-3">
      <DialogHeader>
        <DialogTitle>New show</DialogTitle>
        <DialogDescription>A show holds what every episode shares: its name and author for the file tags, a language, hosts, music and ads.</DialogDescription>
      </DialogHeader>
      <label className="flex flex-col gap-1 text-small">
        <span className="section-label">Name</span>
        <Input value={name} onChange={(e) => setName(e.target.value)} maxLength={200} required dir="auto" />
      </label>
      <div className="grid grid-cols-2 gap-3">
        <label className="flex flex-col gap-1 text-small">
          <span className="section-label">Author</span>
          <Input value={author} onChange={(e) => setAuthor(e.target.value)} maxLength={200} dir="auto" />
        </label>
        <label className="flex flex-col gap-1 text-small">
          <span className="section-label">Language</span>
          <select value={language} onChange={(e) => setLanguage(e.target.value)} className={SELECT}>
            {(languages.length ? languages : LANGS_FALLBACK).map((l) => (
              <option key={l.code} value={l.code}>
                {l.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label className="flex flex-col gap-1 text-small">
        <span className="section-label">Description</span>
        <Textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={3} maxLength={4000} dir="auto" />
      </label>
      {create.isError && (
        <p role="alert" className="text-small text-studio-danger">
          {create.error.message}
        </p>
      )}
      <DialogFooter>
        <Button type="submit" variant="primary" disabled={!name.trim()} loading={create.isPending}>
          Create show
        </Button>
      </DialogFooter>
    </form>
  )
}
