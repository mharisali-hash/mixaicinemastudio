import { useId, useState } from 'react'
import { Link } from 'react-router'
import { Download, Film, Library, Scissors } from 'lucide-react'
import { CaptionStylePicker, type CaptionStyle } from '@/components/generate/CaptionStylePicker'
import { EmptyState } from '@/components/studio/states'
import { Button } from '@/components/ui/button'
import { jobRunning, useAssembleAiVideo, usePatchAiVideo } from '@/hooks/useVideoPodcast'
import { formatTimecode } from '@/lib/duration'
import type { VideoPodcast, VPQuality, VPSubtitles } from '@/lib/types'
import { cn } from '@/lib/utils'
import { announce } from '@/stores/ui'
import { aspectClass } from './vp'

const SUBTITLES: { value: VPSubtitles; label: string }[] = [
  { value: 'none', label: 'None' },
  { value: 'soft', label: 'Soft (viewers turn them on)' },
  { value: 'burn', label: 'Burned in' },
]

export function ResultPanel({ vp, onRender }: { vp: VideoPodcast; onRender: () => void }) {
  const uid = useId()
  const { draft, final } = vp.outputs
  const [quality, setQuality] = useState<VPQuality>(final ? 'final' : 'draft')
  const out = vp.outputs[quality] ?? final ?? draft
  const [subs, setSubs] = useState<VPSubtitles>(vp.settings.subtitles)
  const [captions, setCaptions] = useState<CaptionStyle>(vp.settings.caption_style ?? 'classic')
  const [intro, setIntro] = useState(vp.settings.cover_intro)
  const [outro, setOutro] = useState(vp.settings.cover_outro)
  const patch = usePatchAiVideo(vp.id)
  const assemble = useAssembleAiVideo(vp.id)
  const locked = jobRunning(vp)
  const changed = subs !== vp.settings.subtitles || captions !== (vp.settings.caption_style ?? 'classic') || intro !== vp.settings.cover_intro || outro !== vp.settings.cover_outro

  if (!out)
    return (
      <EmptyState
        icon={<Film />}
        title="Nothing cut yet"
        action={
          <Button type="button" variant="primary" onClick={onRender}>
            Go to Render
          </Button>
        }
      >
        Render a draft first. It's saved to the Library and plays here.
      </EmptyState>
    )

  const recut = () => {
    if (locked) return
    const settings = { subtitles: subs, caption_style: captions, cover_intro: intro, cover_outro: outro }
    const cut = () => assemble.mutate(out.quality, { onSuccess: () => announce(`Cutting the ${out.quality} again.`) })
    if (changed) patch.mutate({ settings }, { onSuccess: cut })
    else cut()
  }

  return (
    <>
      <div>
        <h2 className="font-display text-title font-semibold">Result</h2>
        <p className="text-small text-studio-muted">The cut video over the episode mix, saved to the Library.</p>
      </div>
      {draft && final && (
        <fieldset className="flex gap-3 text-small">
          <legend className="sr-only">Which version</legend>
          {(['draft', 'final'] as VPQuality[]).map((q) => (
            <label key={q} className="flex items-center gap-1.5">
              <input type="radio" name={`${uid}-v`} value={q} checked={quality === q} onChange={() => setQuality(q)} className="accent-studio-accent" />
              {q === 'draft' ? 'Draft' : 'Final'}
            </label>
          ))}
        </fieldset>
      )}
      <figure className="flex flex-col gap-2">
        <div className={cn('darkroom mx-auto w-full overflow-hidden rounded-[6px]', vp.settings.aspect === '9:16' ? 'max-w-sm' : 'max-w-3xl', aspectClass(vp.settings.aspect))}>
          <video key={out.url} src={out.url} controls playsInline preload="metadata" aria-label={`${vp.title}, ${out.quality}`} className="size-full object-contain" />
        </div>
        <figcaption className="text-center text-[11px] text-studio-muted">
          {out.quality === 'final' ? 'Final' : 'Draft'} · {formatTimecode(out.duration_s)} · {out.width}×{out.height}
          {out.subtitles !== 'none' ? ` · ${out.subtitles === 'burn' ? 'subtitles burned in' : 'soft subtitles'}` : ''}
        </figcaption>
      </figure>
      <div className="flex flex-wrap justify-center gap-2">
        <Button asChild size="sm" variant="primary">
          <a href={out.url} download>
            <Download aria-hidden />
            Download MP4
          </a>
        </Button>
        <Button asChild size="sm" variant="outline">
          <Link to={`/video/library?open=${out.media_id}`}>
            <Library aria-hidden />
            Open in Library
          </Link>
        </Button>
      </div>
      {out.fonts_missing.length > 0 && (
        <p role="status" className="text-small text-studio-warning">
          The server lacks fonts for {out.fonts_missing.join(', ')}, so some subtitles may show boxes. Install the fonts, then cut again.
        </p>
      )}
      {out.fallback_shots.length > 0 && (
        <p role="status" className="text-small text-studio-warning">
          {out.fallback_shots.length === 1 ? `Cut ${out.fallback_shots[0]} uses draft footage` : `Cuts ${out.fallback_shots.join(', ')} use draft footage`}: render the final again to replace it.
        </p>
      )}

      <section aria-labelledby={`${uid}-cut`} className="flex flex-col gap-3 rounded-[6px] border border-studio-border bg-studio-panel p-3">
        <h3 id={`${uid}-cut`} className="font-display text-[15px] font-semibold">
          Subtitles and cover
        </h3>
        <fieldset className="flex flex-wrap gap-3 text-small">
          <legend className="section-label mb-1">Subtitles</legend>
          {SUBTITLES.map((s) => (
            <label key={s.value} className="flex items-center gap-1.5">
              <input type="radio" name={`${uid}-subs`} value={s.value} checked={subs === s.value} onChange={() => setSubs(s.value)} className="accent-studio-accent" />
              {s.label}
            </label>
          ))}
        </fieldset>
        {subs === 'burn' && <CaptionStylePicker value={captions} onChange={setCaptions} />}
        <div className="flex flex-wrap gap-4 text-small">
          <label className="flex items-center gap-1.5">
            <input type="checkbox" checked={intro} onChange={(e) => setIntro(e.target.checked)} className="accent-studio-accent" />
            Show the cover over the intro
          </label>
          <label className="flex items-center gap-1.5">
            <input type="checkbox" checked={outro} onChange={(e) => setOutro(e.target.checked)} className="accent-studio-accent" />
            Show the cover over the outro
          </label>
        </div>
        <Button type="button" size="sm" className="self-start" disabled={locked} loading={patch.isPending || assemble.isPending} onClick={recut}>
          <Scissors aria-hidden />
          Cut the {out.quality} again
        </Button>
        <p className="text-[11px] text-studio-muted">Uses the camera tracks already made: no GPU time.</p>
        {(patch.error || assemble.error) && (
          <p role="alert" className="text-small text-studio-danger">
            {(patch.error ?? assemble.error)?.message}
          </p>
        )}
      </section>
    </>
  )
}
