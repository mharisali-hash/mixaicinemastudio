import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { Film, Music, SquareSplitHorizontal, Upload } from 'lucide-react'
import { Button, buttonVariants } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { Skeleton } from '@/components/ui/skeleton'
import { EmptyState, ErrorState } from '@/components/studio/states'
import { MediaPicker } from '@/components/media/MediaPicker'
import { useMediaHost } from '@/components/generate/useMediaHost'
import { qk } from '@/hooks/keys'
import { useJob } from '@/hooks/useJobs'
import { useMediaItem } from '@/hooks/useMedia'
import { useSplitStems, useStems } from '@/hooks/useStems'
import { useUploads } from '@/hooks/useUploads'
import { clock } from '@/lib/audio'
import { modeLabel, STEMS_MODES, type StemSplit, type StemsMode } from '@/lib/stems'
import type { MediaItem } from '@/lib/types'
import { acceptFor } from '@/lib/upload'
import { cn, timeAgo } from '@/lib/utils'
import { announce } from '@/stores/ui'
import { AudioTile } from '../AudioTile'

export function StemsPage() {
  const [params, setParams] = useSearchParams()
  const sourceId = params.get('source')
  const [picking, setPicking] = useState<'audio' | 'video' | null>(null)
  const pick = (m: MediaItem) => setParams({ source: m.id })
  const uploads = useUploads(['audio', 'video'], pick)
  const row = uploads.rows[uploads.rows.length - 1]

  return (
    <main data-f6-region tabIndex={-1} aria-labelledby="stems-title" className="relative h-full overflow-y-auto focus-visible:outline-none">
      <div className="mx-auto flex max-w-6xl flex-col gap-4 px-4 py-6 md:px-8">
        <header>
          <h1 id="stems-title" className="font-display text-title font-semibold">
            Stems
          </h1>
          <p className="text-small text-studio-muted">Split a song, a cue or a video's sound into its parts. Every stem lands in the Library as a track of its own.</p>
        </header>

        <section aria-label="Track to split" className="flex flex-wrap items-center gap-2">
          <Button type="button" onClick={() => setPicking('audio')}>
            <Music aria-hidden />
            Pick audio
          </Button>
          <Button type="button" onClick={() => setPicking('video')}>
            <Film aria-hidden />
            Pick a video
          </Button>
          <label className={cn(buttonVariants({ variant: 'ghost' }), 'cursor-pointer focus-within:outline-2 focus-within:outline-studio-accent')}>
            <Upload aria-hidden />
            Upload
            <input
              type="file"
              accept={acceptFor(['audio', 'video'])}
              className="sr-only"
              onChange={(e) => {
                if (e.target.files?.length) uploads.add(e.target.files)
                e.target.value = ''
              }}
            />
          </label>
          {row?.state === 'uploading' && <span className="text-small text-studio-muted">Uploading {row.name}…</span>}
          {row?.state === 'error' && <span className="text-small text-studio-danger">{row.error}</span>}
        </section>
        {row?.state === 'uploading' && <Progress value={row.progress} label={`Uploading ${row.name}`} />}

        {sourceId ? (
          <Splitter key={sourceId} id={sourceId} />
        ) : (
          <EmptyState icon={<SquareSplitHorizontal />} title="Pick a track to split">
            A song, a score cue or a video with sound. You can also start from a tile's ⋯ menu: Split stems.
          </EmptyState>
        )}
      </div>
      <MediaPicker
        open={!!picking}
        onOpenChange={(o) => !o && setPicking(null)}
        kind={picking ?? 'audio'}
        max={1}
        title={picking === 'video' ? 'Pick a video' : 'Pick a track'}
        confirmLabel="Split this"
        onConfirm={([m]) => m && pick(m)}
      />
    </main>
  )
}

