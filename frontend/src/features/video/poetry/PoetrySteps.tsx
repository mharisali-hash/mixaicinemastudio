import { useEffect, useId, useRef, useState } from 'react'
import { Download, Film, Hand, ImageIcon, Minus, Pause, Play, Plus, RotateCcw, Save, Scissors } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { AerabButton } from '@/components/text/AerabButton'
import { usePatchPoem, usePatchTimings, useRegenerateScene, useRegenerateSong, useRenderPoetry } from '@/hooks/usePoetry'
import {
  clock,
  jobBusy,
  making,
  nudge,
  sectionLabel,
  stageOf,
  tap,
  timingProblem,
  type PoetryScene,
  type PoetryShort,
  type Timing,
} from '@/lib/poetry'
import { cn } from '@/lib/utils'
import { announce } from '@/stores/ui'

const aspectClass = (a: string) => (a === '9:16' ? 'aspect-[9/16]' : a === '1:1' ? 'aspect-square' : 'aspect-video')

function Problem({ error }: { error: Error | null }) {
  if (!error) return null
  return (
    <p role="alert" className="text-small text-studio-danger">
      {error.message}
    </p>
  )
}

// ---------------------------------------------------------------- poem

export function PoemStep({ ps }: { ps: PoetryShort }) {
  const uid = useId()
  const save = usePatchPoem(ps.id)
  const [lyrics, setLyrics] = useState(ps.poem.lyrics)
  const [title, setTitle] = useState(ps.title)
  // a fresh poem from the writer replaces the draft unless someone is mid-edit
  const [seen, setSeen] = useState(ps.poem.lyrics)
  if (ps.poem.lyrics !== seen) {
    setSeen(ps.poem.lyrics)
    setLyrics(ps.poem.lyrics)
    setTitle(ps.title)
  }
  const dirty = lyrics !== ps.poem.lyrics || title !== ps.title
  const after = ['done', 'review'].includes(stageOf(ps, 'song')?.status ?? '') || !!ps.song.generation
  if (!ps.lines.length && !ps.poem.lyrics)
    return <p className="text-body text-studio-muted">{jobBusy(ps) ? 'Writing the poem…' : 'The poem appears here once it is written.'}</p>
  return (
    <section aria-label="The poem" className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <Label htmlFor={`${uid}-title`}>Title</Label>
        <Input id={`${uid}-title`} dir="auto" lang={ps.language} maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} />
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor={`${uid}-poem`}>The poem</Label>
        <Textarea
          id={`${uid}-poem`}
          dir="auto"
          lang={ps.language}
          rows={Math.min(18, Math.max(8, lyrics.split('\n').length + 1))}
          maxLength={4000}
          value={lyrics}
          onChange={(e) => setLyrics(e.target.value)}
          className="text-[18px] leading-loose"
          aria-describedby={`${uid}-hint`}
        />
        <span id={`${uid}-hint`} className="text-small text-studio-muted">
          {ps.lines.length} sung lines. Section markers like [chorus] stay on their own line.
          {after ? ' Saving a change now sings the poem again and re-times it.' : ''}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <AerabButton text={lyrics} onText={setLyrics} language={ps.language} max={4000} target="the poem" />
        <Button
          type="button"
          variant="secondary"
          className="ml-auto"
          disabled={!dirty || jobBusy(ps)}
          loading={save.isPending}
          onClick={() => save.mutate({ lyrics, title }, { onSuccess: () => announce('Poem saved.') })}
        >
          <Save aria-hidden />
          Save the poem
        </Button>
      </div>
      <Problem error={save.error} />
    </section>
  )
}

// ---------------------------------------------------------------- song

export function SongStep({ ps }: { ps: PoetryShort }) {
  const again = useRegenerateSong(ps.id)
  const g = ps.song.generation
  return (
    <section aria-label="The song" className="flex flex-col gap-3">
      {g?.media_url && !making(g) ? (
        <audio controls src={g.media_url} className="w-full" aria-label={`The song${ps.title ? `: ${ps.title}` : ''}`} />
      ) : (
        <p className="text-body text-studio-muted">{making(g) ? 'Singing the poem…' : 'The song is made after the poem.'}</p>
      )}
      {ps.poem.tags && <p className="text-small text-studio-muted">Style: {ps.poem.tags}</p>}
      <div className="flex flex-wrap items-center gap-2">
        {ps.song.duration_s ? <span className="text-small text-studio-muted">{clock(ps.song.duration_s)}</span> : null}
        <Button
          type="button"
          variant="secondary"
          className="ml-auto"
          disabled={!ps.lines.length || jobBusy(ps)}
          loading={again.isPending}
          onClick={() => again.mutate(undefined, { onSuccess: () => announce('Singing it again.') })}
        >
          <RotateCcw aria-hidden />
          Sing it again
        </Button>
      </div>
      <Problem error={again.error} />
    </section>
  )
}

