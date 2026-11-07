import { useId, useState } from 'react'
import { Check, RefreshCw, Save } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { Chip } from '@/components/studio/chip'
import { usePatchConcept, useRegenerateConcept } from '@/hooks/useAds'
import { styleLabel, type AdCampaign, type AdConcept, type AdPhotoSet, type ConceptPatch } from '@/lib/ads'
import { cn } from '@/lib/utils'
import { announce } from '@/stores/ui'
import { LayoutPicker } from './LayoutPicker'

const SELECT = 'h-8 w-full rounded-[6px] border border-studio-border-strong bg-studio-raised px-2 text-body focus-visible:outline-2 focus-visible:outline-studio-accent'
const FIELDS = ['kicker', 'headline', 'key_word', 'body', 'cta', 'layout', 'quote', 'photo_prompt'] as const
type Draft = {
  kicker: string
  headline: string
  key_word: string
  body: string
  cta: string
  layout: AdConcept['layout']
  quote: number | null
  photo_prompt: string
}

const draftOf = (c: AdConcept): Draft => ({
  kicker: c.kicker,
  headline: c.headline,
  key_word: c.key_word ?? '',
  body: c.body,
  cta: c.cta,
  layout: c.layout,
  quote: c.quote ?? null,
  photo_prompt: c.photo_prompt,
})

const GROUPS = [
  ['portrait', 'Portrait'],
  ['square', 'Square'],
  ['wide', 'Wide'],
] as const

const PICKED_BY: Record<string, string> = { vision: 'picked by the photo check', heuristic: 'picked for the calmest copy space' }

// a compact strip per orientation: what was made and which one the ads use
function Candidates({ n, photos }: { n: number; photos: AdConcept['photos'] }) {
  const sets = GROUPS.map(([k, label]) => [label, photos[k]] as [string, AdPhotoSet | undefined]).filter(([, p]) => (p?.candidates?.length ?? 0) > 0)
  if (!sets.length) return null
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-small font-medium">Photos made</span>
      {sets.map(([label, p]) => (
        <div key={label} className="flex flex-wrap items-center gap-1.5">
          <span className="w-16 text-small text-studio-muted">{label}</span>
          <ul className="flex flex-wrap gap-1.5" aria-label={`Concept ${n}, ${label.toLowerCase()} photos`}>
            {p!.candidates!.map((c, i) => {
              const score = c.score != null ? `score ${Math.round(c.score * 10) / 10}` : null
              return (
                <li key={c.id} className="relative">
                  <span className={cn('block size-12 overflow-hidden rounded-[4px] border bg-studio-raised', c.picked ? 'border-studio-accent ring-2 ring-studio-accent' : 'border-studio-border-strong')}>
                    {c.thumb_url || c.media_url ? (
                      <img
                        src={c.thumb_url ?? c.media_url ?? undefined}
                        alt={`Photo ${i + 1}${c.picked ? ', used in the ads' : ''}${score ? `, ${score}` : ''}${c.issues.length ? `: ${c.issues.join(', ')}` : ''}`}
                        className="size-full object-cover"
                      />
                    ) : (
                      <span className="sr-only">Photo {i + 1} not ready</span>
                    )}
                  </span>
                  {c.picked && (
                    <span aria-hidden className="absolute -right-1 -top-1 flex size-4 items-center justify-center rounded-full bg-studio-accent text-studio-accent-fg">
                      <Check className="size-3" />
                    </span>
                  )}
                  {score && (
                    <span aria-hidden className="block text-center text-[11px] text-studio-muted">
                      {Math.round(c.score! * 10) / 10}
                    </span>
                  )}
                </li>
              )
            })}
          </ul>
          {p!.pick && PICKED_BY[p!.pick.by] && <span className="text-small text-studio-muted">{PICKED_BY[p!.pick.by]}</span>}
        </div>
      ))}
    </div>
  )
}

