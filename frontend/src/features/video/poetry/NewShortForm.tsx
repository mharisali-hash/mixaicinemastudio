import { useId, useState } from 'react'
import { useNavigate } from 'react-router'
import { Feather } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { SwitchRow } from '@/components/studio/switch-row'
import { AerabButton } from '@/components/text/AerabButton'
import { useStudioCharacters } from '@/hooks/useCharacterStudio'
import { useCreatePoetry } from '@/hooks/usePoetry'
import { IMAGE_MODELS, POETRY_LANGUAGES, type PoetryAspect, type PoetryCreate } from '@/lib/poetry'
import { announce } from '@/stores/ui'

const SELECT = 'h-8 w-full rounded-[6px] border border-studio-border-strong bg-studio-raised px-2 text-body focus-visible:outline-2 focus-visible:outline-studio-accent'

/** Everything a Short needs up front; only a topic or a poem is required. */
export function NewShortForm() {
  const uid = useId()
  const id = (s: string) => `${uid}-${s}`
  const nav = useNavigate()
  const create = useCreatePoetry()
  const chars = useStudioCharacters()
  const [own, setOwn] = useState(false)
  const [f, setF] = useState({
    title: '',
    topic: '',
    poem: '',
    language: 'ur',
    mood: '',
    style: '',
    vocal: 'female' as 'female' | 'male',
    length_s: 60,
    character_id: '',
    character: '',
    setting: '',
    aspect: '9:16' as PoetryAspect,
    quality: 'draft' as 'draft' | 'final',
    image_model: 'krea2_turbo',
    title_card: false,
    review: true,
  })
  const set = <K extends keyof typeof f>(k: K, v: (typeof f)[K]) => setF((o) => ({ ...o, [k]: v }))
  const missing = own ? !f.poem.trim() : !f.topic.trim()

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    if (missing) return
    const body: PoetryCreate = { ...f, character_id: f.character_id || null, topic: own ? '' : f.topic, poem: own ? f.poem : '' }
    create.mutate(body, {
      onSuccess: (ps) => {
        announce('Poetry Short started.')
        nav(`/video/poetry/${ps.id}`)
      },
    })
  }

  return (
    <form onSubmit={submit} aria-labelledby={id('h')} className="flex flex-col gap-3 rounded-[8px] border border-studio-border bg-studio-panel p-4 shadow-card">
      <h2 id={id('h')} className="font-display text-title font-semibold">
        New Poetry Short
      </h2>
      <fieldset className="flex gap-2">
        <legend className="sr-only">Where the poem comes from</legend>
        <Button type="button" size="sm" variant={own ? 'ghost' : 'primary'} aria-pressed={!own} onClick={() => setOwn(false)}>
          Write it for me
        </Button>
        <Button type="button" size="sm" variant={own ? 'primary' : 'ghost'} aria-pressed={own} onClick={() => setOwn(true)}>
          My own poem
        </Button>
      </fieldset>
      {own ? (
        <div className="flex flex-col gap-1">
          <Label htmlFor={id('poem')}>Your poem</Label>
          <Textarea
            id={id('poem')}
            dir="auto"
            lang={f.language}
            rows={8}
            maxLength={4000}
            value={f.poem}
            onChange={(e) => set('poem', e.target.value)}
            aria-describedby={id('poem-hint')}
            className="text-[16px] leading-relaxed"
          />
          <span id={id('poem-hint')} className="text-small text-studio-muted">
            One line per sung line. [verse] and [chorus] markers are optional.
          </span>
          <AerabButton text={f.poem} onText={(t) => set('poem', t)} language={f.language} max={4000} target="the poem" />
        </div>
      ) : (
        <div className="flex flex-col gap-1">
          <Label htmlFor={id('topic')}>What is the poem about?</Label>
          <Input id={id('topic')} dir="auto" value={f.topic} onChange={(e) => set('topic', e.target.value)} placeholder="Coming apart slowly, like dust in the wind" />
        </div>
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1">
          <Label htmlFor={id('title')}>Title (optional)</Label>
          <Input id={id('title')} dir="auto" maxLength={200} value={f.title} onChange={(e) => set('title', e.target.value)} />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor={id('lang')}>Language</Label>
          <select id={id('lang')} className={SELECT} value={f.language} onChange={(e) => set('language', e.target.value)}>
            {POETRY_LANGUAGES.map((l) => (
              <option key={l.value} value={l.value}>
                {l.label}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor={id('mood')}>Mood</Label>
          <Input id={id('mood')} value={f.mood} onChange={(e) => set('mood', e.target.value)} placeholder="melancholy, longing" />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor={id('style')}>Music style</Label>
          <Input id={id('style')} value={f.style} onChange={(e) => set('style', e.target.value)} placeholder="slow ghazal, harmonium, tabla" />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor={id('vocal')}>Voice</Label>
          <select id={id('vocal')} className={SELECT} value={f.vocal} onChange={(e) => set('vocal', e.target.value as 'female' | 'male')}>
            <option value="female">Female</option>
            <option value="male">Male</option>
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor={id('len')}>Length: {f.length_s} s</Label>
          <input id={id('len')} type="range" min={30} max={90} step={5} value={f.length_s} onChange={(e) => set('length_s', Number(e.target.value))} className="h-8 accent-studio-accent" />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor={id('char')}>Character</Label>
          <select id={id('char')} className={SELECT} value={f.character_id} onChange={(e) => set('character_id', e.target.value)}>
            <option value="">Describe one instead</option>
            {(chars.data ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor={id('who')}>{f.character_id ? 'Anything to add about them' : 'Who we see'}</Label>
          <Input id={id('who')} value={f.character} onChange={(e) => set('character', e.target.value)} placeholder="a woman in a hooded robe, seen from behind" />
        </div>
        <div className="flex flex-col gap-1 sm:col-span-2">
          <Label htmlFor={id('set')}>Setting</Label>
          <Input id={id('set')} value={f.setting} onChange={(e) => set('setting', e.target.value)} placeholder="an old Mughal city, golden hour into lantern-lit night" />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor={id('aspect')}>Shape</Label>
          <select id={id('aspect')} className={SELECT} value={f.aspect} onChange={(e) => set('aspect', e.target.value as PoetryAspect)}>
            <option value="9:16">9:16 (Shorts, Reels)</option>
            <option value="16:9">16:9 (YouTube)</option>
            <option value="1:1">1:1 (square)</option>
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor={id('model')}>Picture model</Label>
          <select id={id('model')} className={SELECT} value={f.image_model} onChange={(e) => set('image_model', e.target.value)} aria-describedby={id('model-hint')}>
            {IMAGE_MODELS.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
          {f.character_id && (
            <span id={id('model-hint')} className="text-small text-studio-muted">
              With a character, the pictures are drawn from their sheet instead.
            </span>
          )}
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor={id('q')}>Quality</Label>
          <select id={id('q')} className={SELECT} value={f.quality} onChange={(e) => set('quality', e.target.value as 'draft' | 'final')}>
            <option value="draft">Draft (faster)</option>
            <option value="final">Final</option>
          </select>
        </div>
      </div>
      <SwitchRow id={id('tc')} checked={f.title_card} onChange={(v) => set('title_card', v)} title="Title card" hint="A few seconds of the title over the first scene before the song." />
      <SwitchRow
        id={id('rv')}
        checked={f.review}
        onChange={(v) => set('review', v)}
        title="Let me check each step"
        hint={f.review ? 'It stops after the poem, the song, the timing and the scenes for your OK.' : 'One click: it runs straight through to the finished video.'}
      />
      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" variant="primary" loading={create.isPending} aria-disabled={missing || undefined} aria-describedby={id('why')}>
          <Feather aria-hidden />
          Make the Short
        </Button>
        <span id={id('why')} aria-live="polite" className="text-small text-studio-muted">
          {missing ? (own ? 'Paste your poem first.' : 'Say what the poem is about first.') : ''}
        </span>
      </div>
      {create.isError && (
        <p role="alert" className="text-small text-studio-danger">
          {create.error.message}
        </p>
      )}
    </form>
  )
}