// ---------------------------------------------------------------- timing

function rowsOf(ps: PoetryShort): Timing[] {
  return ps.lines.map((l) => ({ start: l.start ?? 0, end: l.end ?? 0 }))
}

export function TimingStep({ ps }: { ps: PoetryShort }) {
  const save = usePatchTimings(ps.id)
  const audio = useRef<HTMLAudioElement>(null)
  const [rows, setRows] = useState<Timing[]>(() => rowsOf(ps))
  const [basis, setBasis] = useState(ps.lines)
  const [now, setNow] = useState(0)
  const [tapAt, setTapAt] = useState<number | null>(null)
  if (basis !== ps.lines) {
    setBasis(ps.lines)
    setRows(rowsOf(ps))
  }
  const timed = ps.lines.length > 0 && ps.lines.every((l) => l.start != null)
  const duration = ps.song.duration_s ?? 0
  const problem = timingProblem(rows, duration)
  const dirty = rows.some((r, i) => r.start !== ps.lines[i]?.start || r.end !== ps.lines[i]?.end)
  const current = rows.findIndex((r) => now >= r.start && now < r.end)

  useEffect(() => {
    const el = audio.current
    if (!el) return
    const on = () => setNow(el.currentTime)
    el.addEventListener('timeupdate', on)
    return () => el.removeEventListener('timeupdate', on)
  }, [timed])

  if (!timed) return <p className="text-body text-studio-muted">{jobBusy(ps) ? 'Listening to the song for the lines…' : 'The lines are timed once the song is made.'}</p>

  const playFrom = (t: number) => {
    const el = audio.current
    if (!el) return
    el.currentTime = t
    void el.play().catch(() => {})
  }
  const startTap = () => {
    setTapAt(0)
    playFrom(0)
    announce('Tap to time: press the tap button as each line starts.')
  }
  const doTap = () => {
    if (tapAt === null || !audio.current) return
    const t = audio.current.currentTime
    setRows((r) => tap(r, tapAt, t))
    const next = tapAt + 1
    if (next >= rows.length) {
      setTapAt(null)
      announce('All lines tapped.')
    } else setTapAt(next)
  }
  const source = { whisper: 'from the words heard in the song', phrases: 'on the sung phrases', even: 'spread evenly over the singing' }[ps.timing.source ?? 'even']

  return (
    <section aria-label="Line timing" className="flex flex-col gap-3">
      <audio ref={audio} controls src={ps.song.generation?.media_url ?? undefined} className="w-full" aria-label="The song, to check the timing" />
      <p className="text-small text-studio-muted">
        Lines placed {source}
        {ps.timing.edited ? ', then edited' : ''}. Nudge a start or end, or tap along while it plays.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        {tapAt === null ? (
          <Button type="button" variant="secondary" onClick={startTap}>
            <Hand aria-hidden />
            Tap to time
          </Button>
        ) : (
          <>
            <Button type="button" variant="primary" onClick={doTap} aria-describedby="tap-line" autoFocus>
              <Hand aria-hidden />
              Tap: line {tapAt + 1} starts now
            </Button>
            <span id="tap-line" dir="auto" lang={ps.language} className="text-body">
              {ps.lines[tapAt]?.text}
            </span>
            <Button type="button" variant="ghost" onClick={() => (setTapAt(null), audio.current?.pause())}>
              <Pause aria-hidden />
              Stop tapping
            </Button>
          </>
        )}
        <Button type="button" variant="ghost" disabled={!dirty} onClick={() => setRows(rowsOf(ps))}>
          Undo changes
        </Button>
        <Button
          type="button"
          variant="primary"
          className="ml-auto"
          disabled={!dirty || !!problem || jobBusy(ps)}
          loading={save.isPending}
          onClick={() => save.mutate(rows, { onSuccess: () => announce('Timings saved.') })}
        >
          <Save aria-hidden />
          Save timings
        </Button>
      </div>
      {problem && dirty && (
        <p role="alert" className="text-small text-studio-danger">
          {problem}
        </p>
      )}
      <Problem error={save.error} />
      <ol aria-label="Lines" className="flex flex-col gap-1.5">
        {ps.lines.map((l, i) => {
          const r = rows[i]
          const head = i === 0 || ps.lines[i - 1].part !== l.part
          return (
            <li key={l.i} className={cn('rounded-[6px] border p-2', i === current ? 'border-studio-accent bg-studio-accent-soft' : 'border-studio-border bg-studio-raised')}>
              {head && <span className="mb-1 block text-[11px] uppercase tracking-wide text-studio-muted">{sectionLabel(l.section)}</span>}
              <div className="flex flex-wrap items-center gap-2">
                <Button type="button" size="icon" variant="ghost" aria-label={`Play from line ${i + 1}`} onClick={() => playFrom(r.start)}>
                  <Play aria-hidden />
                </Button>
                <span dir="auto" lang={ps.language} className="min-w-0 flex-1 text-[17px] leading-relaxed">
                  {l.text}
                </span>
                <Edge label={`Line ${i + 1} start`} value={r.start} onNudge={(by) => setRows((x) => nudge(x, i, 'start', by, duration))} />
                <Edge label={`Line ${i + 1} end`} value={r.end} onNudge={(by) => setRows((x) => nudge(x, i, 'end', by, duration))} />
              </div>
            </li>
          )
        })}
      </ol>
    </section>
  )
}

