import { useEffect, useId, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { Music, Shuffle, Sparkles, Upload, X } from 'lucide-react'
import { Button, buttonVariants } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { Skeleton } from '@/components/ui/skeleton'
import { EmptyState, ErrorState } from '@/components/studio/states'
import { MediaPicker } from '@/components/media/MediaPicker'
import { useMediaHost } from '@/components/generate/useMediaHost'
import { qk } from '@/hooks/keys'
import { useMediaItem } from '@/hooks/useMedia'
import { useMakeRemix, useRemixAnalysis, useRemixes, useRemixRecipes } from '@/hooks/useRemix'
import { useUploads } from '@/hooks/useUploads'
import { useVoices } from '@/hooks/useVoices'
import { clock } from '@/lib/audio'
import { DEFAULT_STEER, feel, fits, MAX_SONGS, steps, VOCALS, type RemixRecipe, type Steer } from '@/lib/remix'
import type { MediaItem } from '@/lib/types'
import { acceptFor } from '@/lib/upload'
import { cn } from '@/lib/utils'
import { announce } from '@/stores/ui'
import { AudioTile } from '../AudioTile'

const CHIP = cn(
  'flex cursor-pointer items-start gap-2 rounded-[6px] border p-2.5 transition-colors duration-150',
  'border-studio-border-strong bg-studio-raised hover:bg-studio-panel-hover',
  'has-[:checked]:border-studio-accent has-[:checked]:bg-studio-accent-soft has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-studio-accent',
  'has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-60 has-[:disabled]:hover:bg-studio-raised',
)
const FIELD = 'h-9 rounded-[6px] border border-studio-border-strong bg-studio-raised px-2 text-body focus-visible:outline-2 focus-visible:outline-studio-accent'

function useSongs() {
  const [params, setParams] = useSearchParams()
  const songs = (params.get('songs') ?? params.get('source') ?? '').split(',').filter(Boolean).slice(0, MAX_SONGS)
  const write = (next: (cur: string[]) => string[]) =>
    setParams((prev) => {
      const cur = (prev.get('songs') ?? prev.get('source') ?? '').split(',').filter(Boolean)
      const out = [...new Set(next(cur))].slice(0, MAX_SONGS)
      return out.length ? { songs: out.join(',') } : new URLSearchParams()
    })
  return {
    songs,
    add: (ids: string[]) => write((cur) => [...cur, ...ids]),
    remove: (id: string) => write((cur) => cur.filter((x) => x !== id)),
  }
}

export function RemixPage() {
  const { songs, add, remove } = useSongs()
  const [picking, setPicking] = useState(false)
  const uploads = useUploads(['audio', 'video'], (m: MediaItem) => add([m.id]))
  const row = uploads.rows.find((r) => r.state === 'uploading') ?? uploads.rows[uploads.rows.length - 1]
  const room = MAX_SONGS - songs.length

  return (
    <main data-f6-region tabIndex={-1} aria-labelledby="remix-title" className="relative h-full overflow-y-auto focus-visible:outline-none">
      <div className="mx-auto flex max-w-6xl flex-col gap-4 px-4 py-6 md:px-8">
        <header>
          <h1 id="remix-title" className="font-display text-title font-semibold">
            Remix
          </h1>
          <p className="text-small text-studio-muted">
            Turn a song into new versions, or blend two or three songs into a mashup or a medley. Every remix lands in the Library.
          </p>
        </header>

        <section aria-labelledby="remix-songs" className="flex flex-col gap-2 rounded-[6px] border border-studio-border-strong bg-studio-panel p-3 shadow-card">
          <h2 id="remix-songs" className="flex flex-wrap items-baseline gap-x-2">
            <span className="section-label">Songs</span>
            <span className="text-small text-studio-muted">One to remix it; two or three for a mashup or a medley</span>
          </h2>
          {songs.length > 0 && (
            <ol className="flex flex-col gap-2">
              {songs.map((id, n) => (
                <SongSlot key={id} id={id} n={n + 1} onRemove={() => remove(id)} />
              ))}
            </ol>
          )}
          {room > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <Button type="button" onClick={() => setPicking(true)}>
                <Music aria-hidden />
                {songs.length ? 'Add a song' : 'Pick a song'}
              </Button>
              <label className={cn(buttonVariants({ variant: 'ghost' }), 'cursor-pointer focus-within:outline-2 focus-within:outline-studio-accent')}>
                <Upload aria-hidden />
                Upload
                <input
                  type="file"
                  multiple
                  accept={acceptFor(['audio', 'video'])}
                  className="sr-only"
                  onChange={(e) => {
                    if (e.target.files?.length) uploads.add(Array.from(e.target.files).slice(0, room))
                    e.target.value = ''
                  }}
                />
              </label>
              {row?.state === 'uploading' && <span className="text-small text-studio-muted">Uploading {row.name}…</span>}
              {row?.state === 'error' && <span className="text-small text-studio-danger">{row.error}</span>}
            </div>
          )}
          {row?.state === 'uploading' && <Progress value={row.progress} label={`Uploading ${row.name}`} />}
        </section>

        {songs.length ? (
          <Remixer key={songs.join(',')} songs={songs} />
        ) : (
          <EmptyState icon={<Shuffle />} title="Pick a song to remix">
            Upload one or pick it from the Library. Add a second or third song to make a mashup or a medley.
          </EmptyState>
        )}
      </div>
      <MediaPicker
        open={picking}
        onOpenChange={setPicking}
        kind="audio"
        kinds={['audio', 'video']}
        max={MAX_SONGS}
        taken={songs}
        title="Pick songs to remix"
        confirmLabel="Use these"
        onConfirm={(items) => add(items.map((m) => m.id))}
      />
    </main>
  )
}

