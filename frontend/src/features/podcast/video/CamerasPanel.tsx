import { useId, useRef, useState } from 'react'
import { RotateCcw, Split, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/alert-dialog'
import { jobRunning, useDeleteAiVideoCut, usePatchAiVideoCut, useReplanAiVideo, useSwitchAiVideoCut } from '@/hooks/useVideoPodcast'
import type { VideoPodcast, VPCut, VPSegment, VPTrack } from '@/lib/types'
import { cn, plural } from '@/lib/utils'
import { announce } from '@/stores/ui'
import { StageAction } from './StageAction'
import { cameraLook, cameraLooks, clock, generating, hostName, nearestSnap, parseTime, progressText, takeReady, trackName, type CameraLook } from './vp'

const FIELD = 'h-8 rounded-[6px] border border-studio-border-strong bg-studio-raised px-2 text-body focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-studio-accent'

export function CamerasPanel({ vp }: { vp: VideoPodcast }) {
  const replan = useReplanAiVideo(vp.id)
  const [confirm, setConfirm] = useState(false)
  const locked = jobRunning(vp)
  const { segments, cuts } = vp.plan
  const st = vp.plan_stats
  const empty = cuts.length === 0 && vp.plan.tracks.length === 0

  return (
    <>
      <div className="flex flex-wrap items-start gap-2">
        <div className="mr-auto">
          <h2 className="font-display text-title font-semibold">Cameras &amp; cut</h2>
          <p className="text-small text-studio-muted">
            Each host's camera rolls through every segment, lip-synced while they talk and listening while the other does. The cut switches between those cameras.
          </p>
        </div>
        {!empty && (
          <Button type="button" size="sm" disabled={locked || !vp.duration_s} loading={replan.isPending} onClick={() => setConfirm(true)}>
            <RotateCcw aria-hidden />
            Rebuild cut list
          </Button>
        )}
      </div>
      <StageAction vp={vp} stage="plan" label="Plan the cameras" hint="The segments, camera tracks and the cut are made from the mix. Nothing is rendered yet." />
      {vp.mix_stale && (
        <p role="status" className="rounded-[6px] border border-studio-warning/50 bg-studio-warning/5 p-3 text-small">
          The mix changed after this plan was made. Rebuild the cut list so the cameras match it.
        </p>
      )}

      {empty ? (
        <p className="text-small text-studio-muted">No cameras yet. They are planned after the camera angles.</p>
      ) : (
        <>
          <p className="text-small text-studio-muted">
            {plural(st.segments, 'segment')} · {plural(st.host_tracks, 'host track')} · {st.wide_tracks} wide · {plural(st.pieces, 'piece')} to render · {plural(st.cuts, 'cut')} ·{' '}
            {clock(st.seconds || vp.duration_s || 0)}
          </p>
          <Tracks vp={vp} segments={segments} />
          <CutEditor vp={vp} locked={locked} />
        </>
      )}
      {replan.isError && (
        <p role="alert" className="text-small text-studio-danger">
          {replan.error.message}
        </p>
      )}
      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title="Rebuild the cut list?"
        description="The segments, tracks and the automatic cut are made again from the mix. Your camera switches are lost."
        confirmLabel="Rebuild"
        tone="danger"
        onConfirm={() => replan.mutate(undefined, { onSuccess: () => announce('Cut list rebuilt.') })}
      />
    </>
  )
}

function trackStatus(t: VPTrack) {
  if (generating(t.takes.final) || generating(t.takes.draft)) {
    const q = generating(t.takes.final) ? 'final' : 'draft'
    return `rendering ${q}${progressText(t.progress[q], t.pieces) ? `, ${progressText(t.progress[q], t.pieces)}` : ''}`
  }
  if (takeReady(t, 'final') && !t.needs.final) return 'final ready'
  if (takeReady(t, 'draft') && !t.needs.draft) return 'draft ready'
  return 'needs render'
}

function Tracks({ vp, segments }: { vp: VideoPodcast; segments: VPSegment[] }) {
  const uid = useId()
  return (
    <section aria-labelledby={`${uid}-h`} className="flex flex-col gap-2">
      <h3 id={`${uid}-h`} className="font-display text-[15px] font-semibold">
        Camera tracks
      </h3>
      <ol className="flex flex-col gap-2">
        {segments.map((seg) => {
          const tracks = vp.plan.tracks.filter((t) => t.segment_id === seg.id)
          return (
            <li key={seg.id} aria-labelledby={`${uid}-${seg.id}`} className="flex flex-col gap-2 rounded-[6px] border border-studio-border-strong bg-studio-panel p-2.5">
              <h4 id={`${uid}-${seg.id}`} className="flex flex-wrap items-baseline gap-2 text-small">
                <span className="font-medium" dir="auto">
                  {seg.index + 1}. {seg.title || 'Segment'}
                </span>
                <span className="font-mono text-studio-muted">
                  {clock(seg.start)}–{clock(seg.end)}
                </span>
              </h4>
              {tracks.length === 0 ? (
                <p className="text-small text-studio-muted">No cameras in this segment.</p>
              ) : (
                <ul aria-label={`Cameras in segment ${seg.index + 1}`} className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
                  {tracks.map((t) => (
                    <li key={t.id} className="flex flex-col gap-1 rounded-[6px] border border-studio-border bg-studio-raised p-1.5">
                      <span className="darkroom block aspect-video overflow-hidden rounded-[4px]">
                        {t.thumb_url && <img src={t.thumb_url} alt="" loading="lazy" className="size-full object-cover" />}
                      </span>
                      <span className="text-small font-medium" dir="auto">
                        {trackName(vp, t)}
                      </span>
                      <span className="font-mono text-[11px] text-studio-muted">
                        {clock(t.start)}–{clock(t.end)}
                      </span>
                      <span className="text-[11px] text-studio-muted">
                        {plural(t.pieces, 'piece')} · {trackStatus(t)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          )
        })}
      </ol>
    </section>
  )
}

function CutEditor({ vp, locked }: { vp: VideoPodcast; locked: boolean }) {
  const uid = useId()
  const looks = cameraLooks(vp)
  const cuts = vp.plan.cuts
  const [picked, setPicked] = useState<string | null>(null)
  const edit = usePatchAiVideoCut(vp.id)
  const remove = useDeleteAiVideoCut(vp.id)
  const sw = useSwitchAiVideoCut(vp.id)
  const error = [edit, remove, sw].find((m) => m.isError)?.error
  const at = cuts.findIndex((c) => c.id === picked)
  const cut = at < 0 ? null : cuts[at]
  const speaker = (c: VPCut) => hostName(vp, c.speaker_id)

  return (
    <section aria-labelledby={`${uid}-h`} className="flex flex-col gap-3">
      <div>
        <h3 id={`${uid}-h`} className="font-display text-[15px] font-semibold">
          The cut
        </h3>
        <p className="text-small text-studio-muted">Every camera is already in sync with the mix, so changing the cut never needs a re-render.</p>
      </div>
      <Timeline vp={vp} looks={looks} picked={picked} onPick={setPicked} />

      {cut ? (
        <div role="group" aria-label={`Cut ${at + 1}`} className="flex flex-wrap items-end gap-3 rounded-[6px] border border-studio-border-strong bg-studio-panel p-3 text-small">
          <div className="mr-auto flex flex-col gap-0.5">
            <span className="font-medium">
              Cut {at + 1} · <span className="font-mono">{clock(cut.start)}–{clock(cut.end)}</span>
            </span>
            <span className="max-w-md text-studio-muted" dir="auto">
              {speaker(cut) ? `${speaker(cut)}: ` : ''}
              {cut.text || 'No speech'}
            </span>
          </div>
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-studio-muted">Camera</span>
            <select
              aria-label={`Camera for cut ${at + 1}`}
              value={cut.camera}
              disabled={locked || edit.isPending}
              onChange={(e) => edit.mutate({ cutId: cut.id, camera: e.target.value }, { onSuccess: () => announce(`Cut ${at + 1} now shows ${cameraLook(looks, e.target.value).label}.`) })}
              className={cn(FIELD, 'w-44')}
            >
              {!looks.some((c) => c.key === cut.camera) && <option value={cut.camera}>{cameraLook(looks, cut.camera).label}</option>}
              {looks.map((c) => (
                <option key={c.key} value={c.key}>
                  {c.label}
                </option>
              ))}
            </select>
          </label>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={locked || cuts.length < 2}
            loading={remove.isPending}
            onClick={() =>
              remove.mutate(cut.id, {
                onSuccess: () => {
                  setPicked(null)
                  announce(`Switch at ${clock(cut.start)} removed.`)
                },
              })
            }
          >
            <Trash2 aria-hidden />
            Remove this switch
          </Button>
        </div>
      ) : (
        <p className="text-small text-studio-muted">Pick a cut on the timeline to change its camera or remove the switch.</p>
      )}

      <SwitchForm vp={vp} looks={looks} locked={locked} pending={sw.isPending} onSwitch={(body, done) => sw.mutate(body, { onSuccess: done })} />

      {error && (
        <p role="alert" className="text-small text-studio-danger">
          {error.message}
        </p>
      )}

      <details className="rounded-[6px] border border-studio-border bg-studio-panel">
        <summary className="cursor-pointer px-3 py-2 text-small font-medium focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-studio-accent">Cut list as a table</summary>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-small">
            <caption className="sr-only">Cuts in time order</caption>
            <thead className="border-b border-studio-border text-left text-[11px] uppercase tracking-wide text-studio-muted">
              <tr>
                <th scope="col" className="px-2 py-1.5">#</th>
                <th scope="col" className="px-2 py-1.5">Time</th>
                <th scope="col" className="px-2 py-1.5">Camera</th>
                <th scope="col" className="px-2 py-1.5">Speaker</th>
                <th scope="col" className="px-2 py-1.5">Line</th>
              </tr>
            </thead>
            <tbody>
              {cuts.map((c, i) => (
                <tr key={c.id} className={cn('border-b border-studio-border last:border-b-0', c.id === picked && 'bg-studio-accent-soft')}>
                  <td className="px-2 py-1.5 font-mono text-studio-muted">{i + 1}</td>
                  <td className="whitespace-nowrap px-2 py-1.5 font-mono">
                    {clock(c.start)}–{clock(c.end)}
                  </td>
                  <td className="px-2 py-1.5">{cameraLook(looks, c.camera).label}</td>
                  <td className="px-2 py-1.5" dir="auto">
                    {speaker(c) ?? <span className="text-studio-muted">None</span>}
                  </td>
                  <td className="max-w-64 px-2 py-1.5">
                    <span className="line-clamp-2" dir="auto" title={c.text}>
                      {c.text}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  )
}

/** Cuts as proportional blocks over the episode; one tab stop, arrows move along it, Enter picks the cut. */
function Timeline({ vp, looks, picked, onPick }: { vp: VideoPodcast; looks: CameraLook[]; picked: string | null; onPick: (id: string) => void }) {
  const uid = useId()
  const [at, setAt] = useState(0)
  const refs = useRef<(HTMLButtonElement | null)[]>([])
  const { cuts, snaps } = vp.plan
  const total = vp.duration_s || cuts.at(-1)?.end || 1
  const n = cuts.length
  const cur = Math.min(at, n - 1)
  const used = looks.filter((c) => cuts.some((x) => x.camera === c.key))
  const onKey = (e: React.KeyboardEvent) => {
    const to = e.key === 'ArrowRight' ? cur + 1 : e.key === 'ArrowLeft' ? cur - 1 : e.key === 'Home' ? 0 : e.key === 'End' ? n - 1 : null
    if (to === null) return
    e.preventDefault()
    const i = Math.max(0, Math.min(n - 1, to))
    setAt(i)
    refs.current[i]?.focus()
  }
  return (
    <div className="flex flex-col gap-1">
      <div role="group" aria-label="Cut timeline" aria-describedby={`${uid}-help`} onKeyDown={onKey} className="flex h-11 w-full overflow-hidden rounded-[6px] border border-studio-border-strong bg-studio-raised">
        {cuts.map((c, i) => {
          const look = cameraLook(looks, c.camera)
          const on = picked === c.id
          return (
            <button
              key={c.id}
              ref={(el) => {
                refs.current[i] = el
              }}
              type="button"
              tabIndex={i === cur ? 0 : -1}
              aria-label={`Cut ${i + 1}: ${look.label}, ${clock(c.start)} to ${clock(c.end)}`}
              aria-pressed={on}
              title={`${i + 1}. ${look.label} · ${clock(c.start)}–${clock(c.end)}`}
              onFocus={() => setAt(i)}
              onClick={() => onPick(c.id)}
              style={{ width: `${(Math.max(0, c.end - c.start) / total) * 100}%` }}
              className={cn(
                'h-full min-w-[3px] overflow-hidden border-r-2 border-studio-panel px-1 text-left text-[10px] leading-tight last:border-r-0',
                'focus-visible:relative focus-visible:z-10 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-studio-text',
                look.swatch,
                look.ink,
                on && 'outline-2 -outline-offset-2 outline-studio-text',
              )}
            >
              <span aria-hidden className="block truncate font-medium">
                {look.label}
              </span>
            </button>
          )
        })}
      </div>
      {/* snap points: where a switch can land without cutting a word */}
      <div aria-hidden className="relative h-1.5 w-full">
        {snaps.map((s) => (
          <span key={s} className="absolute top-0 h-full w-px bg-studio-muted" style={{ left: `${(s / total) * 100}%` }} />
        ))}
      </div>
      <p id={`${uid}-help`} className="sr-only">
        Left and right arrows move between cuts. Enter picks a cut to edit.
      </p>
      <div className="flex flex-wrap items-center gap-3 text-[11px] text-studio-muted">
        <ul aria-label="Cameras" className="flex flex-wrap gap-3">
          {used.map((c) => (
            <li key={c.key} className="flex items-center gap-1">
              <span aria-hidden className={cn('size-2.5 rounded-[2px] border border-studio-border-strong', c.swatch)} />
              {c.label}
            </li>
          ))}
        </ul>
        <span className="ml-auto flex items-center gap-1">
          <span aria-hidden className="inline-block h-2 w-px bg-studio-muted" />
          {plural(snaps.length, 'snap point')} (gaps in the voices)
        </span>
      </div>
    </div>
  )
}

function SwitchForm({
  vp,
  looks,
  locked,
  pending,
  onSwitch,
}: {
  vp: VideoPodcast
  looks: CameraLook[]
  locked: boolean
  pending: boolean
  onSwitch: (body: { at: number; camera: string }, done: () => void) => void
}) {
  const uid = useId()
  const [raw, setRaw] = useState('')
  const [camera, setCamera] = useState(looks[0]?.key ?? '')
  const total = vp.duration_s ?? vp.plan.cuts.at(-1)?.end ?? 0
  const t = parseTime(raw)
  const bad = raw.trim() !== '' && (t === null || t <= 0 || t >= total)
  const snap = t === null || bad ? null : nearestSnap(vp.plan.snaps, t)
  const name = cameraLook(looks, camera).label

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    if (t === null || bad || locked || !camera) return
    onSwitch({ at: t, camera }, () => {
      setRaw('')
      announce(`Switched to ${name} at ${clock(snap ?? t)}.`)
    })
  }

  return (
    <form onSubmit={submit} aria-labelledby={`${uid}-h`} className="flex flex-col gap-2 rounded-[6px] border border-studio-border bg-studio-panel p-3">
      <h4 id={`${uid}-h`} className="text-small font-medium">
        Switch camera at a time
      </h4>
      <div className="flex flex-wrap items-end gap-3 text-small">
        <label className="flex flex-col gap-1">
          <span className="text-[11px] text-studio-muted">Time (seconds or mm:ss)</span>
          <input
            type="text"
            inputMode="decimal"
            value={raw}
            placeholder="01:15"
            onChange={(e) => setRaw(e.target.value)}
            aria-invalid={bad || undefined}
            aria-describedby={`${uid}-snap`}
            className={cn(FIELD, 'w-28 font-mono')}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[11px] text-studio-muted">Camera</span>
          <select value={camera} onChange={(e) => setCamera(e.target.value)} className={cn(FIELD, 'w-44')}>
            {looks.map((c) => (
              <option key={c.key} value={c.key}>
                {c.label}
              </option>
            ))}
          </select>
        </label>
        <Button type="submit" size="md" disabled={locked || t === null || bad} loading={pending}>
          <Split aria-hidden />
          Switch
        </Button>
      </div>
      <p id={`${uid}-snap`} aria-live="polite" className={cn('text-[11px]', bad ? 'text-studio-danger' : 'text-studio-muted')}>
        {bad
          ? `Type a time between 0 and ${clock(total)}.`
          : snap !== null && t !== null
            ? `Snaps to ${clock(snap)}${Math.abs(snap - t) >= 0.05 ? ` (the nearest gap in the voices)` : ''}: ${name} from there to the next switch.`
            : 'The switch lands on the nearest gap in the voices, so no word is cut.'}
      </p>
    </form>
  )
}
