import { useId, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { Presentation } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { CaptionStylePicker, type CaptionStyle } from '@/components/generate/CaptionStylePicker'
import { EmptyState, ErrorState } from '@/components/studio/states'
import { SwitchRow } from '@/components/studio/switch-row'
import { useStudioCharacters } from '@/hooks/useCharacterStudio'
import { useCharacterLooks } from '@/hooks/useCharacterLooks'
import { useCreatePresenter, usePresenters } from '@/hooks/usePresenter'
import { BACKGROUNDS, scenesIn, type PresenterAspect } from '@/lib/presenter'
import { announce } from '@/stores/ui'

const SELECT = 'h-8 w-full rounded-[6px] border border-studio-border-strong bg-studio-raised px-2 text-body focus-visible:outline-2 focus-visible:outline-studio-accent'

export function PresenterListPage() {
  const list = usePresenters()
  return (
    <main data-f6-region tabIndex={-1} aria-labelledby="presenter-h" className="relative h-full overflow-y-auto focus-visible:outline-none">
      <div className="mx-auto grid w-full max-w-6xl gap-6 px-4 py-6 md:px-8 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="flex flex-col gap-2">
          <h1 id="presenter-h" className="font-display text-[22px] font-semibold">
            Presenter Video
          </h1>
          <p className="text-body text-studio-muted">One of your characters presents your script to camera, in their own voice, with captions and their name on screen.</p>
          <NewPresenterForm />
        </div>
        <section aria-labelledby="presenter-mine" className="flex flex-col gap-2">
          <h2 id="presenter-mine" className="font-display text-title font-semibold">
            Your presenter videos
          </h2>
          {list.isPending && <Skeleton className="h-24" />}
          {list.isError && <ErrorState error={list.error} onRetry={() => list.refetch()} />}
          {list.data?.length === 0 && (
            <EmptyState icon={<Presentation />} title="None yet">
              Your first one appears here as soon as it starts.
            </EmptyState>
          )}
          <ul className="flex flex-col gap-2">
            {list.data?.map((p) => (
              <li key={p.id}>
                <Link
                  to={`/video/presenter/${p.id}`}
                  className="flex flex-col rounded-[6px] border border-studio-border bg-studio-raised p-2 hover:border-studio-border-hover focus-visible:outline-2 focus-visible:outline-studio-accent"
                >
                  <span dir="auto" className="truncate text-body font-medium">
                    {p.title || 'Untitled'}
                  </span>
                  <span className="text-small text-studio-muted">
                    {p.scenes} scene{p.scenes === 1 ? '' : 's'} · {p.aspect}
                    {p.outputs.final ? ' · final made' : p.outputs.draft ? ' · draft made' : ''}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </main>
  )
}

function NewPresenterForm() {
  const uid = useId()
  const id = (k: string) => `${uid}-${k}`
  const nav = useNavigate()
  const create = useCreatePresenter()
  const chars = useStudioCharacters()
  const [f, setF] = useState({
    character_id: '',
    look_id: '',
    title: '',
    language: 'en',
    aspect: '16:9' as PresenterAspect,
    background: 'studio',
    caption_style: 'pill' as CaptionStyle,
    lower_role: '',
    script: '',
    review: true,
  })
  const set = <K extends keyof typeof f>(k: K, v: (typeof f)[K]) => setF((x) => ({ ...x, [k]: v }))
  const looks = useCharacterLooks(f.character_id)
  const ready = (looks.data?.looks ?? []).filter((l) => l.picture && ['ready', 'approved'].includes(l.picture.status))
  const cast = (chars.data ?? []).filter((c) => c.portrait)
  const n = scenesIn(f.script)
  const missing = !f.character_id ? 'Pick who presents it.' : !f.script.trim() ? 'Write the script.' : ''

  return (
    <form
      className="grid gap-3 rounded-[6px] border border-studio-border-strong bg-studio-panel p-3 sm:grid-cols-2"
      onSubmit={(e) => {
        e.preventDefault()
        if (missing) return
        create.mutate(
          {
            character_id: f.character_id,
            look_id: f.look_id || null,
            title: f.title,
            language: f.language,
            script: f.script,
            review: f.review,
            settings: { aspect: f.aspect, background: f.background, caption_style: f.caption_style, lower_role: f.lower_role },
          },
          {
            onSuccess: (pv) => {
              announce('Presenter video started.')
              nav(`/video/presenter/${pv.id}`)
            },
          },
        )
      }}
    >
      <div className="flex flex-col gap-1">
        <Label htmlFor={id('who')}>Presenter</Label>
        <select id={id('who')} className={SELECT} value={f.character_id} onChange={(e) => setF((x) => ({ ...x, character_id: e.target.value, look_id: '' }))}>
          <option value="">Choose a character…</option>
          {cast.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
              {c.voice_id ? '' : ' (no voice yet)'}
            </option>
          ))}
        </select>
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor={id('look')}>Look</Label>
        <select id={id('look')} className={SELECT} value={f.look_id} disabled={!f.character_id} onChange={(e) => set('look_id', e.target.value)}>
          <option value="">Portrait (as they are)</option>
          {ready.map((l) => (
            <option key={l.id} value={l.id}>
              {l.name}
              {l.is_default ? ' (default)' : ''}
            </option>
          ))}
        </select>
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor={id('title')}>Title</Label>
        <Input id={id('title')} dir="auto" maxLength={200} value={f.title} placeholder="Shown at the start" onChange={(e) => set('title', e.target.value)} />
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor={id('role')}>Their role, under their name</Label>
        <Input id={id('role')} dir="auto" maxLength={120} value={f.lower_role} placeholder="Head baker" onChange={(e) => set('lower_role', e.target.value)} />
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor={id('lang')}>Language</Label>
        <select id={id('lang')} className={SELECT} value={f.language} onChange={(e) => set('language', e.target.value)}>
          <option value="en">English</option>
          <option value="ur">Urdu</option>
          <option value="hi">Hindi</option>
          <option value="ar">Arabic</option>
        </select>
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor={id('aspect')}>Shape</Label>
        <select id={id('aspect')} className={SELECT} value={f.aspect} onChange={(e) => set('aspect', e.target.value as PresenterAspect)}>
          <option value="16:9">Landscape 16:9</option>
          <option value="9:16">Vertical 9:16</option>
          <option value="1:1">Square 1:1</option>
        </select>
      </div>
      <div className="flex flex-col gap-1 sm:col-span-2">
        <Label htmlFor={id('bg')}>Background</Label>
        <select id={id('bg')} className={SELECT} value={f.background} onChange={(e) => set('background', e.target.value)}>
          {BACKGROUNDS.map((b) => (
            <option key={b.key} value={b.key}>
              {b.label}
            </option>
          ))}
        </select>
      </div>
      <div className="flex flex-col gap-1 sm:col-span-2">
        <Label htmlFor={id('script')}>Script</Label>
        <Textarea id={id('script')} dir="auto" lang={f.language} rows={8} value={f.script} aria-describedby={id('script-hint')} onChange={(e) => set('script', e.target.value)} />
        <span id={id('script-hint')} className="text-small text-studio-muted">
          A blank line starts a new scene; keep each scene to a sentence or two (about 10 seconds spoken).
          {n ? ` ${n} scene${n === 1 ? '' : 's'} so far.` : ''}
        </span>
      </div>
      <div className="sm:col-span-2">
        <CaptionStylePicker value={f.caption_style} onChange={(v) => set('caption_style', v)} />
      </div>
      <div className="sm:col-span-2">
        <SwitchRow id={id('review')} checked={f.review} onChange={(v) => set('review', v)} title="Let me check the frames first" hint="Stops after drawing each scene, before filming. Off goes straight to a draft video." />
      </div>
      <div className="flex flex-wrap items-center gap-2 sm:col-span-2">
        <Button type="submit" variant="primary" loading={create.isPending} aria-disabled={!!missing || undefined} aria-describedby={id('why')}>
          <Presentation aria-hidden />
          Make the video
        </Button>
        <span id={id('why')} aria-live="polite" className="text-small text-studio-muted">
          {missing}
        </span>
      </div>
      {create.isError && (
        <div className="sm:col-span-2">
          <ErrorState compact title="Couldn't start it" error={create.error} />
        </div>
      )}
    </form>
  )
}
