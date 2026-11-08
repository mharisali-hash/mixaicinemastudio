import { useId, useState } from 'react'
import { Save } from 'lucide-react'
import { Chip } from '@/components/studio/chip'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { jobRunning, useApproveAiVideoGeneration, usePatchAiVideo, useRegenerateAiVideo } from '@/hooks/useVideoPodcast'
import type { VideoPodcast, VPAspect, VPSetPreset } from '@/lib/types'
import { announce } from '@/stores/ui'
import { GenActions, GenFrame } from './GenFrame'
import { StageAction } from './StageAction'
import { ASPECTS, SET_PRESETS, aspectClass } from './vp'

export function SetPanel({ vp }: { vp: VideoPodcast }) {
  const uid = useId()
  const patch = usePatchAiVideo(vp.id)
  const regen = useRegenerateAiVideo(vp.id)
  const approve = useApproveAiVideoGeneration(vp.id)
  const [prompt, setPrompt] = useState(vp.settings.set_prompt)
  const locked = jobRunning(vp)
  const preset = vp.settings.set_preset

  const save = (settings: Partial<VideoPodcast['settings']>, done: string) => !locked && patch.mutate({ settings }, { onSuccess: () => announce(done) })

  return (
    <>
      <div>
        <h2 className="font-display text-title font-semibold">Set</h2>
        <p className="text-small text-studio-muted">Where the hosts sit. Changing the set or the shape makes the set and the camera angles again; the hosts stay.</p>
      </div>
      <StageAction vp={vp} stage="set" label="Build the set" hint="Choose a set and the shape, then build it." />

      <section aria-labelledby={`${uid}-look`} className="flex flex-col gap-3 rounded-[6px] border border-studio-border-strong bg-studio-panel p-3 shadow-card">
        <h3 id={`${uid}-look`} className="font-display text-[15px] font-semibold">
          The room
        </h3>
        <div role="group" aria-labelledby={`${uid}-preset`}>
          <div id={`${uid}-preset`} className="section-label mb-1.5">
            Set
          </div>
          <div className="flex flex-wrap gap-1">
            {SET_PRESETS.map((p) => (
              <Chip key={p.value} selected={preset === p.value} disabled={locked} onClick={() => p.value !== preset && save({ set_preset: p.value as VPSetPreset }, `Set: ${p.label}.`)}>
                {p.label}
              </Chip>
            ))}
          </div>
        </div>
        <label className="flex flex-col gap-1 text-small">
          <span className="section-label">Anything to add (optional)</span>
          <Textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={2} dir="auto" placeholder="Warm lamps, bookshelves behind, a city window" />
        </label>
        <Button
          type="button"
          size="sm"
          className="self-start"
          disabled={locked || prompt.trim() === vp.settings.set_prompt.trim()}
          loading={patch.isPending && !!patch.variables?.settings && 'set_prompt' in patch.variables.settings}
          onClick={() => save({ set_prompt: prompt.trim() }, 'Set description saved.')}
        >
          <Save aria-hidden />
          Save the description
        </Button>
        <fieldset className="flex flex-wrap gap-3 text-small">
          <legend className="section-label mb-1">Shape</legend>
          {ASPECTS.map((a) => (
            <label key={a.value} className="flex items-center gap-1.5">
              <input
                type="radio"
                name={`${uid}-aspect`}
                value={a.value}
                checked={vp.settings.aspect === a.value}
                disabled={locked}
                onChange={() => save({ aspect: a.value as VPAspect }, `Shape: ${a.value}.`)}
                className="accent-studio-accent"
              />
              {a.label}
            </label>
          ))}
        </fieldset>
      </section>

      <section aria-labelledby={`${uid}-frame`} className="flex flex-col gap-2 rounded-[6px] border border-studio-border bg-studio-panel p-3">
        <h3 id={`${uid}-frame`} className="font-display text-[15px] font-semibold">
          Establishing frame
        </h3>
        {vp.set.description && (
          <p className="text-small text-studio-muted" dir="auto">
            {vp.set.description}
          </p>
        )}
        <GenFrame gen={vp.set.establishing} alt="The empty set" aspect={aspectClass(vp.settings.aspect)} className="max-w-xl" />
        <GenActions
          gen={vp.set.establishing}
          emptyHint="Not made yet: press Build the set above."
          what="the set"
          disabled={locked}
          regenerating={regen.isPending}
          approving={approve.isPending}
          onRegenerate={(note) => regen.mutate({ what: 'set', ...(note ? { note } : {}) }, { onSuccess: () => announce('Making the set again.') })}
          onApprove={(id) => approve.mutate(id, { onSuccess: () => announce('Set approved.') })}
        />
      </section>
      {[patch, regen, approve].map((m, i) =>
        m.isError ? (
          <p key={i} role="alert" className="text-small text-studio-danger">
            {m.error.message}
          </p>
        ) : null,
      )}
    </>
  )
}
