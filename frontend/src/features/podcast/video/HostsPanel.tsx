import { useId, useState } from 'react'
import { UserRound, Users } from 'lucide-react'
import { ImageSourceField } from '@/components/media/ImageSourceField'
import { Button } from '@/components/ui/button'
import { jobRunning, useApproveAiVideoGeneration, usePatchAiVideo, useRegenerateAiVideo } from '@/hooks/useVideoPodcast'
import type { VideoPodcast, VPHost, VPHostChoice, VPHostSource } from '@/lib/types'
import { announce } from '@/stores/ui'
import { LookSelect, StudioCharacterPicker } from '../StudioCharacterPicker'
import { CharacterPicker } from './CharacterPicker'
import { ConsentFields } from './ConsentFields'
import { GenActions, GenFrame } from './GenFrame'
import { StageAction } from './StageAction'
import { LIKENESS_STATEMENT, picture } from './vp'

const SOURCES: { value: VPHostSource; label: string }[] = [
  { value: 'persona', label: 'From persona' },
  { value: 'studio', label: 'From Character Studio' },
  { value: 'photo', label: 'Upload photo' },
  { value: 'existing', label: 'Use existing character' },
]

export function HostsPanel({ vp }: { vp: VideoPodcast }) {
  return (
    <>
      <div>
        <h2 className="font-display text-title font-semibold">Hosts</h2>
        <p className="text-small text-studio-muted">Each host gets a portrait, then a front and a 3/4 view to keep the face the same in every shot.</p>
      </div>
      <StageAction vp={vp} stage="cast" label="Make the portraits" hint="Pick where each face comes from, then make the portraits." />
      {vp.hosts.length ? (
        <ul aria-label="Hosts" className="grid gap-3 md:grid-cols-2">
          {vp.hosts.map((h) => (
            <HostCard key={h.host_id} vp={vp} host={h} />
          ))}
        </ul>
      ) : (
        <p className="text-small text-studio-muted">This episode has no hosts. Add them in the episode's Hosts step.</p>
      )}
    </>
  )
}