function SongSlot({ id, n, onRemove }: { id: string; n: number; onRemove: () => void }) {
  const item = useMediaItem(id)
  const heard = useRemixAnalysis(id)
  const title = item.data?.title || 'Untitled'
  return (
    <li className="flex min-w-0 items-center gap-3 rounded-[6px] border border-studio-border bg-studio-raised px-3 py-2">
      <span aria-hidden className="font-mono text-small text-studio-muted">
        {n}
      </span>
      <span className="flex min-w-0 flex-1 flex-col">
        {item.isPending ? (
          <Skeleton className="h-4 w-40" />
        ) : item.isError ? (
          <span className="text-small text-studio-danger">Couldn't open this song</span>
        ) : (
          <span className="truncate text-body font-medium">{title}</span>
        )}
        <span className="text-[11px] text-studio-muted">
          {item.data?.duration_s ? clock(item.data.duration_s) : null}
          {heard.isPending ? ' · listening…' : feel(heard.data) ? ` · ${feel(heard.data)}` : ''}
        </span>
      </span>
      <Button type="button" size="sm" variant="ghost" onClick={onRemove} aria-label={`Remove song ${n}, ${title}`}>
        <X aria-hidden />
      </Button>
    </li>
  )
}

function Remixer({ songs }: { songs: string[] }) {
  const qc = useQueryClient()
  const uid = useId()
  const recipes = useRemixRecipes()
  const make = useMakeRemix()
  const remixes = useRemixes(songs[0])
  const voices = useVoices()
  const [steer, setSteer] = useState<Steer>(DEFAULT_STEER)
  const set = <K extends keyof Steer>(k: K, v: Steer[K]) => setSteer((s) => ({ ...s, [k]: v }))
  const items = remixes.data?.items ?? []
  const running = remixes.data?.jobs ?? []
  const { handlers, host } = useMediaHost({ items })
  const many = songs.length > 1
  const usable = (r: RemixRecipe) => r.available && fits(r, songs.length)
  const picked = steer.picked.filter((p) => recipes.data?.recipes.some((r) => r.id === p && usable(r)))

  // the Library grids learn about new tracks when the last one is made
  const wasBusy = useRef(running.length > 0)
  useEffect(() => {
    if (wasBusy.current && !running.length) {
      qc.invalidateQueries({ queryKey: qk.mediaLists })
      announce('Your remixes are ready.')
    }
    wasBusy.current = running.length > 0
  }, [running.length, qc])

  const go = (auto: boolean) =>
    make.mutate(auto ? { sources: songs, auto: true } : { sources: songs, recipes: steps({ ...steer, picked }) }, {
      onSuccess: (res) => announce(`Making ${res.items.length} remix${res.items.length === 1 ? '' : 'es'}. They appear below as they finish.`),
    })

  const toggle = (id: Steer['picked'][number], on: boolean) =>
    set('picked', on ? [...steer.picked, id] : steer.picked.filter((x) => x !== id))

  const has = (id: Steer['picked'][number]) => picked.includes(id)
  const songName = (n: number) => `Song ${n + 1}`

  return (
    <>
      <section aria-labelledby={`${uid}-make`} className="flex flex-col gap-3 rounded-[6px] border border-studio-border-strong bg-studio-panel p-3 shadow-card">
        <h2 id={`${uid}-make`} className="section-label">
          Make remixes
        </h2>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="button" variant="primary" size="lg" loading={make.isPending} onClick={() => go(true)}>
            <Sparkles aria-hidden />
            Auto remix: 4 versions
          </Button>
          <p className="text-small text-studio-muted">
            {many ? 'Two mashups, a medley and a slowed medley.' : 'Slowed + reverb, sped up, lo-fi and a new beat under the vocals.'}
          </p>
        </div>

        <details className="group rounded-[6px] border border-studio-border bg-studio-raised">
          <summary className="cursor-pointer select-none px-3 py-2 text-body font-medium focus-visible:outline-2 focus-visible:outline-studio-accent">
            Steer it yourself
          </summary>
          <div className="flex flex-col gap-3 border-t border-studio-border p-3">
            {recipes.isPending ? (
              <Skeleton className="h-24" />
            ) : recipes.isError ? (
              <ErrorState compact error={recipes.error} onRetry={() => recipes.refetch()} />
            ) : (
              <fieldset className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                <legend className="mb-1 text-small text-studio-muted">Pick the kinds of remix to make</legend>
                {recipes.data.recipes.map((r) => {
                  const why = !r.available ? r.reason : !fits(r, songs.length) ? (r.min_sources > 1 ? 'Add a second song for this' : 'Works on one song at a time') : null
                  return (
                    <label key={r.id} className={CHIP}>
                      <input
                        type="checkbox"
                        className="mt-1 size-4 accent-studio-accent"
                        checked={has(r.id)}
                        disabled={!usable(r)}
                        onChange={(e) => toggle(r.id, e.target.checked)}
                        aria-describedby={`${uid}-${r.id}`}
                      />
                      <span className="flex min-w-0 flex-col gap-0.5">
                        <span className="text-body font-medium">{r.label}</span>
                        <span id={`${uid}-${r.id}`} className="text-small text-studio-muted">
                          {why ?? r.hint}
                        </span>
                      </span>
                    </label>
                  )
                })}
              </fieldset>
            )}

            {has('new_beat') && (
              <div className="flex flex-col gap-1.5">
                <label htmlFor={`${uid}-style`} className="text-small font-medium">
                  Style of the new beat
                </label>
                <input
                  id={`${uid}-style`}
                  className={FIELD}
                  value={steer.style}
                  maxLength={200}
                  placeholder="Leave empty for a surprise"
                  onChange={(e) => set('style', e.target.value)}
                />
                <div className="flex flex-wrap gap-1.5" role="group" aria-label="Style ideas">
                  {(recipes.data?.styles ?? []).slice(0, 6).map((s) => (
                    <Button key={s} type="button" size="sm" variant="secondary" onClick={() => set('style', s)}>
                      {s.split(',')[0]}
                    </Button>
                  ))}
                </div>
              </div>
            )}

            {has('new_voice') && (
              <fieldset className="flex flex-wrap items-center gap-3">
                <legend className="mb-1 text-small font-medium">Who sings it</legend>
                {VOCALS.map((v) => (
                  <label key={v.id} className="flex items-center gap-1.5 text-body">
                    <input type="radio" name={`${uid}-vocal`} className="size-4 accent-studio-accent" checked={steer.vocal === v.id} onChange={() => set('vocal', v.id)} />
                    {v.label}
                  </label>
                ))}
                {steer.vocal === 'voice' && (
                  <select aria-label="Library voice" className={FIELD} value={steer.voiceId} onChange={(e) => set('voiceId', e.target.value)}>
                    <option value="">Pick a voice…</option>
                    {(voices.data ?? []).map((v) => (
                      <option key={v.id} value={v.id}>
                        {v.name}
                      </option>
                    ))}
                  </select>
                )}
              </fieldset>
            )}

            {has('key_tempo') && (
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="flex flex-col gap-1 text-small font-medium">
                  Key: {steer.semitones > 0 ? `+${steer.semitones}` : steer.semitones} semitones
                  <input type="range" min={-6} max={6} step={1} value={steer.semitones} onChange={(e) => set('semitones', Number(e.target.value))} className="accent-studio-accent" />
                </label>
                <label className="flex flex-col gap-1 text-small font-medium">
                  Tempo: {Math.round(steer.tempo * 100)}%
                  <input type="range" min={0.7} max={1.4} step={0.05} value={steer.tempo} onChange={(e) => set('tempo', Number(e.target.value))} className="accent-studio-accent" />
                </label>
              </div>
            )}

            {has('mashup') && (
              <div className="flex flex-wrap items-center gap-3 text-small font-medium">
                <label className="flex items-center gap-1.5">
                  Vocals from
                  <select className={FIELD} value={steer.vocalsFrom} onChange={(e) => set('vocalsFrom', Number(e.target.value))}>
                    {songs.map((_, n) => (
                      <option key={n} value={n}>
                        {songName(n)}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex items-center gap-1.5">
                  music from
                  <select className={FIELD} value={steer.musicFrom} onChange={(e) => set('musicFrom', Number(e.target.value))}>
                    {songs.map((_, n) => (
                      <option key={n} value={n}>
                        {songName(n)}
                      </option>
                    ))}
                  </select>
                </label>
                {steer.vocalsFrom === steer.musicFrom && <span className="text-studio-danger">Pick two different songs</span>}
              </div>
            )}

            <div className="flex flex-wrap items-center gap-2">
              <p className="mr-auto text-[11px] text-studio-muted">Effects take seconds; a new beat, a new voice or a mashup runs on the GPU and takes a few minutes.</p>
              <Button
                type="button"
                variant="secondary"
                disabled={!picked.length || (has('mashup') && steer.vocalsFrom === steer.musicFrom) || (has('new_voice') && steer.vocal === 'voice' && !steer.voiceId)}
                loading={make.isPending}
                onClick={() => go(false)}
              >
                <Shuffle aria-hidden />
                Make {picked.length || ''} remix{picked.length === 1 ? '' : 'es'}
              </Button>
            </div>
          </div>
        </details>
        {make.error && <ErrorState compact title="Couldn't start the remix" error={make.error} />}
        {running.length > 0 && (
          <p role="status" className="text-small text-studio-muted">
            Making {running.length} remix{running.length === 1 ? '' : 'es'}…
          </p>
        )}
      </section>

      <section aria-labelledby={`${uid}-made`} className="flex flex-col gap-3">
        <h2 id={`${uid}-made`} className="section-label">
          Remixes of song 1
        </h2>
        {remixes.isPending ? (
          <Skeleton className="h-24" />
        ) : remixes.isError ? (
          <ErrorState compact error={remixes.error} onRetry={() => remixes.refetch()} />
        ) : items.length === 0 ? (
          <p className="text-small text-studio-muted">None yet.</p>
        ) : (
          <ul aria-label="Remixes" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {items.map((m) => (
              <li key={m.id}>
                <AudioTile item={m} handlers={handlers} ratio="3 / 1" />
              </li>
            ))}
          </ul>
        )}
      </section>
      {host}
    </>
  )
}
