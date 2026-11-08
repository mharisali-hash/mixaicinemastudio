import { useState } from 'react'
import { Feather, Loader2, Minimize2, PenLine, Sparkles, Zap } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { ErrorState } from '@/components/studio/states'
import { AerabButton } from '@/components/text/AerabButton'
import { useScript } from '@/hooks/useDialogue'
import { useVoiceChoices } from '@/hooks/useGallery'
import { useEditLines, useRenderEpisode, useWriteEpisode } from '@/hooks/usePodcast'
import { useVoiceEngines } from '@/hooks/useVoices'
import { joinLines, splitLines } from '@/lib/aerab'
import { blankLine, moveLine, type Script } from '@/lib/dialogue'
import { WPM, busy, byChapter, minutesFor, wordCount, writeBlockedReason, type EditOp, type Episode } from '@/lib/podcast'
import { announce } from '@/stores/ui'
import { LineList, type LineActions } from '@/features/audio/dialogue/LineList'
import { useScriptDraft } from './useScriptDraft'

const noop = () => {}

export function ScriptStep({ ep }: { ep: Episode }) {
  const sq = useScript(ep.script_id ?? undefined)
  const write = useWriteEpisode(ep.id)
  const job = ep.jobs.write
  const writing = busy(job) || write.isPending
  const blocked = writeBlockedReason(ep)

  if (ep.script_id && sq.isPending) return <Skeleton className="h-64" />
  if (sq.isError) return <ErrorState error={sq.error} onRetry={() => sq.refetch()} />
  const script = sq.data
  const empty = !script || script.lines.length === 0

  return (
    <>
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="font-display text-title font-semibold">Script</h2>
          <p className="text-small text-studio-muted">
            The writer aims for about {Math.round(ep.target_minutes * WPM)} words ({ep.target_minutes} min at {WPM} words a minute).
          </p>
        </div>
        <Button
          type="button"
          variant={empty ? 'primary' : 'ghost'}
          aria-disabled={!!blocked || writing || undefined}
          loading={writing}
          title={blocked ?? undefined}
          onClick={() => !blocked && !writing && write.mutate(undefined, { onSuccess: () => announce('Writing the episode. It appears here when it’s done.') })}
        >
          <Feather aria-hidden />
          {empty ? 'Write the episode' : 'Write it again'}
        </Button>
      </div>
      {blocked && empty && <p className="text-small text-studio-warning">{blocked}</p>}
      <JobLine job={job} />
      {write.isError && (
        <p role="alert" className="text-small text-studio-danger">
          {write.error.message}
        </p>
      )}
      {script && !empty ? (
        <Editor key={script.id} ep={ep} script={script} locked={writing} />
      ) : (
        !writing && (
          <p className="rounded-[6px] border border-dashed border-studio-border-strong p-6 text-center text-small text-studio-muted">
            No lines yet. Write the episode from your topic and material, or paste your own script in Source.
          </p>
        )
      )}
    </>
  )
}

function JobLine({ job }: { job: Episode['jobs']['write'] }) {
  if (!job) return null
  if (busy(job))
    return (
      <p role="status" className="flex items-center gap-2 text-small text-studio-muted">
        <Loader2 aria-hidden className="size-4 animate-spin" />
        {job.message || 'Working'}
        {job.progress ? ` · ${Math.round(job.progress * 100)}%` : ''}
      </p>
    )
  if (job.status === 'failed')
    return (
      <p role="alert" className="text-small text-studio-danger">
        The writer stopped: {job.error || 'it failed'}
      </p>
    )
  return null
}