function Edge({ label, value, onNudge }: { label: string; value: number; onNudge: (by: number) => void }) {
  return (
    <span role="group" aria-label={label} className="inline-flex items-center gap-0.5">
      <Button type="button" size="icon" variant="ghost" aria-label={`${label} 0.1 s earlier`} onClick={() => onNudge(-0.1)}>
        <Minus aria-hidden />
      </Button>
      <output aria-live="polite" className="w-14 text-center font-mono text-small tabular-nums">
        {clock(value)}
      </output>
      <Button type="button" size="icon" variant="ghost" aria-label={`${label} 0.1 s later`} onClick={() => onNudge(0.1)}>
        <Plus aria-hidden />
      </Button>
    </span>
  )
}

// ---------------------------------------------------------------- scenes

const SCENE_STATUS: Record<string, string> = { pending: 'Waiting', keyframe: 'Drawing the picture', clip: 'Animating', done: 'Ready', failed: 'Failed' }

export function ScenesStep({ ps }: { ps: PoetryShort }) {
  if (!ps.scenes.length) return <p className="text-body text-studio-muted">{jobBusy(ps) ? 'Picturing the poem…' : 'The scenes are made after the timing.'}</p>
  const done = ps.scenes.filter((s) => s.status === 'done').length
  return (
    <section aria-label="Scenes" className="flex flex-col gap-3">
      <p role="status" className="text-small text-studio-muted">
        {done} of {ps.scenes.length} scenes ready
      </p>
      <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {ps.scenes.map((s) => (
          <SceneCard key={s.id} ps={ps} sc={s} />
        ))}
      </ul>
    </section>
  )
}

