import { useId } from 'react'
import { Hash, Languages, MicVocal, Shapes, Timer } from 'lucide-react'
import { ChipGroup } from '@/components/studio/chip'
import { DurationFields } from '@/components/studio/duration-picker'
import { DockChip } from '@/components/generate/DockChip'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { audioDurationPresets, KIND_NOUN, LEAD_VOICES, type LeadVoice, MAX_TAKES, maxAudioDuration, MUSIC_CATEGORIES, SONG_LANGUAGES, SUNG_AS, vocalLabel, VOCALS } from '@/lib/audio'
import { useVoiceChoices } from '@/hooks/useGallery'
import { useSongVoices } from '@/hooks/useStems'
import { formatDuration } from '@/lib/duration'
import { rangeText } from '@/lib/estimate'
import type { AudioCategory, AudioKind, AudioVocal, EstimateResult, ModelInfo } from '@/lib/types'

export function AudioLengthChip({
  kind,
  model,
  value,
  onChange,
  estimate,
}: {
  kind: AudioKind
  model: ModelInfo | undefined
  value: number
  onChange: (s: number) => void
  estimate: EstimateResult | null
}) {
  const max = maxAudioDuration(kind, model)
  return (
    <DockChip name="Length" value={formatDuration(value)} icon={<Timer aria-hidden />}>
      <DurationFields
        key={`${model?.id}-${max}`}
        label="Length"
        value={value}
        onChange={onChange}
        presets={audioDurationPresets(kind, model)}
        max={max}
        limitNoun={kind === 'sfx' ? ' for a sound effect' : ` with ${model?.label ?? 'this model'}`}
        estimateLine={
          estimate ? (
            <p aria-live="polite" className="text-small text-studio-muted">
              <span className="font-mono text-studio-text">{formatDuration(value)}</span> {KIND_NOUN[kind].one} · about {rangeText(estimate.low_s, estimate.high_s).replace(/^~/, '')} to make
              {estimate.basis === 'measured' ? ' (measured on this server)' : ' (rough)'}
            </p>
          ) : null
        }
      />
    </DockChip>
  )
}

export function VocalChip({
  value,
  onChange,
  voiceId,
  onVoice,
  lead = 'girl',
  onLead,
  groupSize = 3,
  onGroupSize,
}: {
  value: AudioVocal | null
  onChange: (v: AudioVocal | null) => void
  voiceId: string | null
  onVoice: (id: string | null) => void
  lead?: LeadVoice
  onLead?: (v: LeadVoice) => void
  groupSize?: 3 | 5
  onGroupSize?: (n: 3 | 5) => void
}) {
  const sung = useSongVoices().data?.available === true
  const voices = useVoiceChoices()
  const selectId = useId()
  const chosen = value === 'voice' ? voices.find((v) => v.id === voiceId)?.name : null
  const label = chosen ?? vocalLabel(value) ?? 'Any'
  return (
    <DockChip name="Vocal" showName value={label} icon={<MicVocal aria-hidden />} active={!!value}>
      <ChipGroup label="Vocal" options={VOCALS} value={sung && SUNG_AS.some((s) => s.value === value) ? null : value} onChange={onChange} />
      {sung && (
        <ChipGroup label="Sung by" options={SUNG_AS} value={SUNG_AS.some((s) => s.value === value) ? value : null} onChange={onChange} />
      )}
      {sung && value === 'lead_group' && (
        <>
          <ChipGroup label="Lead" options={LEAD_VOICES} value={lead} onChange={(v) => v && onLead?.(v)} allowEmpty={false} />
          <ChipGroup
            label="Group"
            options={[
              { value: '3', label: '3 children' },
              { value: '5', label: '5 children' },
            ]}
            value={String(groupSize) as '3' | '5'}
            onChange={(v) => v && onGroupSize?.(Number(v) as 3 | 5)}
            allowEmpty={false}
          />
          <p className="text-small text-studio-muted">
            Mark who sings in the lyrics: <span className="font-mono">[verse: lead]</span>, <span className="font-mono">[chorus: group]</span>,{' '}
            <span className="font-mono">[chorus: all]</span>. Words in (brackets) are the group answering. Unmarked verses are the lead; choruses are everyone.
          </p>
        </>
      )}
      {sung && (value === 'voice' || (value === 'lead_group' && lead === 'voice')) && (
        <div className="flex flex-col gap-1">
          <label htmlFor={selectId} className="section-label">
            Voice
          </label>
          <select
            id={selectId}
            className="h-9 rounded-[6px] border border-studio-border-strong bg-studio-raised px-2 text-body"
            value={voiceId ?? ''}
            onChange={(e) => onVoice(e.target.value || null)}
          >
            <option value="">Choose a voice…</option>
            {voices.map((v) => (
              <option key={v.id} value={v.id}>
                {v.name}
              </option>
            ))}
          </select>
        </div>
      )}
      <p className="text-small text-studio-muted">
        None makes it instrumental, and the lyrics are set aside. Any lets the model choose.
        {sung ? ' A child, a choir or your own voice takes a few minutes more: the song is sung first, then changed into that voice.' : ''}
      </p>
    </DockChip>
  )
}

export function LanguageChip({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const label = SONG_LANGUAGES.find((l) => l.value === value)?.label ?? value
  return (
    <DockChip name="Language" value={label} icon={<Languages aria-hidden />}>
      <ChipGroup label="Sung in" options={SONG_LANGUAGES} value={value} onChange={(v) => v && onChange(v)} allowEmpty={false} />
      <p className="text-small text-studio-muted">Write Urdu lyrics in Urdu script or in Roman Urdu; both are sung in Urdu.</p>
    </DockChip>
  )
}

export function CategoryChip({ value, onChange }: { value: AudioCategory | null; onChange: (v: AudioCategory | null) => void }) {
  const label = MUSIC_CATEGORIES.find((c) => c.value === value)?.label ?? 'Any'
  return (
    <DockChip name="Category" showName value={label} icon={<Shapes aria-hidden />} active={!!value}>
      <ChipGroup label="Category" options={MUSIC_CATEGORIES} value={value} onChange={onChange} />
      <p className="text-small text-studio-muted">Instrument keeps to one instrument; Loop aims for a clean repeat.</p>
    </DockChip>
  )
}

export function TakesChip({ kind, value, onChange }: { kind: AudioKind; value: number; onChange: (n: number) => void }) {
  const id = useId()
  const noun = KIND_NOUN[kind]
  return (
    <DockChip name="Count" value={`×${value}`} icon={<Hash aria-hidden />}>
      <div>
        <div id={id} className="section-label mb-2">
          Takes
        </div>
        <ToggleGroup type="single" aria-labelledby={id} value={String(value)} onValueChange={(v) => v && onChange(Number(v))}>
          {Array.from({ length: MAX_TAKES }, (_, i) => (
            <ToggleGroupItem key={i} value={String(i + 1)} aria-label={`${i + 1} ${i ? noun.many : noun.one}`} className="w-10">
              {i + 1}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      </div>
      <p className="text-small text-studio-muted">Each take uses the next seed, so you can pick the best one.</p>
    </DockChip>
  )
}