function Editor({ ep, script, locked }: { ep: Episode; script: Script; locked: boolean }) {
  const draft = useScriptDraft(script)
  const voices = useVoiceChoices()
  const { engines } = useVoiceEngines()
  const render = useRenderEpisode(ep.id, script.id)
  const editLines = useEditLines(ep.id)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [note, setNote] = useState('')
  const lines = draft.lines
  const groups = byChapter(lines, ep.chapters)
  const count = wordCount(lines)
  const minutes = minutesFor(count)
  const selected = lines.filter((l) => picked.has(l.id))
  const chapterOfPick = selected.length ? (selected[0].chapter_id ?? null) : null
  const oneChapter = selected.length > 0 && chapterOfPick !== null && selected.every((l) => l.chapter_id === chapterOfPick)

  const toggle = (id: string) =>
    setPicked((p) => {
      const next = new Set(p)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const run = (op: EditOp, body: { line_ids?: string[]; chapter_id?: string }) =>
    draft
      .flush()
      .then(() => editLines.mutateAsync({ op, ...body, note: note.trim() || undefined }))
      .then(() => {
        setPicked(new Set())
        announce(op === 'rewrite_chapter' ? 'Rewriting the chapter.' : op === 'shorter' ? 'Shortening the lines.' : 'Livening up the lines.')
      })
      .catch(() => {})

  const actionsFor = (start: number, size: number): LineActions => ({
    change: (id, patch) => draft.edit((d) => ({ ...d, lines: d.lines.map((x) => (x.id === id ? { ...x, ...patch } : x)) })),
    insertAfter: (index, text = '') => {
      const at = start + index
      const near = lines[at]
      const line = { ...blankLine(near?.speaker_id), text, chapter_id: near?.chapter_id ?? null, kind: near?.kind === 'recording' ? ('recording' as const) : undefined }
      draft.edit((d) => {
        const next = d.lines.slice()
        next.splice(at + 1, 0, line)
        return { ...d, lines: next }
      })
      return line.id
    },
    remove: (index) => {
      const line = lines[start + index]
      if (!line) return
      draft.edit((d) => ({ ...d, lines: d.lines.filter((x) => x.id !== line.id) }))
      announce(`Line ${start + index + 1} removed.`)
    },
    move: (from, to) => {
      // a line moves within its chapter; the chapter headers stay where they are
      if (to < 0 || to >= size) return
      draft.edit((d) => ({ ...d, lines: moveLine(d.lines, start + from, start + to) }))
    },
    render: (id) => {
      draft
        .flush()
        .then(() => render.mutateAsync([id]))
        .then((r) => announce(r.queued ? 'Rendering the line.' : 'Nothing to render for that line.'))
        .catch(() => {})
    },
  })

  const setAllTexts = (marked: string) => {
    const next = splitLines(lines.map((l) => l.text), marked)
    if (!next) return
    const byId = new Map(lines.map((l, i) => [l.id, next[i]]))
    draft.edit((d) => ({ ...d, lines: d.lines.map((x) => (byId.has(x.id) && byId.get(x.id) !== x.text ? { ...x, text: byId.get(x.id)! } : x)) }))
  }

  const problem = editLines.error ?? render.error ?? draft.error
  return (
    <div className="flex flex-col gap-3">
      <div role="toolbar" aria-label="Edit with the writer" className="sticky top-0 z-10 flex flex-wrap items-center gap-2 rounded-[6px] border border-studio-border bg-studio-panel p-2 shadow-card">
        <span className="text-[12px] text-studio-muted">
          <span className="font-mono text-studio-text">{count}</span> words ≈ <span className="font-mono text-studio-text">{minutes}</span> min of {ep.target_minutes}
        </span>
        <span className="text-[12px] text-studio-muted" aria-live="polite">
          {selected.length ? `${selected.length} selected` : 'Tick lines to edit them with the writer'}
        </span>
        <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note for the writer (optional)" aria-label="Note for the writer" className="h-7 w-56" />
        <div className="ml-auto flex flex-wrap gap-1.5">
          <AerabButton text={joinLines(lines.map((l) => l.text))} onText={setAllTexts} language={ep.language} label="Add aerab to all lines" showNote={false} disabled={locked} />
          <Button type="button" size="sm" disabled={locked || !oneChapter} title={oneChapter ? undefined : 'Tick lines in one chapter'} onClick={() => run('rewrite_chapter', { chapter_id: chapterOfPick! })}>
            <PenLine aria-hidden />
            Rewrite chapter
          </Button>
          <Button type="button" size="sm" disabled={locked || !selected.length} onClick={() => run('shorter', { line_ids: selected.map((l) => l.id) })}>
            <Minimize2 aria-hidden />
            Shorter
          </Button>
          <Button type="button" size="sm" disabled={locked || !selected.length} onClick={() => run('livelier', { line_ids: selected.map((l) => l.id) })}>
            <Zap aria-hidden />
            Livelier
          </Button>
        </div>
      </div>
      {problem && (
        <p role="alert" className="text-small text-studio-danger">
          {problem.message}
        </p>
      )}
      {groups.map((g) => (
        <section key={g.chapter?.id ?? `none-${g.start}`} aria-label={g.chapter?.title ?? 'Opening'} className="overflow-hidden rounded-[6px] border border-studio-border bg-studio-panel">
          <header className="flex items-center gap-2 border-b border-studio-border bg-studio-raised px-3 py-1.5">
            <h3 className="font-display text-[15px] font-semibold" dir="auto">
              {g.chapter?.title ?? 'Opening'}
            </h3>
            <span className="text-[11px] text-studio-muted">
              {g.lines.length} line{g.lines.length === 1 ? '' : 's'} · ≈ {minutesFor(wordCount(g.lines))} min
            </span>
            {g.chapter && (
              <Button type="button" size="sm" variant="ghost" className="ml-auto" disabled={locked} aria-label={`Rewrite chapter ${g.chapter.title}`} onClick={() => run('rewrite_chapter', { chapter_id: g.chapter!.id })}>
                <Sparkles aria-hidden />
                Rewrite
              </Button>
            )}
          </header>
          <LineList
            lines={g.lines}
            cast={draft.cast}
            voices={voices}
            engines={engines}
            language={ep.language}
            title={ep.title}
            actions={actionsFor(g.start, g.lines.length)}
            focusText={null}
            onFocused={noop}
            selection={{ ids: picked, toggle }}
            offset={g.start}
          />
        </section>
      ))}
      <p className="text-[11px] text-studio-faint">On a line: ↑ ↓ move · Alt+↑ ↓ reorder within the chapter · Enter new line · Ctrl+Enter render · Delete remove · F2 edit</p>
    </div>
  )
}
