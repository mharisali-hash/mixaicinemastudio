import { useId } from 'react'
import { Link } from 'react-router'
import { User } from 'lucide-react'
import { EmptyState, ErrorState } from '@/components/studio/states'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Skeleton } from '@/components/ui/skeleton'
import { useCharacterLooks } from '@/hooks/useCharacterLooks'
import { useStudioCharacters } from '@/hooks/useCharacterStudio'
import type { CharacterLook } from '@/lib/looks'
import type { Character } from '@/lib/types'
import { SELECT } from '@/features/audio/voice/AddVoiceDialog'

/** Character Studio's characters as portrait cards; picking one hands back the whole character. */
export function StudioCharacterPicker({
  hostName,
  open,
  onOpenChange,
  onPick,
}: {
  hostName: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onPick: (c: Character) => void
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="font-display text-title font-semibold">A character for {hostName}</DialogTitle>
          <DialogDescription className="text-small text-studio-muted">
            Their name and voice come along, and on camera they wear the look you pick.
          </DialogDescription>
        </DialogHeader>
        {open && <Cards onPick={onPick} />}
      </DialogContent>
    </Dialog>
  )
}

function Cards({ onPick }: { onPick: (c: Character) => void }) {
  const q = useStudioCharacters()
  if (q.isPending)
    return (
      <div className="grid grid-cols-3 gap-2">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="aspect-[3/4]" />
        ))}
      </div>
    )
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />
  if (!q.data.length)
    return (
      <EmptyState icon={<User />} title="No characters yet">
        Make one in <Link to="/characters" className="underline">Character Studio</Link> and it shows up here.
      </EmptyState>
    )
  return (
    <ul aria-label="Characters" className="grid grid-cols-2 gap-2 sm:grid-cols-3">
      {q.data.map((c) => {
        const img = c.portrait?.thumb_url ?? c.portrait?.media_url ?? c.board_thumb_url
        return (
          <li key={c.id}>
            <button
              type="button"
              onClick={() => onPick(c)}
              className="flex w-full flex-col overflow-hidden rounded-[6px] border border-studio-border-strong bg-studio-panel text-left shadow-card transition-colors duration-150 hover:bg-studio-panel-hover focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-studio-accent"
            >
              <span className="darkroom block aspect-square border-x-0 border-t-0">
                {img ? (
                  <img src={img} alt="" className="size-full object-cover" loading="lazy" />
                ) : (
                  <span className="flex size-full items-center justify-center text-studio-on-dark-muted">
                    <User aria-hidden className="size-8" />
                  </span>
                )}
              </span>
              <span className="flex flex-col gap-0.5 p-2">
                <span className="truncate font-display text-panel font-semibold">{c.name}</span>
                {c.description && <span className="line-clamp-2 text-small text-studio-muted">{c.description}</span>}
              </span>
            </button>
          </li>
        )
      })}
    </ul>
  )
}

/** Which of a character's looks it wears; "" is the plain portrait. Looks still being drawn can't be picked yet. */
export function LookSelect({ characterId, value, onChange, label }: { characterId: string; value: string | null; onChange: (lookId: string | null) => void; label: string }) {
  const uid = useId()
  const q = useCharacterLooks(characterId)
  const looks = q.data?.looks ?? []
  const ready = (l: CharacterLook) => !!l.picture && ['ready', 'approved'].includes(l.picture.status)
  const picked = looks.find((l) => l.id === value)
  const thumb = picked?.picture?.thumb_url
  return (
    <div className="flex items-end gap-2">
      {thumb && <img src={thumb} alt="" className="size-12 shrink-0 rounded-[4px] object-cover" />}
      <label htmlFor={`${uid}-look`} className="flex min-w-0 flex-1 flex-col gap-1 text-small">
        <span className="section-label">{label}</span>
        <select id={`${uid}-look`} value={value ?? ''} disabled={q.isPending} onChange={(e) => onChange(e.target.value || null)} className={SELECT}>
          <option value="">Portrait</option>
          {looks.map((l) => (
            <option key={l.id} value={l.id} disabled={!ready(l)}>
              {l.name}
              {ready(l) ? '' : ' (still being drawn)'}
            </option>
          ))}
        </select>
      </label>
    </div>
  )
}
