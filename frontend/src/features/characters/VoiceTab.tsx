import { useId, useState } from 'react'
import { Link } from 'react-router'
import { AudioLines, Mic, Save, Sparkles } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { ErrorState } from '@/components/studio/states'
import { AerabButton } from '@/components/text/AerabButton'
import { useVoiceChoices } from '@/hooks/useGallery'
import { useDesignCharacterVoice, useSaveCharacter } from '@/hooks/useCharacterStudio'
import { useLiveMediaItem } from '@/hooks/useMedia'
import { useSpeak } from '@/hooks/useVoices'
import type { Character, Voice } from '@/lib/types'
import { announce } from '@/stores/ui'
import { SELECT } from './sheet'

const isGallery = (v: Voice) => v.id.startsWith('gallery:')

/** Which voice the character speaks with (contract-v26): pick one, hear it, or design one from the profile. */
export function VoiceTab({ character }: { character: Character }) {
  const uid = useId()
  const voices = useVoiceChoices()
  const save = useSaveCharacter()
  const [voiceId, setVoiceId] = useState(character.voice_id ?? '')
  // a voice saved elsewhere (designed below, another tab) replaces the pick
  const [seen, setSeen] = useState(character.voice_id ?? '')
  if ((character.voice_id ?? '') !== seen) {
    setSeen(character.voice_id ?? '')
    setVoiceId(character.voice_id ?? '')
  }
  const current = voices.find((v) => v.id === voiceId)
  const mine = voices.filter((v) => !isGallery(v))
  const gallery = voices.filter(isGallery)

  return (
    <div className="flex max-w-xl flex-col gap-6">
      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate({ id: character.id, voice_id: voiceId }, { onSuccess: () => announce(voiceId ? 'Voice saved.' : 'Voice cleared.') })
        }}
      >
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${uid}-voice`}>Voice</Label>
          <select id={`${uid}-voice`} className={SELECT} value={voiceId} onChange={(e) => setVoiceId(e.target.value)}>
            <option value="">No voice yet</option>
            {mine.length > 0 && (
              <optgroup label="My voices">
                {mine.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}
                  </option>
                ))}
              </optgroup>
            )}
            {gallery.length > 0 && (
              <optgroup label="Gallery">
                {gallery.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}
                  </option>
                ))}
              </optgroup>
            )}
          </select>
          {current?.notes && <p className="text-small text-studio-muted">{current.notes}</p>}
          {!voiceId && (
            <p className="text-small text-studio-muted">Without a voice, this character's lines use the video model's own audio, with no lip sync.</p>
          )}
        </div>
        {save.isError && <ErrorState compact title="Couldn't save the voice" error={save.error} />}
        <Button type="submit" variant="primary" className="self-start" loading={save.isPending} disabled={voiceId === (character.voice_id ?? '')}>
          <Save aria-hidden />
          Save voice
        </Button>
      </form>

      {current && <PreviewLine key={current.id} voice={current} name={character.name} />}

      <DesignVoice character={character} />

      <section aria-labelledby={`${uid}-clone`} className="flex flex-col gap-1.5">
        <h3 id={`${uid}-clone`} className="section-label">
          Clone a real voice
        </h3>
        <p className="text-small text-studio-muted">
          Record or upload 5 to 15 seconds of the person speaking in{' '}
          <Link to="/audio/voices" className="underline underline-offset-2">
            Voices
          </Link>
          , with their consent, then pick it above.
        </p>
      </section>
    </div>
  )
}

function PreviewLine({ voice, name }: { voice: Voice; name: string }) {
  const uid = useId()
  const [text, setText] = useState(`Hello, my name is ${name}.`)
  const speak = useSpeak()
  const [itemId, setItemId] = useState<string | null>(null)
  const item = useLiveMediaItem(itemId).data
  const waiting = speak.isPending || (!!itemId && (!item || item.status === 'queued' || item.status === 'generating'))
  const failed = item?.status === 'failed'

  const run = () => {
    if (!text.trim()) return
    speak.mutate(
      { text: text.trim(), voice_id: voice.id, engine: 'auto', language: voice.language, count: 1, title: `${name} · voice preview`, part_of: 'character' },
      {
        onSuccess: (res) => {
          setItemId(res.items?.[0]?.id ?? null)
          announce(`Speaking the line as ${voice.name}. It plays here when it's ready.`)
        },
      },
    )
  }

  return (
    <section aria-labelledby={`${uid}-h`} className="flex flex-col gap-2">
      <h3 id={`${uid}-h`} className="section-label">
        Hear a line
      </h3>
      <Label htmlFor={`${uid}-text`} className="sr-only">
        Line to preview in {voice.name}
      </Label>
      <Textarea id={`${uid}-text`} dir="auto" rows={2} maxLength={300} value={text} onChange={(e) => setText(e.target.value)} />
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" variant="secondary" onClick={run} loading={waiting} disabled={!text.trim()}>
          <AudioLines aria-hidden />
          Preview
        </Button>
        <AerabButton text={text} onText={setText} language={voice.language} target="the preview line" showNote={false} />
      </div>
      {speak.isError && <ErrorState compact title="Couldn't speak the line" error={speak.error} />}
      <p role="status" className="text-small text-studio-muted">
        {waiting ? 'Speaking…' : failed ? "The preview didn't render. Try again." : ''}
      </p>
      {item?.status === 'ready' && item.media_url && (
        <audio controls src={item.media_url} className="w-full" aria-label={`Preview of ${voice.name}`} />
      )}
    </section>
  )
}

function DesignVoice({ character }: { character: Character }) {
  const uid = useId()
  const [extra, setExtra] = useState('')
  const design = useDesignCharacterVoice(character.id)
  const p = character.profile ?? {}
  const known = [p.gender, p.age && `age ${p.age}`, p.accent ? `${p.accent} accent` : p.nationality].filter(Boolean)
  const res = design.data

  return (
    <section aria-labelledby={`${uid}-h`} className="flex flex-col gap-2">
      <h3 id={`${uid}-h`} className="section-label">
        Design a voice for {character.name}
      </h3>
      <p id={`${uid}-from`} className="text-small text-studio-muted">
        {known.length
          ? `Made from the profile: ${known.join(', ')}. It's saved to Voices and set as ${character.name}'s voice.`
          : 'The profile has no gender, age or accent yet. Fill them on the Profile tab, or describe the voice below.'}
      </p>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${uid}-extra`}>Anything to add (optional)</Label>
        <Input
          id={`${uid}-extra`}
          value={extra}
          placeholder="deep, slow"
          aria-describedby={`${uid}-from`}
          onChange={(e) => setExtra(e.target.value)}
        />
      </div>
      <Button
        type="button"
        variant="secondary"
        className="self-start"
        loading={design.isPending}
        onClick={() =>
          design.mutate(
            { extra: extra.trim() },
            {
              onSuccess: (r) => {
                announce(`Designed “${r.voice.name}” (${r.understood.join(', ')}) and gave it to ${character.name}.`)
              },
            },
          )
        }
      >
        <Sparkles aria-hidden />
        Design voice
      </Button>
      {design.isError && <ErrorState compact title="Couldn't design a voice" error={design.error} />}
      {res && (
        <p className="text-small text-studio-muted">
          <Mic aria-hidden className="mr-1 inline size-3.5" />
          {res.voice.name}: {res.understood.join(', ')}.
          {res.ignored.length > 0 && <> The voice model can't use “{res.ignored.join('”, “')}”, so those words were left out.</>}
        </p>
      )}
    </section>
  )
}