function SceneCard({ ps, sc }: { ps: PoetryShort; sc: PoetryScene }) {
  const uid = useId()
  const again = useRegenerateScene(ps.id)
  const [prompt, setPrompt] = useState(sc.image_prompt)
  const lines = ps.lines.slice(sc.first, sc.last + 1)
  const busy = jobBusy(ps) || sc.status === 'keyframe' || sc.status === 'clip'
  const changed = prompt.trim() !== sc.image_prompt && prompt.trim().length > 0
  const clipUrl = sc.clip?.media_url && !making(sc.clip) ? sc.clip.media_url : null
  const pic = sc.keyframe?.thumb_url ?? sc.keyframe?.media_url
  return (
    <li className="flex flex-col gap-2 rounded-[6px] border border-studio-border bg-studio-raised p-2" aria-labelledby={`${uid}-h`}>
      <h3 id={`${uid}-h`} className="text-body font-medium">
        Scene {sc.n} <span className="font-normal text-studio-muted">· {clock(sc.start)}–{clock(sc.end)}</span>
      </h3>
      <div className={cn('overflow-hidden rounded-[4px] bg-studio-panel', aspectClass(ps.settings.aspect))}>
        {clipUrl ? (
          <video src={clipUrl} poster={pic ?? undefined} controls muted playsInline preload="metadata" className="size-full object-cover" aria-label={`Scene ${sc.n} clip`} />
        ) : pic ? (
          <img src={pic} alt={`Scene ${sc.n} picture`} className="size-full object-cover" />
        ) : null}
      </div>
      <p className={cn('text-small', sc.status === 'failed' ? 'text-studio-danger' : 'text-studio-muted')} aria-live="polite">
        {SCENE_STATUS[sc.status]}
        {sc.error ? `: ${sc.error}` : ''}
      </p>
      <div dir="auto" lang={ps.language} className="text-[15px] leading-relaxed">
        {lines.map((l) => (
          <p key={l.i}>{l.text}</p>
        ))}
      </div>
      <Label htmlFor={`${uid}-p`} className="text-small">
        Picture prompt
      </Label>
      <Textarea id={`${uid}-p`} rows={4} value={prompt} onChange={(e) => setPrompt(e.target.value)} className="text-small" />
      <div className="flex flex-wrap gap-1.5">
        <Button
          type="button"
          size="sm"
          variant="secondary"
          disabled={busy}
          loading={again.isPending && again.variables?.what === 'keyframe'}
          onClick={() => again.mutate({ sceneId: sc.id, what: 'keyframe', image_prompt: changed ? prompt.trim() : undefined }, { onSuccess: () => announce(`Scene ${sc.n}: making a new picture.`) })}
        >
          <ImageIcon aria-hidden />
          New picture
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          disabled={busy || !sc.keyframe}
          loading={again.isPending && again.variables?.what === 'clip'}
          onClick={() => again.mutate({ sceneId: sc.id, what: 'clip' }, { onSuccess: () => announce(`Scene ${sc.n}: animating it again.`) })}
        >
          <Film aria-hidden />
          New motion
        </Button>
      </div>
      <Problem error={again.error} />
    </li>
  )
}

// ---------------------------------------------------------------- render

export function RenderStep({ ps }: { ps: PoetryShort }) {
  const render = useRenderPoetry(ps.id)
  const out = ps.outputs
  const ready = (['poem', 'song', 'timing', 'scenes'] as const).every((k) => ['done', 'skipped'].includes(stageOf(ps, k)?.status ?? ''))
  const running = stageOf(ps, 'render')?.status === 'running'
  return (
    <section aria-label="The finished Short" className="flex flex-col gap-3">
      {out?.video_url ? (
        <div className={cn('mx-auto w-full overflow-hidden rounded-[6px] bg-studio-panel', ps.settings.aspect === '9:16' ? 'max-w-sm' : 'max-w-3xl')}>
          <video controls playsInline preload="metadata" src={out.preview_url ?? out.video_url} className={cn('w-full', aspectClass(ps.settings.aspect))} aria-label={`${ps.title || 'Poetry Short'}, preview`} />
        </div>
      ) : (
        <p className="text-body text-studio-muted">{running ? 'Cutting the Short…' : 'The video appears here once it is cut.'}</p>
      )}
      {out?.stale && out.video_url && <p className="text-small text-studio-warning">The timings or scenes changed since this cut. Cut it again to see them.</p>}
      <div className="flex flex-wrap items-center gap-2">
        {out?.video_url && (
          <Button asChild variant="primary">
            <a href={out.video_url} download>
              <Download aria-hidden />
              Download the video
            </a>
          </Button>
        )}
        {out?.srt_url && (
          <Button asChild variant="ghost">
            <a href={out.srt_url} download>
              <Download aria-hidden />
              Subtitles (.srt)
            </a>
          </Button>
        )}
        {out?.duration_s ? <span className="text-small text-studio-muted">{clock(out.duration_s)}</span> : null}
        <Button
          type="button"
          variant="secondary"
          className="ml-auto"
          disabled={!ready || jobBusy(ps)}
          loading={render.isPending}
          onClick={() => render.mutate(undefined, { onSuccess: () => announce('Cutting the Short.') })}
        >
          <Scissors aria-hidden />
          {out ? 'Cut it again' : 'Cut the Short'}
        </Button>
      </div>
      {out?.chapters?.length ? (
        <ol aria-label="Chapters" className="flex flex-wrap gap-2 text-small text-studio-muted">
          {out.chapters.map((c) => (
            <li key={`${c.title}-${c.start}`}>
              {c.title} · {clock(c.start)}
            </li>
          ))}
        </ol>
      ) : null}
      <Problem error={render.error} />
    </section>
  )
}
