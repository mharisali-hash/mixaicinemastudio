import { useId, useState } from 'react'
import { Link } from 'react-router'
import { Layers, Library, Megaphone, Music4, Pause, Play, Plus, Sparkles, Timer, X } from 'lucide-react'
import { MediaPicker } from '@/components/media/MediaPicker'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Textarea } from '@/components/ui/textarea'
import { useScript } from '@/hooks/useDialogue'
import { useMediaItem } from '@/hooks/useMedia'
import { useAudioGenerate } from '@/hooks/useModels'
import { useAddAd, useInsert, useMixEpisode, usePatchEpisode, useShows } from '@/hooks/usePodcast'
import { audioResult } from '@/lib/audio'
import type { ScriptLine } from '@/lib/dialogue'
import { LOUDNESS, busy, insertPoints, stamp, structure, totalSeconds, type Block, type Episode, type EpisodeSettings, type Loudness } from '@/lib/podcast'
import { cn } from '@/lib/utils'
import { announce } from '@/stores/ui'
import { togglePlay, useTrack } from '@/features/audio/sharedAudio'
import { SELECT } from '@/features/audio/voice/AddVoiceDialog'

type Slot = 'intro' | 'bed' | 'outro'

const SLOT: Record<Slot, { label: string; hint: string; seconds: number; prompt: string }> = {
  intro: { label: 'Intro music', hint: 'Plays alone, then dips under the first words', seconds: 15, prompt: 'warm upbeat podcast intro theme, acoustic guitar and light percussion' },
  bed: { label: 'Music bed', hint: 'Loops quietly under the whole conversation', seconds: 60, prompt: 'soft ambient podcast background bed, mellow, unobtrusive' },
  outro: { label: 'Outro music', hint: 'Rises as the last words end', seconds: 20, prompt: 'gentle podcast outro theme, resolving, warm' },
}

const BLOCK_STYLE: Record<Block['kind'], string> = {
  intro: 'bg-studio-accent/70',
  outro: 'bg-studio-accent/70',
  chapter: 'bg-studio-success/50',
  ad: 'bg-studio-warning/60',
  music: 'bg-studio-accent/40',
  break: 'bg-studio-border-strong',
}

