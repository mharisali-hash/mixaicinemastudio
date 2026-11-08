import { useId, useState } from 'react'
import { Clapperboard, RefreshCw, StepForward } from 'lucide-react'
import { StatusPill } from '@/components/studio/status-pill'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { jobRunning, useAiVideoEstimate, useRenderAiVideo, type VPRenderBody } from '@/hooks/useVideoPodcast'
import { generationStatus } from '@/lib/status'
import type { VideoPodcast, VPQuality, VPTrack } from '@/lib/types'
import { plural } from '@/lib/utils'
import { announce } from '@/stores/ui'
import { aspectClass, clock, progressText, takeReady, trackName } from './vp'

const QUALITIES: { value: VPQuality; label: string; hint: string }[] = [
  { value: 'draft', label: 'Draft', hint: 'Quicker, 480p. Check the lip sync and the listening.' },
  { value: 'final', label: 'Final', hint: '720p in two passes, often overnight. Only tracks whose angle, voice or segment changed are made again.' },
]

export function RenderPanel({ vp }: { vp: VideoPodcast }) {
  const uid = useId()
  const [quality, setQuality] = useState<VPQuality>(vp.outputs.draft ? 'final' : 'draft')
  const estimate = useAiVideoEstimate(vp.id, quality)
  const render = useRenderAiVideo(vp.id)
  const locked = jobRunning(vp)
  const tracks = vp.plan.tracks
  const noPlan = tracks.length === 0
  const done = tracks.filter((t) => takeReady(t, quality)).length
  const label = QUALITIES.find((q) => q.value === quality)!.label

  const go = (body: Omit<VPRenderBody, 'quality'> = {}, said = `Rendering the ${quality}.`) =>
    !locked && render.mutate({ quality, ...body }, { onSuccess: () => announce(said) })

  return (
    <>
      <div>
        <h2 className="font-display text-title font-semibold">Render</h2>
        <p className="text-small text-studio-muted">
          Each camera track is one long take, made on the video GPU in pieces of about 10 seconds that continue from each other. The cut is then made over the episode mix.
        </p>
      </div>
      {noPlan && <p className="rounded-[6px] border border-studio-warning/50 bg-studio-warning/5 p-3 text-small">Plan the cameras first (step 4).</p>}
      {vp.mix_stale && <p className="text-small text-studio-warning">The mix changed after the plan was made: rebuild the cut list in Cameras &amp; cut before the final render.</p>}

      <section aria-labelledby={`${uid}-go`} className="flex flex-col gap-3 rounded-[6px] border border-studio-border-strong bg-studio-panel p-3 shadow-card">
        <h3 id={`${uid}-go`} className="font-display text-[15px] font-semibold">
          Quality
        </h3>
        <fieldset className="flex flex-col gap-1.5 text-small">
          <legend className="sr-only">Quality</legend>
          {QUALITIES.map((q) => (
            <label key={q.value} className="flex items-start gap-2">
              <input type="radio" name={`${uid}-q`} value={q.value} checked={quality === q.value} onChange={() => setQuality(q.value)} className="mt-0.5 accent-studio-accent" />
              <span>
                <span className="font-medium">{q.label}</span> <span className="text-studio-muted">{q.hint}</span>
              </span>
            </label>
          ))}
        </fieldset>
        <div aria-live="polite" className="text-small">
          {estimate.isPending ? (
            <span className="text-studio-muted">Working out how long it takes…</span>
          ) : estimate.isError ? (
            <span className="text-studio-danger">Couldn't estimate the time: {estimate.error.message}</span>
          ) : estimate.data.takes === 0 ? (
            <span className="text-studio-muted">Every track already has a {quality} take. Rendering again only cuts the video.</span>
          ) : (
            <span>
              <span className="font-medium">{estimate.data.label}</span>
              <span className="text-studio-muted">
                {' '}
                · {plural(estimate.data.takes, 'take')} in {plural(estimate.data.pieces, 'piece')} · {clock(estimate.data.seconds)} of footage
              </span>
            </span>
          )}
        </div>
        <Button type="button" variant="primary" className="self-start" disabled={noPlan || locked} loading={render.isPending && !render.variables?.track_ids} onClick={() => go()}>
          <Clapperboard aria-hidden />
          Render {label.toLowerCase()}
        </Button>
        {render.isError && (
          <p role="alert" className="text-small text-studio-danger">
            {render.error.message}
          </p>
        )}
      </section>

      {!noPlan && (
        <section aria-labelledby={`${uid}-takes`} className="flex flex-col gap-2">
          <div className="flex flex-wrap items-baseline gap-2">
            <h3 id={`${uid}-takes`} className="mr-auto font-display text-[15px] font-semibold">
              Camera tracks
            </h3>
            <span role="status" className="text-small text-studio-muted">
              {done} of {plural(tracks.length, 'track')} have a {quality} take
            </span>
          </div>
          <ul aria-label={`${label} tracks`} className="flex flex-col gap-2">
            {tracks.map((t) => (
              <TrackRow
                key={t.id}
                vp={vp}
                track={t}
                quality={quality}
                locked={locked}
                pending={render.isPending && render.variables?.track_ids?.[0] === t.id}
                onRender={(fromPiece, name) =>
                  go(fromPiece ? { track_ids: [t.id], from_piece: fromPiece } : { track_ids: [t.id] }, fromPiece ? `Rendering ${name} again from piece ${fromPiece}.` : `Rendering ${name} again.`)
                }
              />
            ))}
          </ul>
        </section>
      )}
    </>
  )
}

