import { useId, useState } from 'react'
import { Link } from 'react-router'
import { Captions, Download, FileAudio, ImageIcon, Library, NotebookPen, Send, Sparkles, Video } from 'lucide-react'
import { MediaPicker } from '@/components/media/MediaPicker'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { useNotes, usePatchEpisode, usePublish, useVideoPodcast } from '@/hooks/usePodcast'
import { api } from '@/lib/api'
import { busy, stamp, type Episode, type Notes } from '@/lib/podcast'
import { cn } from '@/lib/utils'
import { announce } from '@/stores/ui'
import { AiVideoEntry } from './video/AiVideoEntry'

type Fmt = 'mp3' | 'm4a'
type Aspect = '16:9' | '1:1'

export function PublishStep({ ep, onVideo }: { ep: Episode; onVideo: () => void }) {
  const uid = useId()
  const patch = usePatchEpisode(ep.id)
  const publish = usePublish(ep.id)
  const video = useVideoPodcast(ep.id)
  const [fmt, setFmt] = useState<Fmt>((ep.outputs.publish?.format as Fmt) ?? 'mp3')
  const [aspect, setAspect] = useState<Aspect>((ep.outputs.video?.aspect as Aspect) ?? '16:9')
  const [burn, setBurn] = useState(true)
  const [pickCover, setPickCover] = useState(false)
  const mix = ep.outputs.mix
  const pub = ep.outputs.publish
  const vid = ep.outputs.video
  const publishing = busy(ep.jobs.publish) || publish.isPending
  const making = busy(ep.jobs.video) || video.isPending
  const noMix = !mix ? 'Mix the episode first (step 5)' : null

  return (
    <>
      <div>
        <h2 className="font-display text-title font-semibold">Publish</h2>
        <p className="text-small text-studio-muted">A tagged audio file with cover art and chapter markers, show notes, subtitles, and a video version for YouTube and socials.</p>
      </div>
      {noMix && <p className="rounded-[6px] border border-studio-warning/50 bg-studio-warning/5 p-3 text-small">{noMix}. Then come back here.</p>}
      {mix?.stale && <p className="text-small text-studio-warning">The mix is out of date with the script or the music: mix again in Produce before publishing.</p>}

      <section aria-labelledby={`${uid}-audio`} className="flex flex-col gap-3 rounded-[6px] border border-studio-border-strong bg-studio-panel p-3 shadow-card">
        <h3 id={`${uid}-audio`} className="font-display text-[15px] font-semibold">
          Audio file
        </h3>
        <div className="flex flex-wrap items-start gap-4">
          <div className="flex flex-col items-start gap-1.5">
            {ep.cover_url ? (
              <img src={ep.cover_url} alt="Cover art" className="size-28 rounded-[4px] border border-studio-border object-cover" />
            ) : (
              <span className="flex size-28 items-center justify-center rounded-[4px] border border-dashed border-studio-border-strong text-center text-[11px] text-studio-muted">
                {ep.show_name ? "The show's cover, or a lettered one" : 'A lettered cover'}
              </span>
            )}
            <Button type="button" size="sm" variant="ghost" onClick={() => setPickCover(true)}>
              <ImageIcon aria-hidden />
              {ep.cover_url ? 'Change cover' : 'Pick a cover'}
            </Button>
          </div>
          <div className="flex min-w-60 flex-1 flex-col gap-2">
            <fieldset className="flex flex-wrap gap-3 text-small">
              <legend className="section-label mb-1">Format</legend>
              {(['mp3', 'm4a'] as Fmt[]).map((f) => (
                <label key={f} className="flex items-center gap-1.5">
                  <input type="radio" name={`${uid}-fmt`} value={f} checked={fmt === f} onChange={() => setFmt(f)} className="accent-studio-accent" />
                  {f === 'mp3' ? 'MP3 (every app)' : 'M4A (Apple, smaller)'}
                </label>
              ))}
            </fieldset>
            <p className="text-[11px] text-studio-muted">Tagged with the title, show, author and description; {mix?.chapters.length ? `${mix.chapters.length} chapter markers` : 'chapter markers'} from the mix.</p>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                type="button"
                variant="primary"
                aria-disabled={!!noMix || publishing || undefined}
                loading={publishing}
                onClick={() => !noMix && !publishing && publish.mutate({ format: fmt, notes: true }, { onSuccess: () => announce(`Publishing the ${fmt.toUpperCase()}.`) })}
              >
                <Send aria-hidden />
                Publish {fmt.toUpperCase()}
              </Button>
              {pub?.url && (
                <>
                  <Button asChild size="sm" variant="outline">
                    <a href={pub.url} download>
                      <FileAudio aria-hidden />
                      Download {pub.format?.toUpperCase()}
                    </a>
                  </Button>
                  <Button asChild size="sm" variant="ghost">
                    <Link to="/audio/library">
                      <Library aria-hidden />
                      In the Library
                    </Link>
                  </Button>
                </>
              )}
            </div>
            <JobNote job={ep.jobs.publish} what="Publishing" />
            {publish.isError && (
              <p role="alert" className="text-small text-studio-danger">
                {publish.error.message}
              </p>
            )}
          </div>
        </div>
      </section>

      <NotesEditor key={JSON.stringify(ep.notes)} ep={ep} onSave={(notes) => patch.mutate({ notes }, { onSuccess: () => announce('Show notes saved.') })} saving={patch.isPending} />

      <section aria-labelledby={`${uid}-subs`} className="flex flex-wrap items-center gap-2 rounded-[6px] border border-studio-border bg-studio-panel p-3">
        <h3 id={`${uid}-subs`} className="mr-auto font-display text-[15px] font-semibold">
          Subtitles
        </h3>
        <span className="text-[11px] text-studio-muted">From the mix's own line timings, no transcription pass</span>
        <Button asChild size="sm" variant="outline" className={cn(noMix && 'pointer-events-none opacity-50')}>
          <a href={noMix ? undefined : api.podcast.srtUrl(ep.id)} download aria-disabled={!!noMix || undefined}>
            <Captions aria-hidden />
            Download SRT
          </a>
        </Button>
      </section>

      <section aria-labelledby={`${uid}-video`} className="flex flex-col gap-3 rounded-[6px] border border-studio-border-strong bg-studio-panel p-3 shadow-card">
        <h3 id={`${uid}-video`} className="font-display text-[15px] font-semibold">
          Audiogram
        </h3>
        <p className="text-[11px] text-studio-muted">A quick share video: cover, waveform, speaker names and subtitles</p>
        <div className="flex flex-wrap items-center gap-4 text-small">
          <fieldset className="flex gap-3">
            <legend className="sr-only">Shape</legend>
            {(['16:9', '1:1'] as Aspect[]).map((a) => (
              <label key={a} className="flex items-center gap-1.5">
                <input type="radio" name={`${uid}-aspect`} value={a} checked={aspect === a} onChange={() => setAspect(a)} className="accent-studio-accent" />
                {a === '16:9' ? '16:9 (YouTube)' : '1:1 (socials)'}
              </label>
            ))}
          </fieldset>
          <label className="flex items-center gap-1.5">
            <input type="checkbox" checked={burn} onChange={(e) => setBurn(e.target.checked)} className="accent-studio-accent" />
            Burn in subtitles
          </label>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="primary"
            aria-disabled={!!noMix || making || undefined}
            loading={making}
            onClick={() => !noMix && !making && video.mutate({ aspect, burn_subtitles: burn }, { onSuccess: () => announce('Making the audiogram.') })}
          >
            <Video aria-hidden />
            Make the audiogram
          </Button>
          {vid?.url && (
            <>
              <Button asChild size="sm" variant="outline">
                <a href={vid.url} download>
                  <Download aria-hidden />
                  Download MP4 ({vid.aspect})
                </a>
              </Button>
              <Button asChild size="sm" variant="ghost">
                <Link to="/video/library">
                  <Library aria-hidden />
                  In the Library
                </Link>
              </Button>
              {vid.burned === false && burn && <span className="text-[11px] text-studio-warning">The subtitles couldn't be burned in; use the SRT.</span>}
            </>
          )}
        </div>
        <JobNote job={ep.jobs.video} what="Rendering the audiogram" />
        {video.isError && (
          <p role="alert" className="text-small text-studio-danger">
            {video.error.message}
          </p>
        )}
      </section>

      {ep.medium === 'audio' && <AiVideoEntry ep={ep} onVideo={onVideo} />}

      <MediaPicker
        open={pickCover}
        onOpenChange={setPickCover}
        kind="image"
        max={1}
        title="Pick the cover art"
        description="Square works best; anything else is cropped to its centre."
        confirmLabel="Use as cover"
        onConfirm={(items) => {
          if (items[0]) patch.mutate({ cover_media_id: items[0].id })
          setPickCover(false)
        }}
      />
    </>
  )
}

function JobNote({ job, what }: { job: Episode['jobs']['publish']; what: string }) {
  if (!job) return null
  if (busy(job))
    return (
      <p role="status" className="text-small text-studio-muted">
        {job.message || what}
      </p>
    )
  if (job.status === 'failed')
    return (
      <p role="alert" className="text-small text-studio-danger">
        {what} failed: {job.error}
      </p>
    )
  return null
}

function NotesEditor({ ep, onSave, saving }: { ep: Episode; onSave: (n: Notes) => void; saving: boolean }) {
  const uid = useId()
  const regen = useNotes(ep.id)
  const chapters = ep.notes?.chapters?.length ? ep.notes.chapters : (ep.outputs.mix?.chapters ?? []).map((c) => ({ title: c.title, start: c.start }))
  const [description, setDescription] = useState(ep.notes?.description ?? '')
  const [points, setPoints] = useState((ep.notes?.key_points ?? []).join('\n'))
  const writing = busy(ep.jobs.write) || regen.isPending
  const save = () => onSave({ description: description.trim(), key_points: points.split('\n').map((p) => p.trim()).filter(Boolean), chapters })
  return (
    <section aria-labelledby={`${uid}-notes`} className="flex flex-col gap-3 rounded-[6px] border border-studio-border bg-studio-panel p-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 id={`${uid}-notes`} className="mr-auto font-display text-[15px] font-semibold">
          Show notes
        </h3>
        <Button
          type="button"
          size="sm"
          aria-disabled={!ep.outputs.mix || writing || undefined}
          loading={writing}
          title={ep.outputs.mix ? undefined : 'Mix the episode first'}
          onClick={() => ep.outputs.mix && !writing && regen.mutate(undefined, { onSuccess: () => announce('Writing the show notes.') })}
        >
          <Sparkles aria-hidden />
          {ep.notes ? 'Write them again' : 'Write them for me'}
        </Button>
        <Button asChild size="sm" variant="ghost">
          <a href={api.podcast.notesUrl(ep.id)} download>
            <Download aria-hidden />
            Markdown
          </a>
        </Button>
      </div>
      <label className="flex flex-col gap-1 text-small">
        <span className="section-label">Description</span>
        <Textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={3} maxLength={8000} dir="auto" />
      </label>
      <label className="flex flex-col gap-1 text-small">
        <span className="section-label">Key points, one per line</span>
        <Textarea value={points} onChange={(e) => setPoints(e.target.value)} rows={4} dir="auto" />
      </label>
      <div className="flex flex-col gap-1 text-small">
        <span className="section-label">Chapters</span>
        {chapters.length ? (
          <ol aria-label="Chapters" className="flex flex-col gap-0.5">
            {chapters.map((c, i) => (
              <li key={i} className="flex gap-2">
                <span className="w-14 font-mono text-studio-muted">{stamp(c.start)}</span>
                <span dir="auto">{c.title}</span>
              </li>
            ))}
          </ol>
        ) : (
          <p className="text-studio-muted">Chapter times come from the mix.</p>
        )}
      </div>
      <Button type="button" size="sm" variant="primary" className="self-start" loading={saving} onClick={save}>
        <NotebookPen aria-hidden />
        Save notes
      </Button>
      {regen.isError && (
        <p role="alert" className="text-small text-studio-danger">
          {regen.error.message}
        </p>
      )}
    </section>
  )
}
