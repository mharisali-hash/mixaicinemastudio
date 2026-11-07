import { useId, useState } from 'react'
import { Plus, RefreshCw, Shirt, Star, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { EmptyState, ErrorState } from '@/components/studio/states'
import { useSaveCharacter } from '@/hooks/useCharacterStudio'
import { useAddLook, useCharacterLooks, useLookPack, useRedrawLook, useRemoveLook, useSetDefaultLook } from '@/hooks/useCharacterLooks'
import { EXPRESSIVENESS, lookBusy, type CharacterLook, type Expressiveness, type LookPackId } from '@/lib/looks'
import type { Character } from '@/lib/types'
import { cn } from '@/lib/utils'
import { announce } from '@/stores/ui'

/** A character's looks (outfit, setting, pose), Look Packs, and how they move on camera. */
export function LooksTab({ character }: { character: Character }) {
  const looks = useCharacterLooks(character.id)
  const pack = useLookPack(character.id)
  const noPortrait = !character.portrait

  return (
    <div className="flex flex-col gap-6">
      <section aria-labelledby="looks-title" className="flex flex-col gap-3">
        <div>
          <h2 id="looks-title" className="font-display text-title font-semibold">
            Looks
          </h2>
          <p className="text-small text-studio-muted">
            The same face in different outfits, places and poses. Pick a look in Poetry Short or a film shot and {character.name} wears it.
          </p>
        </div>
        {noPortrait && (
          <p role="status" className="text-small text-studio-warning">
            Make {character.name}'s portrait first: every look is drawn from it.
          </p>
        )}
        <div role="group" aria-label="Look packs" className="flex flex-wrap items-center gap-1.5">
          <span className="section-label mr-1">Add a pack of 4</span>
          {(looks.data?.packs ?? []).map((p) => (
            <Button
              key={p.id}
              type="button"
              size="sm"
              variant="secondary"
              disabled={noPortrait || pack.isPending}
              title={p.looks.join(', ')}
              onClick={() => pack.mutate(p.id as LookPackId, { onSuccess: () => announce(`${p.label} looks are being drawn.`) })}
            >
              {p.label}
            </Button>
          ))}
        </div>
        {pack.error && <ErrorState compact title="Couldn't add the pack" error={pack.error} />}
        {looks.isPending ? (
          <Skeleton className="h-48" />
        ) : looks.isError ? (
          <ErrorState compact error={looks.error} onRetry={() => looks.refetch()} />
        ) : looks.data.looks.length === 0 ? (
          <EmptyState icon={<Shirt />} title="No looks yet">
            Add a pack above, or describe one below.
          </EmptyState>
        ) : (
          <ul aria-label="Looks" className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
            {looks.data.looks.map((l) => (
              <LookCard key={l.id} look={l} characterId={character.id} />
            ))}
          </ul>
        )}
        <AddLook characterId={character.id} disabled={noPortrait} />
      </section>
      <PerformanceFields character={character} />
    </div>
  )
}

function LookCard({ look, characterId }: { look: CharacterLook; characterId: string }) {
  const makeDefault = useSetDefaultLook(characterId)
  const remove = useRemoveLook(characterId)
  const redraw = useRedrawLook(characterId)
  const busy = lookBusy(look)
  const pic = look.picture
  const failed = pic?.status === 'failed'
  const details = [look.outfit, look.setting, look.pose].filter(Boolean).join(' · ')
  return (
    <li className={cn('flex flex-col overflow-hidden rounded-[6px] border bg-studio-panel', look.is_default ? 'border-studio-accent' : 'border-studio-border')}>
      <div className="darkroom relative aspect-[3/4]">
        {pic?.media_url && !busy ? (
          <img src={pic.thumb_url || pic.media_url} alt={`${look.name}: ${details}`} className="size-full object-cover" />
        ) : (
          <div role="status" className="flex size-full items-center justify-center p-2 text-center text-small text-studio-muted">
            {busy ? 'Drawing…' : failed ? "Couldn't draw it" : 'No picture yet'}
          </div>
        )}
        {look.is_default && (
          <span className="absolute left-1.5 top-1.5 rounded-[4px] bg-studio-accent px-1.5 py-0.5 text-[11px] font-medium text-studio-on-gold">Default</span>
        )}
      </div>
      <div className="flex flex-1 flex-col gap-1 p-2">
        <span className="text-small font-medium">{look.name}</span>
        {details && <span className="line-clamp-2 text-[11px] text-studio-muted">{details}</span>}
        <div className="mt-auto flex gap-0.5 pt-1">
          {!look.is_default && (
            <Button type="button" size="icon" variant="ghost" aria-label={`Make ${look.name} the default look`} onClick={() => makeDefault.mutate(look.id)}>
              <Star aria-hidden />
            </Button>
          )}
          <Button type="button" size="icon" variant="ghost" aria-label={`Draw ${look.name} again`} disabled={busy} onClick={() => redraw.mutate({ lid: look.id })}>
            <RefreshCw aria-hidden />
          </Button>
          <Button
            type="button"
            size="icon"
            variant="ghost"
            aria-label={`Delete ${look.name}`}
            onClick={() => remove.mutate(look.id, { onSuccess: () => announce(`${look.name} deleted.`) })}
          >
            <Trash2 aria-hidden />
          </Button>
        </div>
      </div>
    </li>
  )
}

function AddLook({ characterId, disabled }: { characterId: string; disabled: boolean }) {
  const uid = useId()
  const add = useAddLook(characterId)
  const [f, setF] = useState({ name: '', outfit: '', setting: '', pose: '' })
  const set = (k: keyof typeof f, v: string) => setF((x) => ({ ...x, [k]: v }))
  const ready = f.name.trim() && (f.outfit.trim() || f.setting.trim() || f.pose.trim())
  return (
    <form
      aria-label="Add a look"
      className="grid gap-2 rounded-[6px] border border-studio-border bg-studio-raised p-3 sm:grid-cols-2"
      onSubmit={(e) => {
        e.preventDefault()
        if (!ready || disabled) return
        add.mutate(f, {
          onSuccess: () => {
            announce(`${f.name} is being drawn.`)
            setF({ name: '', outfit: '', setting: '', pose: '' })
          },
        })
      }}
    >
      <h3 className="section-label sm:col-span-2">Your own look</h3>
      {(
        [
          ['name', 'Name', 'Eid morning'],
          ['outfit', 'Outfit', 'a white kurta with a light waistcoat'],
          ['setting', 'Setting', 'a sunny courtyard with potted plants'],
          ['pose', 'Pose', 'standing, smiling at the camera'],
        ] as const
      ).map(([k, label, ph]) => (
        <div key={k} className="flex flex-col gap-1">
          <Label htmlFor={`${uid}-${k}`}>{label}</Label>
          <Input id={`${uid}-${k}`} value={f[k]} placeholder={ph} maxLength={k === 'name' ? 120 : 600} onChange={(e) => set(k, e.target.value)} />
        </div>
      ))}
      <div className="flex items-center gap-2 sm:col-span-2">
        <Button type="submit" variant="primary" size="sm" loading={add.isPending} aria-disabled={!ready || disabled || undefined}>
          <Plus aria-hidden />
          Add look
        </Button>
        <span aria-live="polite" className="text-small text-studio-muted">
          {disabled ? '' : ready ? '' : 'Give it a name and at least an outfit, a setting or a pose.'}
        </span>
      </div>
      {add.error && <ErrorState compact title="Couldn't add the look" error={add.error} />}
    </form>
  )
}

function PerformanceFields({ character }: { character: Character }) {
  const uid = useId()
  const save = useSaveCharacter()
  const perf = character.performance ?? {}
  const [expr, setExpr] = useState<Expressiveness | ''>(perf.expressiveness ?? '')
  const [gestures, setGestures] = useState(perf.gestures ?? '')
  const changed = expr !== (perf.expressiveness ?? '') || gestures !== (perf.gestures ?? '')
  return (
    <section aria-labelledby={`${uid}-perf`} className="flex max-w-xl flex-col gap-3">
      <div>
        <h2 id={`${uid}-perf`} className="font-display text-title font-semibold">
          On camera
        </h2>
        <p className="text-small text-studio-muted">How {character.name} moves when talking, in test clips, podcasts and presenter videos.</p>
      </div>
      <fieldset className="flex flex-wrap gap-2">
        <legend className="section-label mb-1">Expressiveness</legend>
        {EXPRESSIVENESS.map((e) => (
          <label
            key={e.value}
            className="flex cursor-pointer flex-col rounded-[6px] border border-studio-border-strong bg-studio-raised px-2.5 py-1.5 has-[:checked]:border-studio-accent has-[:checked]:bg-studio-accent-soft has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-studio-accent"
          >
            <input type="radio" name={`${uid}-expr`} value={e.value} checked={expr === e.value} onChange={() => setExpr(e.value)} className="sr-only" />
            <span className="text-small font-medium">{e.label}</span>
            <span className="text-[11px] text-studio-muted">{e.hint}</span>
          </label>
        ))}
      </fieldset>
      <div className="flex flex-col gap-1">
        <Label htmlFor={`${uid}-gestures`}>Gestures and movement</Label>
        <Textarea id={`${uid}-gestures`} rows={2} maxLength={500} value={gestures} placeholder="uses hands while explaining, nods often" onChange={(e) => setGestures(e.target.value)} />
      </div>
      <Button
        type="button"
        size="sm"
        className="self-start"
        disabled={!changed}
        loading={save.isPending}
        onClick={() =>
          save.mutate(
            { id: character.id, performance: { expressiveness: expr || null, gestures } },
            { onSuccess: () => announce('Saved how they move on camera.') },
          )
        }
      >
        Save
      </Button>
      {save.error && <ErrorState compact title="Couldn't save" error={save.error} />}
    </section>
  )
}
