import { useId } from 'react'
import { Music, Music4, Volume2 } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Chip } from '@/components/studio/chip'
import { ErrorState } from '@/components/studio/states'
import { SwitchRow } from '@/components/studio/switch-row'
import { ModelChip } from '@/components/generate/DockChips'
import { GenerateButton } from '@/components/generate/GenerateButton'
import { PromptDock } from '@/components/generate/PromptDock'
import { useDockModels } from '@/components/generate/useDockModels'
import { flatItems, useMediaList } from '@/hooks/useMedia'
import { useGenEstimate } from '@/hooks/useGenEstimate'
import {
  ACE_ID,
  audioBlockedReason,
  audioPayload,
  BPM_MAX,
  BPM_MIN,
  clampAudioDuration,
  instrumental,
  KEYSCALES,
  KIND_NOUN,
  modelFits,
  SFX_PRESETS,
  SONG_LANGUAGES,
  songLanguageOf,
  VOCALS,
  type AudioForm,
} from '@/lib/audio'
import { formatDuration } from '@/lib/duration'
import { localEstimate } from '@/lib/estimate'
import { mediaAlt } from '@/lib/media'
import { has } from '@/lib/models'
import type { AudioGenerateRequest, AudioKind } from '@/lib/types'
import { announce } from '@/stores/ui'
import { AudioLengthChip, CategoryChip, LanguageChip, TakesChip, VocalChip } from './AudioChips'
import { LyricsEditor } from './LyricsEditor'

const ADV_SELECT = 'h-8 w-full rounded-[6px] border border-studio-border-strong bg-studio-raised px-2 text-body'

export interface AudioRun {
  form: AudioForm
  body: AudioGenerateRequest
  summary: string
}

const COPY: Record<AudioKind, { title: string; icon: React.ReactNode; promptLabel: string; placeholder: string; hint: string; verb: string }> = {
  song: {
    title: 'Song',
    icon: <Music aria-hidden className="size-4 text-studio-accent-hover" />,
    promptLabel: 'Style tags',
    placeholder: 'e.g. soulful qawwali, harmonium, tabla, male vocal, 92 BPM',
    hint: 'Genre, instruments, voice and tempo. The words go in Lyrics below.',
    verb: 'Compose',
  },
  music: {
    title: 'Music & Score',
    icon: <Music4 aria-hidden className="size-4 text-studio-accent-hover" />,
    promptLabel: 'Describe the music',
    placeholder: 'e.g. tense cinematic score, low strings, ticking clock, 70 BPM',
    hint: 'Mood, genre, instruments and BPM.',
    verb: 'Compose',
  },
  sfx: {
    title: 'Sound Effects',
    icon: <Volume2 aria-hidden className="size-4 text-studio-accent-hover" />,
    promptLabel: 'Describe the sound',
    placeholder: 'e.g. heavy rain on a tin roof, distant thunder',
    hint: 'One sound or one place at a time works best.',
    verb: 'Generate',
  },
}

const SELECT = 'h-8 w-full rounded-[6px] border border-studio-border-strong bg-studio-raised px-2 text-body'

interface Props {
  form: AudioForm
  setForm: React.Dispatch<React.SetStateAction<AudioForm>>
  modelPicked?: boolean
  onRun: (r: AudioRun) => void
  pending?: boolean
  error?: unknown
}

