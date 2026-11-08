import { useRef } from 'react'
import { Mic, Pause, Play, Upload, Wand2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { ErrorState } from '@/components/studio/states'
import { useScript } from '@/hooks/useDialogue'
import { useVoiceChoices } from '@/hooks/useGallery'
import { useRecordLine, useRenderEpisode } from '@/hooks/usePodcast'
import { useUploads } from '@/hooks/useUploads'
import { changedLines, progressOf, type Script, type ScriptLine } from '@/lib/dialogue'
import { ROLE_LABEL, recordingLines, type Episode } from '@/lib/podcast'
import { acceptFor } from '@/lib/upload'
import { announce } from '@/stores/ui'
import { StatusPill } from '@/features/audio/dialogue/LineList'
import { togglePlay, useTrack } from '@/features/audio/sharedAudio'
import { Recorder, type RecordLimits } from '@/features/audio/voice/Recorder'

// a podcast line, not a voice reference: short is fine, long is fine
const LINE_LIMITS: RecordLimits = { min: 0.5, max: 180, goodMin: 2, goodMax: 60 }

export function VoiceStep({ ep }: { ep: Episode }) {
  const sq = useScript(ep.script_id ?? undefined)
  if (!ep.script_id) return <p className="text-small text-studio-muted">Write or paste the script first (step 3).</p>
  if (sq.isPending) return <Skeleton className="h-64" />
  if (sq.isError) return <ErrorState error={sq.error} onRetry={() => sq.refetch()} />
  return <Voicing ep={ep} script={sq.data} />
}

function Voicing({ ep, script }: { ep: Episode; script: Script }) {
  const render = useRenderEpisode(ep.id, script.id)
  const voices = useVoiceChoices()
  const changed = changedLines(script.lines)
  const { ready, total, rendering } = progressOf(script.lines)
  const failed = script.lines.filter((l) => l.status === 'failed')
  const mine = recordingLines(script.lines)
  const index = new Map(script.lines.map((l, i) => [l.id, i + 1]))
  const voiceName = (id: string | null) => (id ? (voices.find((v) => v.id === id)?.name ?? 'Chosen voice') : 'Default voice')

  const renderChanged = () =>
    render.mutate(
      changed.map((l) => l.id),
      { onSuccess: (r) => announce(r.failed ? `${r.queued} queued, ${r.failed} couldn't start.` : `Rendering ${r.queued} line${r.queued === 1 ? '' : 's'}.`) },
    )

  return (
    <>
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="font-display text-title font-semibold">Voice</h2>
          <p className="text-small text-studio-muted">AI hosts are rendered line by line; only lines that changed are rendered again. Record your own lines below.</p>
        </div>
        <Button type="button" variant="primary" disabled={changed.length === 0} loading={render.isPending} onClick={renderChanged}>
          <Wand2 aria-hidden />
          Render {changed.length} changed
        </Button>
      </div>
      <div className="flex flex-wrap items-center gap-3 text-[12px] text-studio-muted">
        <progress aria-label="Lines ready" value={ready} max={Math.max(1, total)} className="h-1.5 w-40 accent-studio-accent" />
        <span aria-live="polite">
          {ready} of {total} ready{rendering ? ` · ${rendering} rendering` : ''}
        </span>
      </div>
      {render.isError && (
        <p role="alert" className="text-small text-studio-danger">
          {render.error.message}
        </p>
      )}

      <ul aria-label="Who speaks how" className="flex flex-wrap gap-2">
        {ep.hosts.map((h) => (
          <li key={h.id} className="rounded-[6px] border border-studio-border bg-studio-panel px-2.5 py-1.5 text-small">
            <span className="font-medium">{h.name}</span> <span className="text-studio-muted">· {ROLE_LABEL[h.role]} · {h.me ? 'you record' : voiceName(h.voice_id)}</span>
          </li>
        ))}
      </ul>

      {failed.length > 0 && (
        <section aria-labelledby="failed-heading" className="rounded-[6px] border border-studio-danger/40 bg-studio-danger/5 p-3">
          <h3 id="failed-heading" className="text-body font-medium">
            {failed.length} line{failed.length === 1 ? '' : 's'} failed
          </h3>
          <ul className="mt-1 flex flex-col gap-1 text-small">
            {failed.map((l) => (
              <li key={l.id}>
                Line {index.get(l.id)}: {l.error ?? 'failed'}
              </li>
            ))}
          </ul>
        </section>
      )}

      {mine.length > 0 && (
        <section aria-labelledby="record-heading" className="flex flex-col gap-2">
          <h3 id="record-heading" className="section-label">
            Your lines ({mine.filter((l) => l.status === 'ready').length} of {mine.length} recorded)
          </h3>
          <ul className="flex flex-col gap-2">
            {mine.map((l) => (
              <RecordRow key={l.id} ep={ep} line={l} n={index.get(l.id) ?? 0} speaker={script.cast.find((c) => c.id === l.speaker_id)?.name ?? 'You'} />
            ))}
          </ul>
        </section>
      )}
    </>
  )
}

function RecordRow({ ep, line, n, speaker }: { ep: Episode; line: ScriptLine; n: number; speaker: string }) {
  const attach = useRecordLine(ep.id)
  const file = useRef<HTMLInputElement>(null)
  const uploads = useUploads(['audio'], (item) =>
    attach.mutate({ line_id: line.id, media_id: item.id }, { onSuccess: () => announce(`Line ${n} recorded.`) }),
  )
  const row = uploads.rows[uploads.rows.length - 1]
  const uploading = row?.state === 'uploading'
  const track = useTrack(`take-${line.id}`)
  return (
    <li className="flex flex-col gap-2 rounded-[6px] border border-studio-border bg-studio-panel p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Mic aria-hidden className="size-4 text-studio-muted" />
        <span className="text-small font-medium">
          Line {n} · {speaker}
        </span>
        <StatusPill status={line.status} error={line.error} />
        {line.take_url && (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            aria-label={`${track.playing ? 'Pause' : 'Play'} your take of line ${n}`}
            onClick={() => togglePlay(`take-${line.id}`, line.take_url!, { title: `${ep.title} · line ${n}`, mediaId: line.take_media_id ?? null })}
          >
            {track.playing ? <Pause aria-hidden /> : <Play aria-hidden />}
            Your take
          </Button>
        )}
      </div>
      <p className="rounded-[4px] bg-studio-raised px-2.5 py-1.5 text-body" dir="auto">
        {line.text || <span className="text-studio-muted">No words written for this line; say what fits.</span>}
      </p>
      <Recorder limits={LINE_LIMITS} hint="Read the line as written, close to the microphone; press Stop when you finish." disabled={uploading || attach.isPending} onRecorded={(f) => uploads.add([f])} />
      <div className="flex flex-wrap items-center gap-2">
        <input ref={file} type="file" accept={acceptFor(['audio'])} className="sr-only" aria-label={`Upload a clip for line ${n}`} onChange={(e) => e.target.files && uploads.add(e.target.files)} />
        <Button type="button" size="sm" variant="ghost" onClick={() => file.current?.click()} loading={uploading}>
          <Upload aria-hidden />
          Or upload a clip
        </Button>
        {row?.state === 'error' && <span role="alert" className="text-small text-studio-danger">{row.error}</span>}
        {attach.isError && <span role="alert" className="text-small text-studio-danger">{attach.error.message}</span>}
      </div>
    </li>
  )
}
