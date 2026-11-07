import { useId, useState } from 'react'
import { Link } from 'react-router'
import { Dices, Lock, LockOpen, Save, Sparkles } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Chip } from '@/components/studio/chip'
import { EmptyState, ErrorState } from '@/components/studio/states'
import { GeneratorPage, ResultsHeading } from '@/components/generate/GeneratorPage'
import { GenerateButton } from '@/components/generate/GenerateButton'
import { PromptDock } from '@/components/generate/PromptDock'
import { useMediaHost } from '@/components/generate/useMediaHost'
import { flatItems, useMediaList } from '@/hooks/useMedia'
import { useCreateVoice, useDesignPreview, useVoiceEngines } from '@/hooks/useVoices'
import {
  DEFAULT_DESIGN_FORM,
  DESIGN_PRESETS,
  OMNIVOICE_WORDS,
  designBlockedReason,
  designEngines,
  designPayload,
  designVoiceBody,
  languageName,
  sampleLine,
  SCRIPT_MAX,
  type DesignForm,
  type DesignTake,
} from '@/lib/voice'
import type { MediaItem, Voice } from '@/lib/types'
import { trackJobs } from '@/stores/toasts'
import { announce } from '@/stores/ui'
import { AudioTile } from '../AudioTile'
import { EngineChip, SpeechLanguageChip } from './VoiceChips'

interface Take extends DesignTake {
  item: MediaItem
  n: number
}