export function ProduceStep({ ep }: { ep: Episode }) {
  const patch = usePatchEpisode(ep.id)
  const mix = useMixEpisode(ep.id)
  const sq = useScript(ep.script_id ?? undefined)
  const st = ep.settings
  const set = (p: Partial<EpisodeSettings>) => patch.mutate({ settings: { ...st, ...p } })
  const setSlot = <K extends Slot>(k: K, p: Partial<EpisodeSettings[K]>) => set({ [k]: { ...st[k], ...p } } as Partial<EpisodeSettings>)
  const lines = sq.data?.lines ?? []
  const blocks = structure(lines, ep.chapters, st)
  const total = totalSeconds(blocks)
  const c = ep.counts
  const waiting = c.lines - c.ready
  const mixing = busy(ep.jobs.mix) || mix.isPending
  const blocked = c.lines === 0 ? 'Write the script first' : waiting ? `${waiting} line${waiting === 1 ? '' : 's'} to voice or record first (step 4)` : null
  const reasonId = useId()
  const m = ep.outputs.mix
  const track = useTrack(`podcast-mix-${ep.id}`)

  return (
    <>
      <div>
        <h2 className="font-display text-title font-semibold">Produce</h2>
        <p className="text-small text-studio-muted">Music around the voices, ads between chapters, and the loudness podcast apps expect.</p>
      </div>

      <section aria-labelledby="music-heading" className="flex flex-col gap-2">
        <h3 id="music-heading" className="section-label">
          Music
        </h3>
        <div className="grid gap-3 md:grid-cols-3">
          {(['intro', 'bed', 'outro'] as Slot[]).map((k) => (
            <MusicSlot key={k} ep={ep} slot={k} mediaId={st[k].media_id} gain={st[k].gain_db} onPick={(id) => setSlot(k, { media_id: id })} onGain={(g) => setSlot(k, { gain_db: g })} />
          ))}
        </div>
        <div className="grid gap-3 rounded-[6px] border border-studio-border bg-studio-panel p-3 sm:grid-cols-2 lg:grid-cols-4">
          <NumberField label="Intro alone (s)" value={st.intro.lead_s} min={0} max={60} step={0.5} onCommit={(v) => setSlot('intro', { lead_s: v })} />
          <NumberField label="Intro under speech (s)" value={st.intro.overlap_s} min={0} max={30} step={0.5} onCommit={(v) => setSlot('intro', { overlap_s: v })} />
          <NumberField label="Outro starts before the end (s)" value={st.outro.overlap_s} min={0} max={30} step={0.5} onCommit={(v) => setSlot('outro', { overlap_s: v })} />
          <NumberField label="Longest outro (s)" value={st.outro.max_s} min={1} max={600} step={1} onCommit={(v) => setSlot('outro', { max_s: v })} />
          <RangeField label="Bed level" value={st.bed.gain_db} min={-40} max={0} unit="dB" onCommit={(v) => setSlot('bed', { gain_db: v })} />
          <RangeField label="Ducking under speech" value={st.duck_db} min={0} max={24} unit="dB" onCommit={(v) => set({ duck_db: v })} />
          <NumberField label="Gap between lines (s)" value={st.gap_s} min={0} max={10} step={0.05} onCommit={(v) => set({ gap_s: v })} />
          <NumberField label="Extra gap between chapters (s)" value={st.chapter_gap_s} min={0} max={10} step={0.1} onCommit={(v) => set({ chapter_gap_s: v })} />
        </div>
      </section>

      <AdsSection ep={ep} lines={lines} />

      <section aria-labelledby="timeline-heading" className="flex flex-col gap-2">
        <h3 id="timeline-heading" className="section-label">
          Episode structure · ≈ {stamp(total)}
        </h3>
        {blocks.length === 0 ? (
          <p className="text-small text-studio-muted">The structure shows here once there's a script.</p>
        ) : (
          <>
            <div role="img" aria-label={`Episode structure: ${blocks.map((b) => `${b.label} ${stamp(b.seconds)}`).join(', ')}`} className="flex h-8 overflow-hidden rounded-[6px] border border-studio-border">
              {blocks.map((b, i) => (
                <span key={i} title={`${b.label} · ${stamp(b.seconds)}`} className={cn('h-full min-w-1 border-r border-studio-panel last:border-r-0', BLOCK_STYLE[b.kind])} style={{ flexGrow: Math.max(0.5, b.seconds) }} />
              ))}
            </div>
            <ol className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-studio-muted" aria-hidden>
              {blocks.map((b, i) => (
                <li key={i} className="flex items-center gap-1">
                  <span className={cn('size-2 rounded-full', BLOCK_STYLE[b.kind])} />
                  <span dir="auto">{b.label}</span> {stamp(b.seconds)}
                </li>
              ))}
            </ol>
          </>
        )}
      </section>

      <section aria-labelledby="mix-heading" className="flex flex-col gap-2 rounded-[6px] border border-studio-border-strong bg-studio-panel p-3 shadow-card">
        <div className="flex flex-wrap items-center gap-2">
          <h3 id="mix-heading" className="mr-auto font-display text-[15px] font-semibold">
            Mix
          </h3>
          <label className="flex items-center gap-2 text-small">
            Loudness
            <select value={st.loudness} onChange={(e) => set({ loudness: e.target.value as Loudness })} className={cn(SELECT, 'w-56')}>
              {LOUDNESS.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.label}
                </option>
              ))}
            </select>
          </label>
          <Button
            type="button"
            variant="primary"
            aria-disabled={!!blocked || mixing || undefined}
            aria-describedby={blocked ? reasonId : undefined}
            loading={mixing}
            onClick={() => !blocked && !mixing && mix.mutate(undefined, { onSuccess: () => announce('Mixing the episode.') })}
          >
            <Layers aria-hidden />
            {m ? 'Mix again' : 'Mix the episode'}
          </Button>
        </div>
        {blocked && (
          <p id={reasonId} className="text-small text-studio-warning">
            {blocked}
          </p>
        )}
        {busy(ep.jobs.mix) && (
          <p role="status" className="text-small text-studio-muted">
            {ep.jobs.mix?.message || 'Mixing'}
          </p>
        )}
        {ep.jobs.mix?.status === 'failed' && (
          <p role="alert" className="text-small text-studio-danger">
            The mix failed: {ep.jobs.mix.error}
          </p>
        )}
        {(mix.error || patch.error) && (
          <p role="alert" className="text-small text-studio-danger">
            {(mix.error ?? patch.error)?.message}
          </p>
        )}
        {m?.url && (
          <div className="flex flex-wrap items-center gap-2">
            <Button type="button" size="sm" variant="outline" onClick={() => togglePlay(`podcast-mix-${ep.id}`, m.url!, { title: ep.title, mediaId: m.media_id })}>
              {track.playing ? <Pause aria-hidden /> : <Play aria-hidden />}
              {track.playing ? 'Pause' : 'Play the episode'}
            </Button>
            {m.duration_s ? <span className="font-mono text-[12px] text-studio-muted">{stamp(m.duration_s)}</span> : null}
            {m.stale && <span className="rounded-[4px] border border-studio-warning px-1.5 text-[11px] leading-5 text-studio-warning">Out of date: mix again</span>}
            {m.media_id && (
              <Button asChild size="sm" variant="ghost">
                <Link to={`/audio/edit/${m.media_id}`}>Open in Editor</Link>
              </Button>
            )}
          </div>
        )}
      </section>
    </>
  )
}

