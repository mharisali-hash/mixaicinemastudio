import { useId, useState } from 'react'
import { Link, useParams } from 'react-router'
import { ArrowLeft, Clapperboard, Library, Plus, RefreshCw, Save, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { ErrorState } from '@/components/studio/states'
import { StageTimeline } from '@/features/quick/StageTimeline'
import { useMediaItem } from '@/hooks/useMedia'
import { usePresenter, usePresenterActions } from '@/hooks/usePresenter'
import { atReview, BACKGROUNDS, presenterBusy, type PresenterScene, type PresenterVideo } from '@/lib/presenter'
import type { QuickStage } from '@/lib/types'
import { cn } from '@/lib/utils'
import { announce } from '@/stores/ui'

const SELECT = 'h-8 rounded-[6px] border border-studio-border-strong bg-studio-raised px-2 text-small focus-visible:outline-2 focus-visible:outline-studio-accent'

interface Draft {
  id?: string
  text: string
  background: string
}

const draftOf = (pv: PresenterVideo): Draft[] => pv.scenes.map((s) => ({ id: s.id, text: s.text, background: s.background }))

export function PresenterPage() {
  const { id = '' } = useParams()
  const q = usePresenter(id)
  if (q.isPending) return <Skeleton className="m-6 h-64" />
  if (q.isError) return <ErrorState title="Couldn't open this presenter video" error={q.error} onRetry={() => q.refetch()} />
  return <Workspace pv={q.data} />
}

function Workspace({ pv }: { pv: PresenterVideo }) {
  const act = usePresenterActions(pv.id)
  const busy = presenterBusy(pv)
  const [draft, setDraft] = useState<Draft[]>(() => draftOf(pv))
  const [seen, setSeen] = useState(pv.updated_at)
  // the server's copy changed (a save, or a run that wrote durations): start again from it
  if (pv.updated_at !== seen) {
    setSeen(pv.updated_at)
    setDraft(draftOf(pv))
  }
  const dirty = JSON.stringify(draft) !== JSON.stringify(draftOf(pv))
  const failed = pv.stages.find((s) => s.status === 'failed')
  const review = atReview(pv)
  const out = pv.outputs.final ?? pv.outputs.draft
  const tall = pv.settings.aspect === '9:16'

  const save = (then?: () => void) =>
    act.update.mutate(
      { scenes: draft.map((d) => ({ id: d.id, text: d.text, background: d.background })) },
      { onSuccess: () => (then ? then() : announce('Scenes saved. Only the changed ones are made again.')) },
    )
  const run = (what: 'continue' | 'draft' | 'final') => {
    const go = () =>
      what === 'continue'
        ? act.proceed.mutate(undefined, { onSuccess: () => announce('Drawing the frames.') })
        : act.render.mutate(what, { onSuccess: () => announce(`Making the ${what}.`) })
    if (dirty) save(go)
    else go()
  }

  return (
    <main data-f6-region tabIndex={-1} aria-labelledby="pv-h" className="relative h-full overflow-y-auto focus-visible:outline-none">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-5 px-4 py-6 md:px-8">
        <header className="flex flex-wrap items-center gap-2">
          <Button asChild variant="ghost" size="sm">
            <Link to="/video/presenter">
              <ArrowLeft aria-hidden />
              Presenter videos
            </Link>
          </Button>
          <h1 id="pv-h" dir="auto" className="mr-auto font-display text-[22px] font-semibold">
            {pv.title || 'Untitled'}
          </h1>
          <span className="text-small text-studio-muted">
            {pv.character_name ?? 'No presenter'} · {pv.settings.aspect} · {pv.scenes.length} scene{pv.scenes.length === 1 ? '' : 's'}
          </span>
        </header>

        <StageTimeline stages={pv.stages.map((s) => ({ ...s, status: s.status === 'review' ? 'done' : s.status })) as unknown as QuickStage[]} />
        {busy && pv.job?.message && (
          <p role="status" className="text-small text-studio-muted">
            {pv.job.message}
          </p>
        )}
        {failed?.detail && !busy && <ErrorState compact title={`${failed.label} stopped`} error={failed.detail} />}
        {review && !busy && (
          <p role="status" className="rounded-[6px] border border-studio-accent bg-studio-accent-soft px-3 py-2 text-small">
            Check each scene's frame below. Redraw any you don't like, then make the draft.
          </p>
        )}

        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="secondary" disabled={busy} loading={act.proceed.isPending} onClick={() => run('continue')}>
            Voice and frames
          </Button>
          <Button type="button" variant="primary" disabled={busy} loading={act.render.isPending} onClick={() => run('draft')}>
            <Clapperboard aria-hidden />
            Make the draft
          </Button>
          <Button type="button" variant="secondary" disabled={busy || !pv.outputs.draft} onClick={() => run('final')}>
            Make the final
          </Button>
          {dirty && (
            <Button type="button" variant="ghost" disabled={busy} loading={act.update.isPending} onClick={() => save()}>
              <Save aria-hidden />
              Save changes
            </Button>
          )}
        </div>
        {(act.update.error || act.proceed.error || act.render.error) && (
          <ErrorState compact title="That didn't go through" error={act.update.error ?? act.proceed.error ?? act.render.error} />
        )}

        {out && (
          <section aria-labelledby="pv-result" className="flex flex-col gap-2">
            <h2 id="pv-result" className="font-display text-title font-semibold">
              {pv.outputs.final ? 'Final' : 'Draft'}
            </h2>
            <ResultVideo mediaId={out.media_id} tall={tall} title={pv.title} />
          </section>
        )}

        <section aria-labelledby="pv-scenes" className="flex flex-col gap-3">
          <h2 id="pv-scenes" className="font-display text-title font-semibold">
            Scenes
          </h2>
          <ol className="flex flex-col gap-3">
            {draft.map((d, i) => (
              <SceneRow
                key={d.id ?? `new-${i}`}
                n={i + 1}
                draft={d}
                scene={pv.scenes.find((s) => s.id === d.id)}
                tall={tall}
                disabled={busy}
                onChange={(next) => setDraft((all) => all.map((x, k) => (k === i ? next : x)))}
                onRemove={draft.length > 1 ? () => setDraft((all) => all.filter((_, k) => k !== i)) : undefined}
                onRedraw={d.id ? () => act.redo.mutate({ sceneId: d.id!, what: 'frame' }, { onSuccess: () => announce(`Redrawing scene ${i + 1}.`) }) : undefined}
              />
            ))}
          </ol>
          <Button type="button" variant="ghost" size="sm" className="self-start" disabled={busy} onClick={() => setDraft((all) => [...all, { text: '', background: '' }])}>
            <Plus aria-hidden />
            Add a scene
          </Button>
        </section>
      </div>
    </main>
  )
}

function SceneRow({
  n,
  draft,
  scene,
  tall,
  disabled,
  onChange,
  onRemove,
  onRedraw,
}: {
  n: number
  draft: Draft
  scene?: PresenterScene
  tall: boolean
  disabled: boolean
  onChange: (d: Draft) => void
  onRemove?: () => void
  onRedraw?: () => void
}) {
  const uid = useId()
  const frame = scene?.frame
  const drawing = frame && ['queued', 'generating'].includes(frame.status)
  return (
    <li className="grid gap-3 rounded-[6px] border border-studio-border bg-studio-panel p-3 sm:grid-cols-[auto_minmax(0,1fr)]">
      <div className={cn('darkroom overflow-hidden rounded-[4px]', tall ? 'aspect-[9/16] w-28' : 'aspect-video w-48')}>
        {frame?.media_url && !drawing ? (
          <img src={frame.thumb_url || frame.media_url} alt={`Scene ${n} frame`} className="size-full object-cover" />
        ) : (
          <span className="flex size-full items-center justify-center p-1 text-center text-[11px] text-studio-muted">{drawing ? 'Drawing…' : 'No frame yet'}</span>
        )}
      </div>
      <div className="flex min-w-0 flex-col gap-2">
        <label htmlFor={`${uid}-text`} className="section-label">
          Scene {n}
          {scene?.duration_s ? ` · ${scene.duration_s.toFixed(1)} s spoken` : ''}
        </label>
        <Textarea id={`${uid}-text`} dir="auto" rows={2} value={draft.text} disabled={disabled} onChange={(e) => onChange({ ...draft, text: e.target.value })} />
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor={`${uid}-bg`} className="text-small text-studio-muted">
            Background
          </label>
          <select id={`${uid}-bg`} className={SELECT} value={draft.background} disabled={disabled} onChange={(e) => onChange({ ...draft, background: e.target.value })}>
            <option value="">Same as the video</option>
            {BACKGROUNDS.map((b) => (
              <option key={b.key} value={b.key}>
                {b.label}
              </option>
            ))}
          </select>
          <span className="ml-auto flex gap-1">
            {onRedraw && (
              <Button type="button" size="sm" variant="ghost" disabled={disabled} onClick={onRedraw} aria-label={`Redraw scene ${n}'s frame`}>
                <RefreshCw aria-hidden />
                Redraw
              </Button>
            )}
            {onRemove && (
              <Button type="button" size="icon" variant="ghost" disabled={disabled} onClick={onRemove} aria-label={`Remove scene ${n}`}>
                <Trash2 aria-hidden />
              </Button>
            )}
          </span>
        </div>
      </div>
    </li>
  )
}

function ResultVideo({ mediaId, tall, title }: { mediaId: string; tall: boolean; title: string }) {
  // the Library item's current version is the newest cut
  const url = useMediaItem(mediaId).data?.media_url ?? null
  return (
    <div className="flex flex-col gap-2">
      <div className={cn('darkroom mx-auto w-full overflow-hidden rounded-[6px]', tall ? 'aspect-[9/16] max-w-sm' : 'aspect-video max-w-3xl')}>
        {url && <video key={url} src={url} controls playsInline preload="metadata" aria-label={title} className="size-full object-contain" />}
      </div>
      <Button asChild variant="ghost" size="sm" className="self-start">
        <Link to={`/video/library?open=${mediaId}`}>
          <Library aria-hidden />
          Open in Library
        </Link>
      </Button>
    </div>
  )
}