export function DesignPage() {
  const uid = useId()
  const [form, setForm] = useState<DesignForm>(DEFAULT_DESIGN_FORM)
  const [takes, setTakes] = useState<Take[]>([])
  const [saving, setSaving] = useState<Take | null>(null)
  const [heard, setHeard] = useState<{ understood: string[]; ignored: string[] } | null>(null)
  const { engines } = useVoiceEngines()
  const able = designEngines(engines)
  const engine = able.find((e) => e.id === form.engine) ?? able[0]
  const effective = engine && engine.id !== form.engine ? { ...form, engine: engine.id } : form
  const preview = useDesignPreview()
  const blocked = designBlockedReason(effective, engines)
  const set = <K extends keyof DesignForm>(k: K, v: DesignForm[K]) => setForm((f) => ({ ...f, [k]: v }))
  const listed = flatItems(useMediaList({ kind: 'audio', origin: 'generated' }).data)
  const live = (m: MediaItem) => listed.find((x) => x.id === m.id) ?? m
  const { handlers, host } = useMediaHost({ items: takes.map((t) => live(t.item)) })

  const setLanguage = (code: string) =>
    setForm((f) => {
      // swap the sample line too, unless it was rewritten by hand
      const sample = f.text === sampleLine(f.language) ? sampleLine(code) : f.text
      return { ...f, language: code, text: sample }
    })

  const submit = () => {
    if (blocked || preview.isPending) return
    const body = designPayload(effective)
    preview.mutate(body, {
      onSuccess: (res) => {
        const item = res.items?.[0]
        trackJobs(res.jobs ?? [], 'Voice preview')
        if (!item) return
        const take: Take = { item, itemId: item.id, n: takes.length + 1, description: body.description, seed: res.seed, engine: res.engine_resolved ?? body.engine ?? effective.engine, language: body.language }
        setTakes((t) => [take, ...t])
        // the seed comes back either way, so "Keep this voice" can hold on to it
        setForm((f) => ({ ...f, seed: String(res.seed) }))
        setHeard(res.understood || res.ignored ? { understood: res.understood ?? [], ignored: res.ignored ?? [] } : null)
        announce(`Preview ${take.n} is on its way, seed ${res.seed}. It appears below; press play to hear it.`)
      },
    })
  }

  return (
    <GeneratorPage label="Design a voice" targets={[]}>
      <PromptDock
        title="Design a voice"
        icon={<Sparkles aria-hidden className="size-4 text-studio-accent-hover" />}
        headerExtra={<p className="text-small text-studio-muted max-lg:hidden">Describe who is speaking; keep the seed to hold the identity while you tweak.</p>}
        promptLabel="Describe the voice"
        prompt={form.description}
        onPrompt={(v) => set('description', v)}
        mentions={false}
        placeholder="e.g. male, elderly, low pitch, indian accent, slow"
        belowPrompt={
          <div className="flex flex-col gap-3">
            {effective.engine === 'omnivoice' && (
              <p className="text-small text-studio-muted">
                OmniVoice understands: {OMNIVOICE_WORDS}. Other words are left out; the seed gives each voice its own character.
              </p>
            )}
            {heard && (
              <p role="status" className="text-small">
                <span className="text-studio-muted">Last preview used </span>
                {heard.understood.length ? heard.understood.join(', ') : 'no attributes (a voice of its own choosing)'}
                {heard.ignored.length > 0 && <span className="text-studio-muted"> · left out: {heard.ignored.join(', ')}</span>}
              </p>
            )}
            <div role="group" aria-label="Start from a preset" className="flex flex-wrap gap-1">
              {DESIGN_PRESETS.map((p) => (
                <Chip key={p.id} selected={form.description === p.description} onClick={() => set('description', p.description)}>
                  {p.label}
                </Chip>
              ))}
            </div>
            <div className="grid gap-3 border-t border-studio-border pt-3 md:grid-cols-[1fr_auto]">
              <div>
                <Label htmlFor={`${uid}-text`} className="mb-1.5">
                  Preview line
                </Label>
                <Input
                  id={`${uid}-text`}
                  dir="auto"
                  value={form.text}
                  maxLength={SCRIPT_MAX}
                  onChange={(e) => set('text', e.target.value)}
                  placeholder={`The server's sample line in ${languageName(form.language, engines)}`}
                />
              </div>
              <div>
                <Label htmlFor={`${uid}-seed`} className="mb-1.5">
                  Seed
                </Label>
                <div className="flex items-center gap-1.5">
                  <Input
                    id={`${uid}-seed`}
                    inputMode="numeric"
                    value={form.seed}
                    onChange={(e) => {
                      const v = e.target.value.replace(/\D/g, '').slice(0, 10)
                      // a typed seed is one to keep
                      setForm((f) => ({ ...f, seed: v, lockSeed: v ? true : f.lockSeed }))
                    }}
                    placeholder="New each time"
                    className="w-32 font-mono"
                  />
                  <Button
                    type="button"
                    size="sm"
                    aria-pressed={form.lockSeed}
                    disabled={!form.seed}
                    onClick={() => {
                      set('lockSeed', !form.lockSeed)
                      announce(form.lockSeed ? 'Seed released: the next preview is a new voice.' : `Keeping this voice: seed ${form.seed}.`)
                    }}
                    className="aria-pressed:border-studio-accent aria-pressed:bg-studio-accent-soft"
                  >
                    {form.lockSeed ? <Lock aria-hidden /> : <LockOpen aria-hidden />}
                    Keep this voice
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setForm((f) => ({ ...f, seed: '', lockSeed: false }))
                      announce('The next preview is a new voice.')
                    }}
                  >
                    <Dices aria-hidden />
                    New voice
                  </Button>
                </div>
              </div>
            </div>
          </div>
        }
        chips={
          <>
            <SpeechLanguageChip languages={engine?.languages ?? []} engines={engines} value={form.language} onChange={setLanguage} />
            <EngineChip engines={able} value={engine?.id ?? ''} onChange={(id) => set('engine', id)} language={form.language} voice={undefined} auto={false} />
          </>
        }
        error={preview.error ? <ErrorState compact title="Couldn't make the preview" error={preview.error} /> : null}
        footer={
          <GenerateButton
            verb="Preview"
            what={form.lockSeed && form.seed ? `seed ${form.seed}` : 'a new voice'}
            estimate={null}
            icon={<Sparkles aria-hidden />}
            blocked={blocked}
            note="Nothing plays until you press play."
            pending={preview.isPending}
          />
        }
        onSubmit={submit}
      />

      <ResultsHeading>Previews this session</ResultsHeading>
      {takes.length === 0 ? (
        <EmptyState icon={<Sparkles />} title="No previews yet">
          Describe a voice or tap a preset, then Preview. Each one lands here with its seed, ready to save.
        </EmptyState>
      ) : (
        <ol aria-label="Previews" className="flex flex-col divide-y divide-studio-border rounded-[8px] border border-studio-border bg-studio-panel">
          {takes.map((t) => (
            <li key={t.item.id} aria-label={`Preview ${t.n}, seed ${t.seed}`} className="grid items-center gap-3 p-3 md:grid-cols-[minmax(0,1fr)_minmax(0,18rem)_auto]">
              <div className="min-w-0">
                <p className="flex items-baseline gap-2 text-small">
                  <span className="font-mono font-semibold">#{t.n}</span>
                  <span className="font-mono text-studio-muted">seed {t.seed}</span>
                  <span className="text-studio-faint">
                    {engines.find((e) => e.id === t.engine)?.label ?? t.engine} · {languageName(t.language, engines)}
                  </span>
                </p>
                <p className="line-clamp-2 text-small text-studio-muted">{t.description}</p>
              </div>
              <AudioTile item={live(t.item)} handlers={handlers} ratio="4 / 1" />
              <div className="flex flex-wrap gap-1.5 md:flex-col">
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setForm((f) => ({ ...f, seed: String(t.seed), lockSeed: true, description: t.description }))
                    announce(`Seed ${t.seed} kept; tweak the description and preview again.`)
                  }}
                >
                  <Lock aria-hidden />
                  Keep this seed
                </Button>
                <Button type="button" size="sm" onClick={() => setSaving(t)}>
                  <Save aria-hidden />
                  Save as voice
                </Button>
              </div>
            </li>
          ))}
        </ol>
      )}
      {host}
      <SaveVoiceDialog take={saving} onClose={() => setSaving(null)} />
    </GeneratorPage>
  )
}