function MusicSlot({ ep, slot, mediaId, gain, onPick, onGain }: { ep: Episode; slot: Slot; mediaId: string | null; gain: number; onPick: (id: string | null) => void; onGain: (g: number) => void }) {
  const info = SLOT[slot]
  const item = useMediaItem(mediaId)
  const [picking, setPicking] = useState(false)
  const track = useTrack(`podcast-${slot}-${mediaId}`)
  const url = item.data?.media_url
  const musicLink = `/audio/music?${new URLSearchParams({ duration: String(info.seconds), prompt: info.prompt, from: 'podcast', episode: ep.id })}`
  return (
    <div className="flex flex-col gap-2 rounded-[6px] border border-studio-border bg-studio-panel p-3">
      <div className="flex items-center gap-2">
        <Music4 aria-hidden className="size-4 text-studio-muted" />
        <span className="text-small font-medium">{info.label}</span>
      </div>
      <p className="text-[11px] text-studio-muted">{info.hint}</p>
      {mediaId ? (
        <div className="flex items-center gap-1.5">
          <Button type="button" size="icon-sm" variant="ghost" disabled={!url} aria-label={`${track.playing ? 'Pause' : 'Play'} the ${info.label.toLowerCase()}`} onClick={() => url && togglePlay(`podcast-${slot}-${mediaId}`, url, { title: item.data?.title ?? info.label, mediaId })}>
            {track.playing ? <Pause aria-hidden /> : <Play aria-hidden />}
          </Button>
          <span className="min-w-0 flex-1 truncate text-small" dir="auto">
            {item.data?.title ?? 'Loading…'}
            {item.data && !url ? ' · still generating' : ''}
          </span>
          <Button type="button" size="icon-sm" variant="ghost" aria-label={`Remove the ${info.label.toLowerCase()}`} onClick={() => onPick(null)}>
            <X aria-hidden />
          </Button>
        </div>
      ) : (
        <p className="text-small text-studio-faint">None</p>
      )}
      <div className="flex flex-wrap gap-1.5">
        <Button type="button" size="sm" onClick={() => setPicking(true)}>
          <Library aria-hidden />
          Library
        </Button>
        <GeneratePopover slot={slot} onMade={onPick} />
        <Button asChild size="sm" variant="ghost">
          <Link to={musicLink}>Music page</Link>
        </Button>
      </div>
      {mediaId && <RangeField label="Level" value={gain} min={-30} max={6} unit="dB" onCommit={onGain} />}
      <MediaPicker
        open={picking}
        onOpenChange={setPicking}
        kind="audio"
        max={1}
        title={`Pick the ${info.label.toLowerCase()}`}
        confirmLabel="Use this"
        onConfirm={(items) => {
          if (items[0]) onPick(items[0].id)
          setPicking(false)
        }}
      />
    </div>
  )
}