function HostCard({ vp, host }: { vp: VideoPodcast; host: VPHost }) {
  const uid = useId()
  const patch = usePatchAiVideo(vp.id)
  const regen = useRegenerateAiVideo(vp.id)
  const approve = useApproveAiVideoGeneration(vp.id)
  const [source, setSource] = useState<VPHostSource>(host.source)
  const [photo, setPhoto] = useState<string | null>(host.photo_media_id)
  const [who, setWho] = useState(host.consent?.name ?? '')
  const [agreed, setAgreed] = useState(false)
  const [asIs, setAsIs] = useState(!!host.photo_as_is)
  const [picking, setPicking] = useState(false)
  const [studioPicking, setStudioPicking] = useState(false)
  const [studio, setStudio] = useState<{ id: string; name: string } | null>(
    host.source === 'studio' && host.from_character_id ? { id: host.from_character_id, name: host.name } : null,
  )
  const [look, setLook] = useState<string | null>(host.source === 'studio' ? (host.look_id ?? null) : null)
  const locked = jobRunning(vp)

  const choose = (choice: VPHostChoice, done: string) =>
    patch.mutate({ hosts: { [host.host_id]: choice } }, { onSuccess: () => announce(done) })

  const photoBlocked = !photo ? 'Pick or upload a photo first.' : !who.trim() ? "Type the person's name." : !agreed ? 'Tick the consent statement to use this photo.' : null
  const applyPhoto = () => {
    if (photoBlocked || !photo || locked) return
    choose(
      { source: 'photo', media_id: photo, as_is: asIs, consent: { name: who.trim(), statement: LIKENESS_STATEMENT } },
      asIs ? `The photo is now ${host.name}'s approved portrait.` : `${host.name} will be made from the photo.`,
    )
  }

  const views = [
    ['front', 'Front', host.sheet_views.front],
    ['three_quarter', '3/4', host.sheet_views.three_quarter],
  ] as const

  return (
    <li className="flex flex-col gap-3 rounded-[6px] border border-studio-border-strong bg-studio-panel p-3 shadow-card">
      <div className="flex gap-3">
        <GenFrame gen={host.portrait} alt={`Portrait of ${host.name}`} aspect="aspect-[3/4]" className="w-32 shrink-0" />
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <h3 id={`${uid}-name`} className="font-display text-[15px] font-semibold" dir="auto">
            {host.name}
          </h3>
          <span className="text-[11px] uppercase tracking-wide text-studio-muted">{host.role}</span>
          {host.persona && (
            <p className="line-clamp-3 text-small text-studio-muted" dir="auto">
              {host.persona}
            </p>
          )}
          <GenActions
            gen={host.portrait}
            what={`portrait of ${host.name}`}
            emptyHint="Not made yet: press Make the portraits above."
            disabled={locked}
            regenerating={regen.isPending}
            approving={approve.isPending}
            onRegenerate={(note) => regen.mutate({ what: 'portrait', key: host.host_id, ...(note ? { note } : {}) }, { onSuccess: () => announce(`Making a new portrait of ${host.name}.`) })}
            onApprove={(id) => approve.mutate(id, { onSuccess: () => announce(`Portrait of ${host.name} approved.`) })}
          />
          {views.some(([, , g]) => picture(g)) && (
            <ul aria-label={`Sheet views of ${host.name}`} className="mt-1 flex gap-1.5">
              {views.map(([k, label, g]) =>
                picture(g) ? (
                  <li key={k} className="flex flex-col items-center gap-0.5">
                    <span className="darkroom block aspect-[3/4] w-12 overflow-hidden rounded-[3px]">
                      <img src={picture(g)!} alt={`${label} view of ${host.name}`} loading="lazy" className="size-full object-cover" />
                    </span>
                    <span className="text-[11px] text-studio-muted">{label}</span>
                  </li>
                ) : null,
              )}
            </ul>
          )}
        </div>
      </div>

      <fieldset className="flex flex-wrap gap-x-4 gap-y-1 text-small">
        <legend className="section-label mb-1">
          Face from<span className="sr-only"> for {host.name}</span>
        </legend>
        {SOURCES.map((s) => (
          <label key={s.value} className="flex items-center gap-1.5">
            <input type="radio" name={`${uid}-source`} value={s.value} checked={source === s.value} onChange={() => setSource(s.value)} className="accent-studio-accent" />
            {s.label}
          </label>
        ))}
      </fieldset>

      {source === 'persona' &&
        (host.source === 'persona' ? (
          <p className="text-small text-studio-muted">The look is written from the host's persona.</p>
        ) : (
          <Button type="button" size="sm" className="self-start" disabled={locked} loading={patch.isPending} onClick={() => choose({ source: 'persona' }, `${host.name} will be drawn from the persona.`)}>
            <UserRound aria-hidden />
            Use the persona
          </Button>
        ))}

      {source === 'photo' && (
        <div className="flex flex-col gap-2">
          <ImageSourceField label={`Photo of ${host.name}`} required value={photo} previewUrl={photo === host.photo_media_id ? host.photo_url : undefined} onChange={(id) => setPhoto(id)} aspectClass="aspect-[3/4]" />
          {host.source === 'photo' && host.consent && (
            <p className="text-[11px] text-studio-muted">
              Consent from {host.consent.name}
              {host.consent.at ? `, ${new Date(host.consent.at).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })}` : ''}
            </p>
          )}
          <label className="flex items-start gap-2 text-body">
            <input type="checkbox" checked={asIs} onChange={(e) => setAsIs(e.target.checked)} className="mt-1 size-4 accent-studio-accent" />
            <span>
              Use the photo itself as the portrait
              <span className="block text-small text-studio-muted">No AI clean-up: it's approved as it is, and only the side views are made from it.</span>
            </span>
          </label>
          <ConsentFields name={who} agreed={agreed} onName={setWho} onAgreed={setAgreed} />
          <div className="flex flex-wrap items-center gap-2">
            <Button type="button" size="sm" variant="primary" aria-disabled={!!photoBlocked || locked || undefined} aria-describedby={`${uid}-why`} loading={patch.isPending} onClick={applyPhoto}>
              Use this photo
            </Button>
            <span id={`${uid}-why`} aria-live="polite" className="text-small text-studio-muted">
              {photoBlocked ?? 'Ready.'}
            </span>
          </div>
        </div>
      )}

      {source === 'studio' && (
        <div className="flex flex-col gap-2">
          {host.source === 'studio' && <p className="text-small text-studio-muted">Wearing a look from Character Studio. Change it here, or in the episode's Hosts step.</p>}
          <div className="flex flex-wrap items-center gap-2">
            <Button type="button" size="sm" disabled={locked} onClick={() => setStudioPicking(true)}>
              <Users aria-hidden />
              {studio ? `Character: ${studio.name}` : 'Pick a character'}
            </Button>
          </div>
          {studio && <LookSelect characterId={studio.id} value={look} label={`Look for ${host.name}`} onChange={setLook} />}
          {studio && (
            <Button
              type="button"
              size="sm"
              variant="primary"
              className="self-start"
              disabled={locked}
              loading={patch.isPending}
              onClick={() => choose({ source: 'studio', character_id: studio.id, look_id: look }, `${host.name} will wear that look.`)}
            >
              Use this look
            </Button>
          )}
          <StudioCharacterPicker
            hostName={host.name}
            open={studioPicking}
            onOpenChange={setStudioPicking}
            onPick={(c) => {
              setStudioPicking(false)
              setStudio({ id: c.id, name: c.name })
              setLook(null)
            }}
          />
        </div>
      )}

      {source === 'existing' && (
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" size="sm" disabled={locked} onClick={() => setPicking(true)}>
            <Users aria-hidden />
            Pick a character
          </Button>
          {host.source === 'existing' && host.from_character_id && <span className="text-small text-studio-muted">Copied from a character in another project.</span>}
          <CharacterPicker
            vpId={vp.id}
            hostName={host.name}
            open={picking}
            onOpenChange={setPicking}
            onPick={(characterId) => {
              setPicking(false)
              choose({ source: 'existing', character_id: characterId }, `${host.name} will use that character.`)
            }}
          />
        </div>
      )}

      {[patch, regen, approve].map((m, i) =>
        m.isError ? (
          <p key={i} role="alert" className="text-small text-studio-danger">
            {m.error.message}
          </p>
        ) : null,
      )}
    </li>
  )
}