function SaveVoiceDialog({ take, onClose }: { take: Take | null; onClose: () => void }) {
  return (
    <Dialog open={!!take} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-md">{take && <SaveVoiceForm key={take.item.id} take={take} onClose={onClose} />}</DialogContent>
    </Dialog>
  )
}

function SaveVoiceForm({ take, onClose }: { take: Take; onClose: () => void }) {
  const uid = useId()
  const [name, setName] = useState('')
  const [saved, setSaved] = useState<Voice | null>(null)
  const create = useCreateVoice()
  const save = () => {
    if (!name.trim() || create.isPending) return
    create.mutate(designVoiceBody(name, take), {
      onSuccess: (v) => {
        setSaved(v)
        announce(`${v.name} saved to your voices with seed ${take.seed}.`)
      },
    })
  }
  if (saved) {
    return (
      <>
        <DialogHeader>
          <DialogTitle>{saved.name} is saved</DialogTitle>
          <DialogDescription>The description and seed {take.seed} are kept, so it sounds the same every time.</DialogDescription>
        </DialogHeader>
        <DialogFooter className="items-center">
          <Link to="/audio/voices" className="mr-auto text-small text-studio-muted underline-offset-2 hover:text-studio-text hover:underline">
            Open Voices
          </Link>
          <Button asChild variant="primary">
            <Link to={`/audio/speak?voice=${saved.id}`}>Speak with it</Link>
          </Button>
        </DialogFooter>
      </>
    )
  }
  return (
    <form
      className="contents"
      onSubmit={(e) => {
        e.preventDefault()
        save()
      }}
    >
      <DialogHeader>
        <DialogTitle>Save as voice</DialogTitle>
        <DialogDescription>
          Preview #{take.n}, seed {take.seed}. It joins your voices for Speak and Dialogue.
        </DialogDescription>
      </DialogHeader>
      <div>
        <Label htmlFor={`${uid}-name`} className="mb-1.5">
          Voice name
        </Label>
        <Input id={`${uid}-name`} value={name} maxLength={80} onChange={(e) => setName(e.target.value)} placeholder="e.g. Dada jaan" autoFocus />
      </div>
      {create.error && <ErrorState compact title="Couldn't save the voice" error={create.error} className="mt-3" />}
      <DialogFooter>
        <Button type="button" variant="secondary" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" disabled={!name.trim()} loading={create.isPending}>
          Save voice
        </Button>
      </DialogFooter>
    </form>
  )
}