function Splitter({ id }: { id: string }) {
  const qc = useQueryClient()
  const item = useMediaItem(id)
  const stems = useStems(id)
  const split = useSplitStems(id)
  const [mode, setMode] = useState<StemsMode>('two')
  const [lyrics, setLyrics] = useState(false)
  const [mine, setMine] = useState<string | null>(null)
  const running = stems.data?.jobs[0]
  const live = useJob(running?.id ?? mine) ?? running
  const splits = stems.data?.splits ?? []
  const fresh = mine ? splits.find((s) => s.id === mine) : undefined
  const earlier = splits.filter((s) => s !== fresh)
  const { handlers, host } = useMediaHost({ items: splits.flatMap((s) => s.items) })
  const busy = !!running

  // when the last running split finishes, the Library grids have new tracks too
  const wasBusy = useRef(busy)
  useEffect(() => {
    if (wasBusy.current && !busy) qc.invalidateQueries({ queryKey: qk.mediaLists })
    wasBusy.current = busy
  }, [busy, qc])
  const freshId = fresh?.id
  const freshCount = fresh?.items.length ?? 0
  useEffect(() => {
    if (freshId) announce(`${freshCount} stems ready.`)
  }, [freshId, freshCount])

  if (item.isPending) return <Skeleton className="h-40" />
  if (item.isError) return <ErrorState title="Couldn't open that track" error={item.error} onRetry={() => item.refetch()} />
  const src = item.data
  const failed = mine && !running && !fresh && live?.id === mine && live.status === 'failed' ? live : null

  const go = () =>
    split.mutate(
      { mode, ...(lyrics ? { lyrics: true } : {}) },
      {
        onSuccess: (res) => {
          setMine(res.job.id)
          announce(`Splitting ${src.title || 'the track'} into ${modeLabel(mode).toLowerCase()}. The stems appear below.`)
        },
      },
    )

  return (
    <>
      <section aria-labelledby="stems-split" className="flex flex-col gap-3 rounded-[6px] border border-studio-border-strong bg-studio-panel p-3 shadow-card">
        <h2 id="stems-split" className="flex min-w-0 flex-wrap items-baseline gap-x-2 text-body">
          <span className="section-label">{src.kind === 'video' ? 'Video' : 'Audio'}</span>
          <span className="min-w-0 truncate font-medium">{src.title || 'Untitled'}</span>
          {src.duration_s ? <span className="font-mono text-[11px] text-studio-muted">{clock(src.duration_s)}</span> : null}
        </h2>
        <fieldset className="grid gap-2 sm:grid-cols-2" disabled={busy}>
          <legend className="sr-only">Split into</legend>
          {STEMS_MODES.map((m) => (
            <label
              key={m.id}
              className={cn(
                'flex cursor-pointer gap-2.5 rounded-[6px] border p-2.5 transition-colors duration-150',
                'border-studio-border-strong bg-studio-raised hover:bg-studio-panel-hover',
                'has-[:checked]:border-studio-accent has-[:checked]:bg-studio-accent-soft has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-studio-accent',
              )}
            >
              <input
                type="radio"
                name="stems-mode"
                value={m.id}
                checked={mode === m.id}
                onChange={() => setMode(m.id)}
                aria-labelledby={`stems-label-${m.id}`}
                aria-describedby={`stems-hint-${m.id}`}
                className="mt-1 size-4 accent-studio-accent"
              />
              <span className="flex min-w-0 flex-col gap-0.5">
                <span id={`stems-label-${m.id}`} className="text-body font-medium">
                  {m.label}
                </span>
                <span id={`stems-hint-${m.id}`} className="text-small text-studio-muted">
                  {m.hint}
                </span>
              </span>
            </label>
          ))}
        </fieldset>
        <label className="flex items-center gap-2 text-body">
          <input type="checkbox" className="size-4 accent-studio-accent" checked={lyrics} disabled={busy} onChange={(e) => setLyrics(e.target.checked)} />
          Also write out the lyrics
          <span className="text-small text-studio-muted">(from the vocals, takes a little longer)</span>
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <p className="mr-auto text-[11px] text-studio-muted">Runs on the GPU. Longer tracks take longer; you can leave this page while it works.</p>
          <Button type="button" variant="primary" disabled={busy} loading={split.isPending} onClick={go}>
            <SquareSplitHorizontal aria-hidden />
            Split
          </Button>
        </div>
        {busy && live && (
          <div role="status" className="flex flex-col gap-1.5 rounded-[6px] border border-studio-border bg-studio-raised px-3 py-2 text-small">
            <span>{live.status === 'running' ? live.message || 'Separating the stems' : 'Waiting for a GPU worker…'}</span>
            <Progress value={live.status === 'running' ? live.progress : null} label="Split progress" />
          </div>
        )}
        {split.error && <ErrorState compact title="Couldn't start the split" error={split.error} />}
        {failed && <ErrorState compact title="The split failed" error={failed.error || 'Something went wrong on the worker'} />}
      </section>

      {fresh && <SplitResults split={fresh} heading="This split" handlers={handlers} />}

      <section aria-labelledby="stems-earlier" className="flex flex-col gap-3">
        <h2 id="stems-earlier" className="section-label">
          Previous splits of this track
        </h2>
        {stems.isPending ? (
          <Skeleton className="h-24" />
        ) : stems.isError ? (
          <ErrorState compact error={stems.error} onRetry={() => stems.refetch()} />
        ) : earlier.length === 0 ? (
          <p className="text-small text-studio-muted">{fresh ? 'None before this one.' : 'Not split yet.'}</p>
        ) : (
          earlier.map((s) => <SplitResults key={s.id} split={s} handlers={handlers} />)
        )}
      </section>
      {host}
    </>
  )
}

function SplitResults({ split, heading, handlers }: { split: StemSplit; heading?: string; handlers: ReturnType<typeof useMediaHost>['handlers'] }) {
  const title = heading ?? `${modeLabel(split.mode)} · ${timeAgo(split.created_at)}`
  const hid = `split-${split.id}`
  return (
    <section aria-labelledby={hid} className="flex flex-col gap-2">
      <h3 id={hid} className={heading ? 'font-display text-[1.15rem] font-semibold' : 'text-small font-medium text-studio-muted'}>
        {title}
        {heading && <span className="ml-2 text-small font-normal text-studio-muted">{modeLabel(split.mode)}</span>}
      </h3>
      <ul aria-label={`Stems, ${title}`} className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {split.items.map((m) => (
          <li key={m.id}>
            <AudioTile item={m} handlers={handlers} ratio="3 / 1" />
          </li>
        ))}
      </ul>
      {split.lyrics && <SplitLyrics text={split.lyrics} />}
    </section>
  )
}

function SplitLyrics({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  const copy = () => {
    navigator.clipboard?.writeText(text).then(
      () => {
        setCopied(true)
        announce('Lyrics copied.')
      },
      () => announce("Couldn't copy; select the text instead."),
    )
  }
  return (
    <div className="flex flex-col gap-1.5 rounded-[6px] border border-studio-border bg-studio-raised p-3">
      <div className="flex items-center gap-2">
        <span className="section-label mr-auto">Lyrics heard on the vocals</span>
        <Button type="button" size="sm" variant="secondary" onClick={copy}>
          {copied ? 'Copied' : 'Copy'}
        </Button>
      </div>
      <p dir="auto" className="whitespace-pre-line text-body">
        {text}
      </p>
    </div>
  )
}
