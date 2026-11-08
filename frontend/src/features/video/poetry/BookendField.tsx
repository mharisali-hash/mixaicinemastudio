import { useState } from 'react'
import { Film, Library, X } from 'lucide-react'
import { MediaPicker } from '@/components/media/MediaPicker'
import { Button } from '@/components/ui/button'
import { useMediaItem } from '@/hooks/useMedia'

/** An optional Library picture or clip shown before (intro) or after (outro) the song. */
export function BookendField({ which, value, onChange }: { which: 'intro' | 'outro'; value: string | null; onChange: (id: string | null) => void }) {
  const [picking, setPicking] = useState(false)
  const item = useMediaItem(value).data
  const label = which === 'intro' ? 'Intro' : 'Outro'
  const thumb = item?.thumb_url || (item?.kind === 'image' ? item.media_url : null)
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="w-14 text-small font-medium">{label}</span>
      {value ? (
        <span className="flex min-w-0 flex-1 items-center gap-2">
          <span className="darkroom flex size-10 shrink-0 items-center justify-center overflow-hidden rounded-[4px]">
            {thumb ? <img src={thumb} alt="" className="size-full object-cover" /> : <Film aria-hidden className="size-4 text-studio-muted" />}
          </span>
          <span className="min-w-0 truncate text-small" dir="auto">
            {item ? `${item.title || 'Untitled'} · ${item.kind === 'video' ? 'clip' : 'picture'}` : 'Loading…'}
          </span>
          <Button type="button" size="icon-sm" variant="ghost" aria-label={`Remove the ${which}`} onClick={() => onChange(null)}>
            <X aria-hidden />
          </Button>
        </span>
      ) : (
        <span className="flex-1 text-small text-studio-faint">None</span>
      )}
      <Button type="button" size="sm" variant="secondary" onClick={() => setPicking(true)} aria-label={`${value ? 'Change' : 'Choose'} the ${which}`}>
        <Library aria-hidden />
        {value ? 'Change' : 'Choose'}
      </Button>
      <MediaPicker
        open={picking}
        onOpenChange={setPicking}
        kind="image"
        kinds={['image', 'video']}
        max={1}
        title={`Pick the ${which}`}
        description={`A picture or a short clip shown ${which === 'intro' ? 'before' : 'after'} the song.`}
        confirmLabel="Use this"
        onConfirm={(items) => items[0] && onChange(items[0].id)}
      />
    </div>
  )
}