function GeneratePopover({ slot, onMade }: { slot: Slot; onMade: (id: string) => void }) {
  const info = SLOT[slot]
  const gen = useAudioGenerate()
  const [prompt, setPrompt] = useState(info.prompt)
  const [seconds, setSeconds] = useState(info.seconds)
  const [open, setOpen] = useState(false)
  const run = () =>
    gen.mutate(
      { kind: 'music', prompt: prompt.trim(), duration_s: seconds },
      {
        onSuccess: (res) => {
          const made = audioResult(res).items[0]
          if (made) {
            onMade(made.id)
            announce(`Generating the ${info.label.toLowerCase()}; it's set for the mix and plays once it's ready.`)
          }
          setOpen(false)
        },
      },
    )
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger className="inline-flex h-7 items-center gap-1.5 rounded-[6px] border border-studio-border-strong bg-studio-raised px-2.5 text-small hover:bg-studio-panel-hover [&_svg]:size-4">
        <Sparkles aria-hidden />
        Generate
      </PopoverTrigger>
      <PopoverContent aria-label={`Generate the ${info.label.toLowerCase()}`} className="flex w-80 flex-col gap-2">
        <label className="flex flex-col gap-1 text-small">
          <span className="section-label">Describe it</span>
          <Textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={3} />
        </label>
        <NumberField label="Length (s)" value={seconds} min={5} max={240} step={1} onCommit={setSeconds} />
        {gen.isError && (
          <p role="alert" className="text-small text-studio-danger">
            {gen.error.message}
          </p>
        )}
        <Button type="button" variant="primary" size="sm" disabled={!prompt.trim()} loading={gen.isPending} onClick={run}>
          Generate {seconds} s
        </Button>
      </PopoverContent>
    </Popover>
  )
}