function ConceptCard({ camp, concept, busy }: { camp: AdCampaign; concept: AdConcept; busy: boolean }) {
  const uid = useId()
  const id = (s: string) => `${uid}-${s}`
  const patch = usePatchConcept(camp.id)
  const regen = useRegenerateConcept(camp.id)
  const [d, setD] = useState<Draft>(() => draftOf(concept))
  const server = JSON.stringify(draftOf(concept))
  const [seen, setSeen] = useState(server)
  // new copy from the server (a rewrite, a save) replaces the draft; another card's save leaves it alone
  if (seen !== server) {
    setSeen(server)
    setD(draftOf(concept))
  }
  const was = draftOf(concept)
  const dirty = FIELDS.some((k) => d[k] !== was[k])
  const quotes = camp.brief.quotes ?? []
  const options = (concept.headline_options ?? []).filter((o) => o.headline.trim())
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setD((o) => ({ ...o, [k]: v }))
  const lang = camp.language
  const dropped = [...(concept.guard.offers_dropped ?? []), ...(concept.guard.numbers_removed ?? []), ...(concept.guard.channels_removed ?? [])]

  const save = () => {
    const body: ConceptPatch = {}
    for (const k of FIELDS) if (d[k] !== was[k]) (body as Record<string, unknown>)[k] = d[k]
    patch.mutate({ conceptId: concept.id, ...body }, { onSuccess: () => announce(`Concept ${concept.n} saved.`) })
  }

  return (
    <li aria-labelledby={id('h')} className="flex flex-col gap-2 rounded-[8px] border border-studio-border-strong bg-studio-panel p-3 shadow-card">
      <div className="flex items-center gap-2">
        <h3 id={id('h')} className="font-display text-panel font-semibold">
          Concept {concept.n}
        </h3>
        <label className="ml-auto flex items-center gap-2 text-small">
          <Switch
            checked={concept.approved}
            disabled={busy || patch.isPending}
            onCheckedChange={(v) => patch.mutate({ conceptId: concept.id, approved: v })}
            aria-label={`Make concept ${concept.n}`}
          />
          Make this one
        </label>
      </div>
      {concept.insight && (
        <p dir="auto" lang={lang} className="text-small italic text-studio-muted">
          {concept.insight}
        </p>
      )}
      <div className="flex flex-col gap-1">
        <label htmlFor={id('kicker')} className="text-small font-medium">
          Kicker
        </label>
        <Input id={id('kicker')} dir="auto" lang={lang} maxLength={300} value={d.kicker} onChange={(e) => set('kicker', e.target.value)} />
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor={id('headline')} className="text-small font-medium">
          Headline
        </label>
        <Textarea id={id('headline')} dir="auto" lang={lang} rows={2} maxLength={300} value={d.headline} onChange={(e) => set('headline', e.target.value)} className="font-display text-[18px] font-semibold" aria-describedby={id('hl-hint')} />
        <span id={id('hl-hint')} className="text-small text-studio-muted">
          A new line here is a line break in the ad.
        </span>
      </div>
      {options.length > 0 && (
        <div className="flex flex-col gap-1" role="group" aria-labelledby={id('alt-h')}>
          <span id={id('alt-h')} className="text-small font-medium">
            Other headlines
          </span>
          <div className="flex flex-wrap gap-1.5">
            {options.map((o) => (
              <Chip
                key={o.headline}
                selected={o.headline === concept.headline}
                disabled={busy || patch.isPending}
                className="h-auto min-h-7 py-0.5 text-left"
                onClick={() =>
                  patch.mutate(
                    { conceptId: concept.id, headline: o.headline, key_word: o.key_word },
                    { onSuccess: () => announce(`Concept ${concept.n} now uses that headline.`) },
                  )
                }
              >
                <span dir="auto" lang={lang} className="whitespace-normal">
                  {o.headline.replace(/\n/g, ' ')}
                </span>
              </Chip>
            ))}
          </div>
        </div>
      )}
      <div className="flex flex-col gap-1">
        <label htmlFor={id('kw')} className="text-small font-medium">
          Key word
        </label>
        <Input id={id('kw')} dir="auto" lang={lang} maxLength={60} value={d.key_word} onChange={(e) => set('key_word', e.target.value)} aria-describedby={id('kw-hint')} />
        <span id={id('kw-hint')} className="text-small text-studio-muted">
          Set in the accent colour. Leave it empty for no highlight.
        </span>
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor={id('body')} className="text-small font-medium">
          Body
        </label>
        <Textarea id={id('body')} dir="auto" lang={lang} rows={2} maxLength={600} value={d.body} onChange={(e) => set('body', e.target.value)} />
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor={id('cta')} className="text-small font-medium">
          Button
        </label>
        <Input id={id('cta')} dir="auto" lang={lang} maxLength={60} value={d.cta} onChange={(e) => set('cta', e.target.value)} />
      </div>
      <div className="flex flex-col gap-1">
        <span aria-hidden className="text-small font-medium">
          Layout: {styleLabel(d.layout)}
        </span>
        <LayoutPicker compact label={`Concept ${concept.n} layout`} value={d.layout} onChange={(v) => set('layout', v)} />
      </div>
      {d.layout === 'quote' && quotes.length > 0 && (
        <div className="flex flex-col gap-1">
          <label htmlFor={id('quote')} className="text-small font-medium">
            Quote
          </label>
          <select id={id('quote')} className={SELECT} value={d.quote ?? ''} onChange={(e) => set('quote', e.target.value === '' ? null : Number(e.target.value))}>
            <option value="">Pick one for me</option>
            {quotes.map((q, i) => (
              <option key={i} value={i}>
                {q.by ? `${q.text} (${q.by})` : q.text}
              </option>
            ))}
          </select>
        </div>
      )}
      {concept.offers.length > 0 && (
        <div className="flex flex-col gap-1">
          <span className="text-small font-medium">Offers used</span>
          <ul className="flex flex-wrap gap-1.5">
            {concept.offers.map((o) => (
              <li key={o} dir="auto" className="rounded-full border border-studio-border-strong bg-studio-raised px-2.5 py-0.5 text-small">
                {o}
              </li>
            ))}
          </ul>
        </div>
      )}
      <Candidates n={concept.n} photos={concept.photos} />
      {dropped.length > 0 && (
        <p className="text-small text-studio-muted" role="note">
          Left out because they weren't in your offers or brand kit: <span dir="auto">{dropped.join(' · ')}</span>
        </p>
      )}
      <details className="text-small">
        <summary className="cursor-pointer text-studio-muted">Photo idea</summary>
        <label htmlFor={id('photo')} className="sr-only">
          Photo prompt
        </label>
        <Textarea id={id('photo')} rows={3} value={d.photo_prompt} onChange={(e) => set('photo_prompt', e.target.value)} className="mt-1" />
      </details>
      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" variant="primary" disabled={!dirty || busy || !d.headline.trim()} loading={patch.isPending} onClick={save}>
          <Save aria-hidden />
          Save changes
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          disabled={busy}
          loading={regen.isPending}
          onClick={() => regen.mutate({ conceptId: concept.id, what: 'copy' }, { onSuccess: () => announce(`Writing concept ${concept.n} again.`) })}
        >
          <RefreshCw aria-hidden />
          New copy
        </Button>
      </div>
      {(patch.isError || regen.isError) && (
        <p role="alert" className="text-small text-studio-danger">
          {(patch.error ?? regen.error)?.message}
        </p>
      )}
    </li>
  )
}

/** The concepts for approval: edit any line, untick the ones not to make. */
export function ConceptsBoard({ camp, busy }: { camp: AdCampaign; busy: boolean }) {
  return (
    <ul className="grid gap-3 md:grid-cols-2" aria-label="Concepts">
      {camp.concepts.map((c) => (
        <ConceptCard key={c.id} camp={camp} concept={c} busy={busy} />
      ))}
    </ul>
  )
}
