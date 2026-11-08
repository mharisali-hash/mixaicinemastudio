import { useId, useState } from 'react'
import { Check, ImageIcon, PencilLine, RefreshCw } from 'lucide-react'
import { StatusPill } from '@/components/studio/status-pill'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { generationStatus } from '@/lib/status'
import type { Generation } from '@/lib/types'
import { cn } from '@/lib/utils'
import { picture } from './vp'

/** A portrait, set or angle picture on its darkroom frame, with its status underneath. */
export function GenFrame({ gen, alt, aspect = 'aspect-video', className }: { gen: Generation | null | undefined; alt: string; aspect?: string; className?: string }) {
  const src = picture(gen)
  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      <div className={cn('darkroom overflow-hidden rounded-[6px]', aspect)}>
        {src ? (
          <img src={src} alt={alt} loading="lazy" className="size-full object-cover" />
        ) : (
          <span className="flex size-full items-center justify-center text-studio-on-dark-muted">
            <ImageIcon aria-hidden className="size-6" />
            <span className="sr-only">{alt}: not made yet</span>
          </span>
        )}
      </div>
      <StatusPill className="self-start" status={gen ? generationStatus(gen.status) : { label: 'Not made yet', tone: 'neutral', icon: 'clock' }} />
    </div>
  )
}

/** Regenerate (as it was, or with a note on what to change), and Approve while the newest one waits for review.
 * `what` names it for screen readers. */
export function GenActions({
  gen,
  what,
  disabled,
  onRegenerate,
  onApprove,
  regenerating,
  approving,
  emptyHint,
}: {
  gen: Generation | null | undefined
  what: string
  disabled?: boolean
  onRegenerate: (note?: string) => void
  onApprove: (id: string) => void
  regenerating?: boolean
  approving?: boolean
  // shown instead of the buttons until the first one has been made by the stage's own button
  emptyHint?: string
}) {
  const uid = useId()
  const [editing, setEditing] = useState(false)
  const [note, setNote] = useState('')
  if (!gen) return emptyHint ? <p className="text-[11px] text-studio-muted">{emptyHint}</p> : null
  const send = () => {
    onRegenerate(note.trim())
    setEditing(false)
    setNote('')
  }
  return (
    <div className="flex flex-wrap gap-1.5">
      <Button type="button" size="sm" variant="secondary" disabled={disabled} loading={regenerating && !editing} aria-label={`Regenerate ${what}`} onClick={() => onRegenerate()}>
        <RefreshCw aria-hidden />
        Regenerate
      </Button>
      <Button type="button" size="sm" variant="ghost" disabled={disabled} aria-expanded={editing} aria-controls={`${uid}-note`} aria-label={`Regenerate ${what} with changes`} onClick={() => setEditing((v) => !v)}>
        <PencilLine aria-hidden />
        With changes
      </Button>
      {gen?.status === 'ready' && (
        <Button type="button" size="sm" variant="primary" disabled={disabled} loading={approving} aria-label={`Approve ${what}`} onClick={() => onApprove(gen.id)}>
          <Check aria-hidden />
          Approve
        </Button>
      )}
      {editing && (
        <form
          id={`${uid}-note`}
          className="flex w-full flex-col gap-1.5"
          onSubmit={(e) => {
            e.preventDefault()
            if (note.trim()) send()
          }}
        >
          <label htmlFor={`${uid}-text`} className="text-[11px] text-studio-muted">
            What should change? For example: remove the man in the foreground, only one person in the picture.
          </label>
          <Textarea id={`${uid}-text`} rows={2} value={note} onChange={(e) => setNote(e.target.value)} autoFocus />
          <Button type="submit" size="sm" variant="primary" className="self-start" disabled={disabled || !note.trim()} loading={regenerating}>
            <RefreshCw aria-hidden />
            Regenerate with this change
          </Button>
        </form>
      )}
    </div>
  )
}