function AdsSection({ ep, lines }: { ep: Episode; lines: ScriptLine[] }) {
  const shows = useShows()
  const show = shows.data?.find((s) => s.id === ep.show_id) ?? null
  const insert = useInsert(ep.id)
  const addAd = useAddAd()
  const points = insertPoints(lines, ep.chapters)
  const [at, setAt] = useState(0)
  const [picking, setPicking] = useState<'ad' | 'clip' | null>(null)
  const [sponsor, setSponsor] = useState('')
  const [sponsorHost, setSponsorHost] = useState(ep.hosts.find((h) => !h.me)?.id ?? '')
  const point = points[Math.min(at, points.length - 1)]
  const after = point?.after ?? null
  const done = (what: string) => announce(`${what} inserted ${point?.label.toLowerCase() ?? ''}.`)
  const problem = insert.error ?? addAd.error

  if (lines.length === 0) return null
  return (
    <section aria-labelledby="ads-heading" className="flex flex-col gap-2">
      <h3 id="ads-heading" className="section-label">
        Ads, jingles and pauses
      </h3>
      <div className="flex flex-col gap-3 rounded-[6px] border border-studio-border bg-studio-panel p-3">
        <label className="flex flex-wrap items-center gap-2 text-small">
          Insert
          <select value={at} onChange={(e) => setAt(Number(e.target.value))} className={cn(SELECT, 'w-auto max-w-full')}>
            {points.map((p, i) => (
              <option key={i} value={i}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
        {show && show.ads.length > 0 && (
          <ul aria-label={`${show.name} ad library`} className="flex flex-wrap gap-2">
            {show.ads.map((a) => (
              <li key={a.id}>
                <Button type="button" size="sm" variant="outline" loading={insert.isPending} onClick={() => insert.mutate({ kind: 'ad', after_line_id: after, ad_id: a.id }, { onSuccess: () => done(a.name) })}>
                  <Megaphone aria-hidden />
                  {a.name}
                  <span className="text-[11px] text-studio-muted">{a.media_id ? 'clip' : 'read'}</span>
                </Button>
              </li>
            ))}
          </ul>
        )}
        <div className="flex flex-wrap gap-2">
          <Button type="button" size="sm" onClick={() => setPicking('clip')}>
            <Plus aria-hidden />
            A clip from the Library
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={() => insert.mutate({ kind: 'break', after_line_id: after, seconds: 1.5 }, { onSuccess: () => done('A pause') })}>
            <Timer aria-hidden />
            A 1.5 s pause
          </Button>
          {show && (
            <Button type="button" size="sm" variant="ghost" onClick={() => setPicking('ad')}>
              <Library aria-hidden />
              Add a jingle to {show.name}
            </Button>
          )}
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <label className="flex min-w-60 flex-1 flex-col gap-1 text-small">
            <span className="section-label">Sponsor read</span>
            <Input value={sponsor} onChange={(e) => setSponsor(e.target.value)} maxLength={3000} dir="auto" placeholder="This episode is brought to you by…" />
          </label>
          <label className="flex w-40 flex-col gap-1 text-small">
            <span className="section-label">Read by</span>
            <select value={sponsorHost} onChange={(e) => setSponsorHost(e.target.value)} className={SELECT}>
              {ep.hosts.map((h) => (
                <option key={h.id} value={h.id}>
                  {h.name}
                </option>
              ))}
            </select>
          </label>
          <Button
            type="button"
            size="sm"
            disabled={!sponsor.trim()}
            onClick={() =>
              insert.mutate(
                { kind: 'ad', after_line_id: after, text: sponsor.trim(), speaker_id: sponsorHost || undefined },
                {
                  onSuccess: () => {
                    done('The sponsor read')
                    if (show) addAd.mutate({ id: show.id, ad: { name: sponsor.trim().slice(0, 40), kind: 'sponsor', media_id: null, text: sponsor.trim(), host_id: sponsorHost || null } })
                    setSponsor('')
                  },
                },
              )
            }
          >
            Insert the read
          </Button>
        </div>
        <p className="text-[11px] text-studio-muted">A sponsor read is voiced by its host like any line (render it in step 4){show ? ` and kept in ${show.name}'s ad library` : ''}.</p>
        {problem && (
          <p role="alert" className="text-small text-studio-danger">
            {problem.message}
          </p>
        )}
      </div>
      <MediaPicker
        open={picking !== null}
        onOpenChange={(o) => !o && setPicking(null)}
        kind="audio"
        max={1}
        title={picking === 'ad' ? 'Add a jingle to the show' : 'Insert a clip'}
        confirmLabel={picking === 'ad' ? 'Add to the show' : 'Insert'}
        onConfirm={(items) => {
          const it = items[0]
          if (it && picking === 'ad' && show) addAd.mutate({ id: show.id, ad: { name: it.title || 'Jingle', kind: 'jingle', media_id: it.id, text: null, host_id: null } })
          else if (it) insert.mutate({ kind: 'ad', after_line_id: after, media_id: it.id }, { onSuccess: () => done(it.title || 'The clip') })
          setPicking(null)
        }}
      />
    </section>
  )
}

function NumberField({ label, value, min, max, step, onCommit }: { label: string; value: number; min: number; max: number; step: number; onCommit: (v: number) => void }) {
  const uid = useId()
  const [text, setText] = useState(String(value))
  const [was, setWas] = useState(value)
  if (was !== value) {
    setWas(value)
    setText(String(value))
  }
  const commit = () => {
    const n = Number(text)
    if (!Number.isFinite(n) || text.trim() === '') return setText(String(value))
    const v = Math.min(max, Math.max(min, n))
    setText(String(v))
    if (v !== value) onCommit(v)
  }
  return (
    <div className="flex flex-col gap-1 text-small">
      <label htmlFor={uid}>{label}</label>
      <Input id={uid} type="number" inputMode="decimal" min={min} max={max} step={step} value={text} onChange={(e) => setText(e.target.value)} onBlur={commit} onKeyDown={(e) => e.key === 'Enter' && commit()} className="h-7" />
    </div>
  )
}

function RangeField({ label, value, min, max, unit, onCommit }: { label: string; value: number; min: number; max: number; unit: string; onCommit: (v: number) => void }) {
  const uid = useId()
  const [v, setV] = useState(value)
  const [was, setWas] = useState(value)
  if (was !== value) {
    setWas(value)
    setV(value)
  }
  return (
    <div className="flex flex-col gap-1 text-small">
      <label htmlFor={uid} className="flex justify-between">
        {label}
        <span className="font-mono text-studio-muted">
          {min < 0 && v > 0 ? '+' : ''}
          {v} {unit}
        </span>
      </label>
      <input
        id={uid}
        type="range"
        min={min}
        max={max}
        step={1}
        value={v}
        onChange={(e) => setV(Number(e.target.value))}
        onPointerUp={() => v !== value && onCommit(v)}
        onKeyUp={() => v !== value && onCommit(v)}
        onBlur={() => v !== value && onCommit(v)}
        className="accent-studio-accent"
      />
    </div>
  )
}