export function AudioDock({ form, setForm, modelPicked, onRun, pending, error }: Props) {
  const uid = useId()
  const kind = form.kind
  const copy = COPY[kind]
  const noun = KIND_NOUN[kind]
  const set = <K extends keyof AudioForm>(k: K, v: AudioForm[K]) => setForm((f) => ({ ...f, [k]: v }))
  const { models, model, send, effective } = useDockModels('audio', form.model, { only: (all) => all.filter((m) => modelFits(m, kind)), prompt: form.prompt })
  const duration = clampAudioDuration(form.durationS, kind, effective)
  const estimate = useGenEstimate(
    effective ? { kind: 'audio', model: effective.id, duration_s: duration, count: form.count } : null,
    localEstimate(effective, { durationS: duration, count: form.count }),
  )
  const blocked = audioBlockedReason({ ...form, durationS: duration }, send)
  const timbreOk = kind === 'song' && has(effective, 'timbre_ref')

  const chooseModel = (id: string) => {
    const m = models.find((x) => x.id === id)
    setForm((f) => ({ ...f, model: id, durationS: clampAudioDuration(f.durationS, kind, m) }))
  }
  const setInstrumental = (on: boolean) => {
    setForm((f) => ({ ...f, vocal: on ? 'none' : f.vocal === 'none' ? null : f.vocal }))
    announce(on ? 'Instrumental: the lyrics are set aside.' : 'Vocals back on; your lyrics will be sent.')
  }

  const submit = () => {
    if (blocked || pending || !send) return
    const body = audioPayload({ ...form, durationS: duration }, send)
    const summary = [
      model?.label,
      formatDuration(duration),
      kind === 'song' ? (instrumental(form) ? 'instrumental' : VOCALS.find((v) => v.value === form.vocal)?.label.toLowerCase()) : null,
      kind === 'song' ? SONG_LANGUAGES.find((l) => l.value === form.language)?.label : null,
      form.category,
      form.count > 1 ? `${form.count} ${noun.many}` : null,
    ]
      .filter(Boolean)
      .join(' · ')
    onRun({ form: { ...form, model: model?.id, durationS: duration }, body, summary })
  }

  const belowPrompt =
    kind === 'song' ? (
      <LyricsEditor
        lyrics={form.lyrics}
        onLyrics={(v) => set('lyrics', v)}
        instrumental={instrumental(form)}
        onInstrumental={setInstrumental}
        onLanguage={(l) => set('language', songLanguageOf(l))}
        language={form.language}
        aerab={form.aerab}
        onAerab={(on) => set('aerab', on)}
      />
    ) : kind === 'sfx' ? (
      <div role="group" aria-label="Quick presets" className="flex flex-wrap gap-1">
        {SFX_PRESETS.map((p) => (
          <Chip key={p} selected={form.prompt === p} onClick={() => set('prompt', p)}>
            {p}
          </Chip>
        ))}
      </div>
    ) : null

  return (
    <PromptDock
      into="audio"
      title={copy.title}
      icon={copy.icon}
      headerExtra={<p className="text-small text-studio-muted max-lg:hidden">{copy.hint}</p>}
      promptLabel={copy.promptLabel}
      prompt={form.prompt}
      onPrompt={(v) => set('prompt', v)}
      mentions={false}
      placeholder={copy.placeholder}
      belowPrompt={belowPrompt}
      chips={
        <>
          <ModelChip models={models} model={model} onChange={chooseModel} highlighted={modelPicked} />
          <AudioLengthChip kind={kind} model={effective} value={duration} onChange={(v) => set('durationS', v)} estimate={estimate} />
          {kind === 'song' && <VocalChip value={form.vocal} onChange={(v) => set('vocal', v)} />}
          {kind === 'song' && <LanguageChip value={form.language} onChange={(v) => set('language', v)} />}
          {kind === 'music' && <CategoryChip value={form.category} onChange={(v) => set('category', v)} />}
          <TakesChip kind={kind} value={form.count} onChange={(n) => set('count', n)} />
        </>
      }
      advanced={
        <div className="flex flex-wrap items-start gap-4">
          <div className="w-32">
            <Label htmlFor={`${uid}-seed`} className="mb-1.5">
              Seed
            </Label>
            <Input id={`${uid}-seed`} inputMode="numeric" value={form.seed} onChange={(e) => set('seed', e.target.value.replace(/\D/g, ''))} placeholder="Random" className="font-mono" />
          </div>
          <div className="w-28">
            <Label htmlFor={`${uid}-steps`} className="mb-1.5">
              Steps
            </Label>
            <Input id={`${uid}-steps`} inputMode="numeric" value={form.steps} onChange={(e) => set('steps', e.target.value.replace(/\D/g, '').slice(0, 3))} placeholder="Default" className="font-mono" />
          </div>
          {kind !== 'sfx' && (
            <div className="w-28">
              <Label htmlFor={`${uid}-bpm`} className="mb-1.5">
                BPM
              </Label>
              <Input
                id={`${uid}-bpm`}
                inputMode="numeric"
                value={form.bpm}
                onChange={(e) => set('bpm', e.target.value.replace(/\D/g, '').slice(0, 3))}
                placeholder={`${BPM_MIN}–${BPM_MAX}`}
                className="font-mono"
              />
            </div>
          )}
          {kind === 'song' && effective?.id === ACE_ID && (
            <>
              <div className="w-36">
                <Label htmlFor={`${uid}-key`} className="mb-1.5">
                  Key
                </Label>
                <select id={`${uid}-key`} value={form.keyscale} onChange={(e) => set('keyscale', e.target.value)} className={ADV_SELECT}>
                  <option value="">Model decides</option>
                  {KEYSCALES.map((k) => (
                    <option key={k} value={k}>
                      {k}
                    </option>
                  ))}
                </select>
              </div>
              <div className="w-28">
                <Label htmlFor={`${uid}-metre`} className="mb-1.5">
                  Time signature
                </Label>
                <select id={`${uid}-metre`} value={form.timeSignature} onChange={(e) => set('timeSignature', e.target.value as AudioForm['timeSignature'])} className={ADV_SELECT}>
                  <option value="">Auto</option>
                  <option value="2">2/4</option>
                  <option value="3">3/4 (waltz)</option>
                  <option value="4">4/4</option>
                  <option value="6">6/8</option>
                </select>
              </div>
              <div className="w-28">
                <Label htmlFor={`${uid}-guidance`} className="mb-1.5">
                  Guidance
                </Label>
                <Input
                  id={`${uid}-guidance`}
                  inputMode="decimal"
                  value={form.guidance}
                  onChange={(e) => set('guidance', e.target.value.replace(/[^\d.]/g, '').slice(0, 4))}
                  placeholder="2.0"
                  title="How closely the song follows your tags (0–10)"
                  className="font-mono"
                />
              </div>
              <SwitchRow
                id={`${uid}-think`}
                checked={form.think}
                onChange={(on) => set('think', on)}
                title="Plan the song first"
                hint="Better structure, a little slower. Turned off for you when a voice reference is used."
                className="max-w-xs py-1"
              />
            </>
          )}
          {kind === 'song' && <TimbrePicker value={form.timbreId} onChange={(id) => set('timbreId', id)} disabledReason={timbreOk ? null : `${effective?.label ?? 'This model'} can't follow a voice reference.`} />}
        </div>
      }
      error={error ? <ErrorState compact title={`Couldn't start the ${noun.one}`} error={error} /> : null}
      footer={
        <GenerateButton
          verb={copy.verb}
          what={`${form.count > 1 ? `${form.count} × ` : ''}${formatDuration(duration)} ${form.count > 1 ? noun.many : noun.one}`}
          estimate={estimate}
          icon={copy.icon}
          blocked={blocked}
          note={kind === 'song' ? (instrumental(form) ? 'Instrumental, no vocals.' : form.lyrics.trim() ? 'Sung from your lyrics.' : 'No lyrics yet, so it comes out instrumental.') : 'Nothing plays until you press play.'}
          pending={pending}
        />
      }
      onSubmit={submit}
    />
  )
}

/** Advanced: an audio item from the library whose voice and timbre the song should follow (ACE-Step). */
function TimbrePicker({ value, onChange, disabledReason }: { value: string | null; onChange: (id: string | null) => void; disabledReason: string | null }) {
  const uid = useId()
  const list = useMediaList({ kind: 'audio' })
  const items = flatItems(list.data).filter((m) => m.media_url)
  return (
    <div className="w-64">
      <Label htmlFor={`${uid}-timbre`} className="mb-1.5">
        Voice reference
      </Label>
      <select
        id={`${uid}-timbre`}
        value={value ?? ''}
        disabled={!!disabledReason}
        onChange={(e) => onChange(e.target.value || null)}
        aria-describedby={`${uid}-timbre-hint`}
        className={SELECT}
      >
        <option value="">None</option>
        {items.map((m) => (
          <option key={m.id} value={m.id}>
            {mediaAlt(m)}
          </option>
        ))}
      </select>
      <p id={`${uid}-timbre-hint`} className="mt-1 text-small text-studio-muted">
        {disabledReason ?? (items.length ? 'The song borrows the voice and timbre of this clip.' : 'Upload a clip to the Audio library to use it here.')}
      </p>
    </div>
  )
}