function TrackRow({
  vp,
  track,
  quality,
  locked,
  pending,
  onRender,
}: {
  vp: VideoPodcast
  track: VPTrack
  quality: VPQuality
  locked: boolean
  pending: boolean
  onRender: (fromPiece: number | null, name: string) => void
}) {
  const [from, setFrom] = useState('1')
  const take = track.takes[quality]
  const prog = track.progress[quality]
  const seg = vp.plan.segments.find((s) => s.id === track.segment_id)
  const name = `${trackName(vp, track)}, segment ${(seg?.index ?? 0) + 1}`
  const piece = Number(from)
  const pieceOk = Number.isInteger(piece) && piece >= 1 && piece <= track.pieces
  // resuming keeps the earlier pieces of that quality, so there has to be a take to keep them from
  const canResume = !!take && track.pieces > 1
  const status = take
    ? generationStatus(take.status)
    : track.needs[quality]
      ? ({ label: 'Not rendered', tone: 'neutral', icon: 'clock' } as const)
      : ({ label: 'Up to date', tone: 'success', icon: 'check' } as const)
  const pieces = progressText(prog, track.pieces)

  return (
    <li className="flex flex-wrap gap-3 rounded-[6px] border border-studio-border bg-studio-panel p-2">
      <div className={`darkroom w-36 shrink-0 overflow-hidden rounded-[4px] ${aspectClass(vp.settings.aspect)}`}>
        {takeReady(track, quality) ? (
          <video src={take!.media_url!} preload="metadata" muted playsInline controls aria-label={name} className="size-full object-cover" />
        ) : track.thumb_url ? (
          <img src={track.thumb_url} alt="" loading="lazy" className="size-full object-cover" />
        ) : (
          <span className="flex size-full items-center justify-center text-[11px] text-studio-on-dark-muted">No frame</span>
        )}
      </div>
      <div className="flex min-w-48 flex-1 flex-col gap-1.5">
        <div className="flex flex-wrap items-center gap-2">
          <span className="mr-auto text-small font-medium" dir="auto">
            {name}
          </span>
          <StatusPill status={status} />
        </div>
        <span className="font-mono text-[11px] text-studio-muted">
          {clock(track.start)}–{clock(track.end)} · {plural(track.pieces, 'piece')}
        </span>
        {prog && <Progress value={prog.pieces ? prog.done / prog.pieces : null} label={`${name}: ${quality} pieces done`} className="max-w-xs" />}
        <span aria-live="polite" className={prog?.failed ? 'text-[11px] text-studio-danger' : 'text-[11px] text-studio-muted'}>
          {pieces}
        </span>
        {track.needs[quality] && take && <span className="text-[11px] text-studio-warning">The angle, voice or segment changed since this take.</span>}
        <div className="flex flex-wrap items-end gap-2">
          <Button type="button" size="sm" variant="ghost" disabled={locked} loading={pending} aria-label={`Re-render ${name}`} onClick={() => onRender(null, name)}>
            <RefreshCw aria-hidden />
            Re-render this track
          </Button>
          {track.pieces > 1 && (
            <span className="flex items-end gap-1.5">
              <label className="flex flex-col gap-0.5 text-[11px] text-studio-muted">
                <span>From piece (1 to {track.pieces})</span>
                <input
                  type="number"
                  min={1}
                  max={track.pieces}
                  step={1}
                  value={from}
                  disabled={!canResume}
                  aria-label={`${name}: re-render from piece`}
                  onChange={(e) => setFrom(e.target.value)}
                  className="h-7 w-16 rounded-[6px] border border-studio-border-strong bg-studio-raised px-1.5 font-mono text-small text-studio-text focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-studio-accent"
                />
              </label>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={locked || !canResume || !pieceOk}
                title={canResume ? undefined : `Needs a ${quality} take of this track first`}
                aria-label={`Re-render ${name} from piece ${pieceOk ? piece : ''}`.trim()}
                onClick={() => onRender(piece, name)}
              >
                <StepForward aria-hidden />
                Re-render from piece {pieceOk ? piece : ''}
              </Button>
            </span>
          )}
        </div>
      </div>
    </li>
  )
}
