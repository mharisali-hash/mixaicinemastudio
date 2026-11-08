import { useEffect, useId } from 'react'
import { Link } from 'react-router'
import { UserRound } from 'lucide-react'
import { Skeleton } from '@/components/ui/skeleton'
import { useCharacterLooks } from '@/hooks/useCharacterLooks'
import { lookBusy, type CharacterLook } from '@/lib/looks'
import { cn } from '@/lib/utils'

// the server refuses a look whose picture isn't finished, so those can't be picked
const ready = (l: CharacterLook) => !!l.picture?.media_url && ['ready', 'approved'].includes(l.picture.status)

const startingLook = (looks: CharacterLook[]) => looks.find((l) => l.is_default && ready(l))?.id ?? null

const CARD = cn(
  'flex cursor-pointer items-center gap-2 rounded-[6px] border p-1.5 transition-colors duration-150',
  'border-studio-border-strong bg-studio-raised hover:bg-studio-panel-hover',
  'has-[:checked]:border-studio-accent has-[:checked]:bg-studio-accent-soft has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-studio-accent',
  'has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-60',
)

/**
 * Which of the character's Looks the Short is drawn from. `undefined` means "not chosen yet":
 * once the looks arrive it settles on the default look, the way the API does for a new Short.
 */
export function LookChoice({ characterId, value, onChange }: { characterId: string; value: string | null | undefined; onChange: (id: string | null) => void }) {
  const uid = useId()
  const q = useCharacterLooks(characterId)
  const looks = q.data?.looks

  useEffect(() => {
    if (value === undefined && looks) onChange(startingLook(looks))
  }, [value, looks, onChange])

  if (q.isPending) return <Skeleton className="h-20" />
  // no looks (or the list failed): the character sheet is used, nothing to choose
  if (!looks?.length)
    return (
      <p className="text-small text-studio-muted">
        Drawn from the character's sheet.{' '}
        <Link to={`/characters/${characterId}`} className="text-studio-accent-hover underline underline-offset-2">
          Add looks
        </Link>{' '}
        for outfits and settings.
      </p>
    )

  const radio = (v: string | null, describedBy?: string, disabled?: boolean) => (
    <input
      type="radio"
      name={`${uid}-look`}
      value={v ?? ''}
      checked={(value ?? null) === v}
      disabled={disabled}
      onChange={() => onChange(v)}
      aria-describedby={describedBy}
      className="sr-only"
    />
  )

  return (
    <fieldset className="flex flex-col gap-1.5">
      <legend className="section-label mb-1">Look</legend>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {looks.map((l) => {
          const ok = ready(l)
          const note = ok ? [l.outfit, l.setting].filter(Boolean).join(' · ') : lookBusy(l) ? 'Still being drawn' : 'No picture yet'
          return (
            <label key={l.id} className={CARD}>
              {radio(l.id, `${uid}-${l.id}`, !ok)}
              <span className="darkroom flex h-16 w-12 shrink-0 items-center justify-center overflow-hidden rounded-[4px]">
                {ok && <img src={l.picture!.thumb_url || l.picture!.media_url!} alt="" className="size-full object-cover" />}
              </span>
              <span className="flex min-w-0 flex-col">
                <span className="truncate text-small font-medium">
                  {l.name}
                  {l.is_default && <span className="font-normal text-studio-muted"> (default)</span>}
                </span>
                <span id={`${uid}-${l.id}`} className="line-clamp-2 text-[11px] leading-snug text-studio-muted">
                  {note}
                </span>
              </span>
            </label>
          )
        })}
        <label className={CARD}>
          {radio(null, `${uid}-sheet`)}
          <span className="flex h-16 w-12 shrink-0 items-center justify-center rounded-[4px] border border-dashed border-studio-border-strong text-studio-muted">
            <UserRound aria-hidden className="size-5" />
          </span>
          <span className="flex min-w-0 flex-col">
            <span className="text-small font-medium">Portrait (no look)</span>
            <span id={`${uid}-sheet`} className="text-[11px] leading-snug text-studio-muted">
              Their sheet, dressed as each scene asks
            </span>
          </span>
        </label>
      </div>
    </fieldset>
  )
}
