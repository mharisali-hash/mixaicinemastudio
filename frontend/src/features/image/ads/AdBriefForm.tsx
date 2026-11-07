import { useId, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { Megaphone, Plus, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Chip } from '@/components/studio/chip'
import { SwitchRow } from '@/components/studio/switch-row'
import { Make3DLink } from '@/components/generate/Make3DLink'
import { RefAdd, RefThumb } from '@/components/generate/RefSlot'
import { useBrandKits } from '@/hooks/useBrandKits'
import { useCreateAds } from '@/hooks/useAds'
import { AD_LANGUAGES, defaultFooter, FORMATS, IMAGE_MODELS, MAX_PRODUCTS, MAX_QUOTES, productsFromQuery, type AdCreate, type AdFormat, type AdQualityLevel, type FooterItems } from '@/lib/ads'
import { announce } from '@/stores/ui'
import { FooterPicker } from './FooterPicker'
import { Segmented } from './Segmented'

const SELECT = 'h-8 w-full rounded-[6px] border border-studio-border-strong bg-studio-raised px-2 text-body focus-visible:outline-2 focus-visible:outline-studio-accent'
const MAX_OFFERS = 6
const NAMES: Record<string, string> = { website: 'website or booking link', socials: 'social accounts' }

const QUALITY = [
  { id: 'draft', label: 'Draft' },
  { id: 'final', label: 'Final' },
] as const

type QuoteRow = { key: number; text: string; by: string }
let rowKey = 0

const listOf = (w: string[]) => (w.length < 2 ? w.join('') : `${w.slice(0, -1).join(', ')} and ${w[w.length - 1]}`)

/** The brief: who it's for, what's on offer, where it runs. Only the subject is required. */
export function AdBriefForm() {
  const uid = useId()
  const id = (s: string) => `${uid}-${s}`
  const nav = useNavigate()
  const [query] = useSearchParams()
  const create = useCreateAds()
  const { kits } = useBrandKits()
  // null until the user picks: then the default kit (or the only one) is the starting point
  const [picked, setKitId] = useState<string | null>(null)
  const [f, setF] = useState({
    title: '',
    subject: '',
    description: '',
    brief: '',
    occasion: '',
    market: '',
    language: 'en',
    tone: '',
    must_say: '',
    variants: 3,
    image_model: 'auto',
    review: true,
  })
  const [formats, setFormats] = useState<AdFormat[]>(['4:5'])
  const [offers, setOffers] = useState<string[]>([])
  const [offerLine, setOfferLine] = useState('')
  // the 3D viewer's product shots arrive as ?products=a,b,c
  const [products, setProducts] = useState<string[]>(() => productsFromQuery(query))
  // null: the kit's defaults, so switching kits starts from the new kit's own picks
  const [footer, setFooter] = useState<FooterItems | null>(null)
  const [quality, setQuality] = useState<AdQualityLevel>('final')
  const [candidates, setCandidates] = useState(3)
  const [quotes, setQuotes] = useState<QuoteRow[]>([])
  const [useStyle, setUseStyle] = useState(true)
  const editQuote = (key: number, v: Partial<QuoteRow>) => setQuotes((rows) => rows.map((r) => (r.key === key ? { ...r, ...v } : r)))
  const set = <K extends keyof typeof f>(k: K, v: (typeof f)[K]) => setF((o) => ({ ...o, [k]: v }))

  // no kit until the user picks one, even when there is only one
  const kitId = picked ?? ''
  const kit = kits.find((k) => k.id === kitId)
  const claims = kit?.claims ?? []
  const missing = kit
    ? [...(kit.contact?.website?.trim() || kit.contact?.booking_url?.trim() ? [] : ['website']), ...(kit.socials?.length ? [] : ['socials'])]
    : []
  const custom = offers.filter((o) => !claims.includes(o))
  const styleReady = kit?.ad_style?.status === 'ready'

  const toggleOffer = (o: string) => setOffers((cur) => (cur.includes(o) ? cur.filter((x) => x !== o) : cur.length < MAX_OFFERS ? [...cur, o] : cur))
  const addOffer = () => {
    const v = offerLine.trim().replace(/\s+/g, ' ')
    if (v && !offers.includes(v) && offers.length < MAX_OFFERS) setOffers([...offers, v])
    setOfferLine('')
  }
  const toggleFormat = (fm: AdFormat) => setFormats((cur) => (cur.includes(fm) ? (cur.length > 1 ? cur.filter((x) => x !== fm) : cur) : [...cur, fm]))
  const blocked = !f.subject.trim()

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    if (blocked) return
    const body: AdCreate = {
      ...f,
      brand_kit_id: kitId || null,
      formats,
      offers,
      product_media_ids: products,
      quality,
      candidates,
      quotes: quotes.map((q) => ({ text: q.text.trim(), by: q.by.trim() })).filter((q) => q.text),
      use_style: useStyle,
    }
    if (footer && kit) body.footer_items = footer
    create.mutate(body, {
      onSuccess: (c) => {
        announce('Ad campaign started. The concepts are being written.')
        nav(`/image/ads/${c.id}`)
      },
    })
  }

  return (
    <form onSubmit={submit} aria-labelledby={id('h')} className="flex flex-col gap-3 rounded-[8px] border border-studio-border bg-studio-panel p-4 shadow-card">
      <h2 id={id('h')} className="font-display text-title font-semibold">
        New campaign
      </h2>

      <div className="flex flex-col gap-1">
        <Label htmlFor={id('kit')}>Brand kit</Label>
        <select
          id={id('kit')}
          className={SELECT}
          value={kitId}
          onChange={(e) => {
            setKitId(e.target.value)
            setFooter(null)
          }}
          aria-describedby={id('kit-hint')}
        >
          <option value="">No brand kit</option>
          {kits.map((k) => (
            <option key={k.id} value={k.id}>
              {k.name}
              {k.is_default ? ' (default)' : ''}
            </option>
          ))}
        </select>
        <span id={id('kit-hint')} className="text-small text-studio-muted">
          {kit ? (
            missing.length ? (
              <>
                Add your {listOf(missing.map((m) => NAMES[m]))} to <Link className="underline underline-offset-2" to={`/brand-kits/${kit.id}`}>{kit.name}</Link> for the ad's contact footer.
              </>
            ) : (
              'Logo, colours, fonts and contact footer come from this kit.'
            )
          ) : kits.length ? (
            'Without a kit the ads get neutral colours, no logo and no footer.'
          ) : (
            <>
              <Link className="underline underline-offset-2" to="/brand-kits">Make a brand kit</Link> for your logo, colours and contact footer.
            </>
          )}
        </span>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1 sm:col-span-2">
          <Label htmlFor={id('subject')}>What are you advertising?</Label>
          <Input id={id('subject')} dir="auto" maxLength={200} value={f.subject} onChange={(e) => set('subject', e.target.value)} placeholder="Weekly home cleaning · a new cold brew · winter tyres" />
        </div>
        <div className="flex flex-col gap-1 sm:col-span-2">
          <Label htmlFor={id('desc')}>A line about it (optional)</Label>
          <Input id={id('desc')} dir="auto" value={f.description} onChange={(e) => set('description', e.target.value)} />
        </div>
      </div>

      <div className="flex flex-col gap-1.5" role="group" aria-labelledby={id('prod-h')}>
        <span id={id('prod-h')} className="text-small font-medium">
          Product photos (optional, up to {MAX_PRODUCTS})
        </span>
        <div className="flex flex-wrap items-start gap-2">
          {products.map((p, i) => (
            <div key={p} className="flex flex-col items-center gap-0.5">
              <RefThumb id={p} label={`Product ${i + 1}`} onRemove={() => setProducts(products.filter((x) => x !== p))} />
              <Make3DLink start={{ picture: p }} ariaLabel={`Make 3D from product ${i + 1}`} newTab className="px-1.5" />
            </div>
          ))}
          {products.length < MAX_PRODUCTS && (
            <RefAdd
              label="Product photo"
              multiple
              max={MAX_PRODUCTS - products.length}
              taken={products}
              description="The real product is placed into each scene, label and shape kept."
              // a 3D model's product shots are Ad Studio parts, re-picked from here
              includeHome="character,ad"
              onAdd={(items) => setProducts([...products, ...items.map((m) => m.id)].slice(0, MAX_PRODUCTS))}
            />
          )}
        </div>
      </div>

      <div className="flex flex-col gap-1">
        <Label htmlFor={id('brief')}>Brief (optional)</Label>
        <Textarea id={id('brief')} dir="auto" rows={3} value={f.brief} onChange={(e) => set('brief', e.target.value)} placeholder="Leave it empty and the creative director finds the ideas." />
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1">
          <Label htmlFor={id('lang')}>Language of the ads</Label>
          <select id={id('lang')} className={SELECT} value={f.language} onChange={(e) => set('language', e.target.value)}>
            {AD_LANGUAGES.map((l) => (
              <option key={l.value} value={l.value}>
                {l.label}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor={id('market')}>Market</Label>
          <Input id={id('market')} dir="auto" maxLength={120} value={f.market} onChange={(e) => set('market', e.target.value)} placeholder="Sweden" />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor={id('occ')}>Occasion or season</Label>
          <Input id={id('occ')} dir="auto" maxLength={120} value={f.occasion} onChange={(e) => set('occasion', e.target.value)} placeholder="autumn · Eid · Black Friday" />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor={id('tone')}>Tone</Label>
          <Input id={id('tone')} dir="auto" maxLength={120} value={f.tone} onChange={(e) => set('tone', e.target.value)} placeholder="warm, cosy, confident" />
        </div>
        <div className="flex flex-col gap-1 sm:col-span-2">
          <Label htmlFor={id('must')}>Must say (optional)</Label>
          <Input id={id('must')} dir="auto" maxLength={200} value={f.must_say} onChange={(e) => set('must_say', e.target.value)} />
        </div>
      </div>

      <fieldset className="flex flex-col gap-1.5">
        <legend className="text-small font-medium">Offers</legend>
        <p id={id('offers-hint')} className="text-small text-studio-muted">
          The copy only uses the offers ticked here, word for word; it never makes up prices or discounts.
        </p>
        {claims.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {claims.map((c) => (
              <Chip key={c} selected={offers.includes(c)} onClick={() => toggleOffer(c)}>
                <span dir="auto">{c}</span>
              </Chip>
            ))}
          </div>
        )}
        {custom.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {custom.map((c) => (
              <Chip key={c} selected onClick={() => toggleOffer(c)}>
                <span dir="auto">{c}</span>
              </Chip>
            ))}
          </div>
        )}
        <div className="flex gap-2">
          <label htmlFor={id('offer')} className="sr-only">
            Another offer
          </label>
          <Input
            id={id('offer')}
            dir="auto"
            maxLength={160}
            value={offerLine}
            onChange={(e) => setOfferLine(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                addOffer()
              }
            }}
            placeholder={claims.length ? 'Or type another offer' : 'e.g. From 215 kr/hour'}
            aria-describedby={id('offers-hint')}
          />
          <Button type="button" variant="ghost" onClick={addOffer} disabled={!offerLine.trim() || offers.length >= MAX_OFFERS}>
            <Plus aria-hidden />
            Add offer
          </Button>
        </div>
      </fieldset>

      <fieldset className="flex flex-col gap-1.5">
        <legend className="text-small font-medium">Formats</legend>
        <div className="flex flex-wrap gap-1.5">
          {FORMATS.map((fm) => (
            <Chip key={fm.id} selected={formats.includes(fm.id)} onClick={() => toggleFormat(fm.id)} aria-label={`${fm.label}, ${fm.hint}`}>
              {fm.label}
            </Chip>
          ))}
        </div>
      </fieldset>

      {kit && <FooterPicker kit={kit} legend="The footers show" value={footer ?? defaultFooter(kit)} onChange={setFooter} />}

      {styleReady && kit && (
        <div className="flex flex-col gap-1 rounded-[6px] border border-studio-border-strong bg-studio-raised p-2.5">
          <span className="text-body font-medium">Using your style</span>
          <p id={id('style-hint')} className="text-small text-studio-muted">
            The layouts, shapes and type follow the ads you added to {kit.name}.
          </p>
          <label htmlFor={id('style')} className="flex min-h-6 items-center gap-1.5 text-small">
            <input
              id={id('style')}
              type="checkbox"
              className="size-4 shrink-0 accent-studio-accent focus-visible:outline-2 focus-visible:outline-studio-accent"
              checked={useStyle}
              onChange={(e) => setUseStyle(e.target.checked)}
              aria-describedby={id('style-hint')}
            />
            Follow my ad style
          </label>
        </div>
      )}

      <fieldset className="flex flex-col gap-1.5">
        <legend className="text-small font-medium">Quotes (optional)</legend>
        <p id={id('quotes-hint')} className="text-small text-studio-muted">
          For the quote card, word for word. Only your own words are used; the AI never writes reviews.
        </p>
        {quotes.length > 0 && (
          <ul className="flex flex-col gap-2" aria-label="Quotes">
            {quotes.map((q, i) => (
              <li key={q.key} className="grid gap-1.5 sm:grid-cols-[minmax(0,1fr)_12rem_auto]">
                <div className="flex flex-col gap-1">
                  <label htmlFor={id(`q-${q.key}`)} className="sr-only">
                    Quote {i + 1}
                  </label>
                  <Textarea id={id(`q-${q.key}`)} dir="auto" rows={2} maxLength={300} value={q.text} onChange={(e) => editQuote(q.key, { text: e.target.value })} placeholder="What they said" aria-describedby={id('quotes-hint')} />
                </div>
                <div className="flex flex-col gap-1">
                  <label htmlFor={id(`qb-${q.key}`)} className="sr-only">
                    Who said quote {i + 1}
                  </label>
                  <Input id={id(`qb-${q.key}`)} dir="auto" maxLength={80} value={q.by} onChange={(e) => editQuote(q.key, { by: e.target.value })} placeholder="Anna, Solna" />
                </div>
                <Button type="button" size="icon-sm" variant="ghost" aria-label={`Remove quote ${i + 1}`} onClick={() => setQuotes((rows) => rows.filter((r) => r.key !== q.key))}>
                  <X aria-hidden />
                </Button>
              </li>
            ))}
          </ul>
        )}
        <div>
          <Button type="button" variant="ghost" size="sm" disabled={quotes.length >= MAX_QUOTES} onClick={() => setQuotes((rows) => [...rows, { key: ++rowKey, text: '', by: '' }])}>
            <Plus aria-hidden />
            Add a quote
          </Button>
        </div>
      </fieldset>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1">
          <span aria-hidden className="text-small font-medium">
            Quality
          </span>
          <Segmented label="Quality" options={QUALITY} value={quality} onChange={setQuality} describedBy={id('q-hint')} className="self-start" />
          <span id={id('q-hint')} className="text-small text-studio-muted">
            {quality === 'draft' ? 'Draft: one photo each and no upscale, quicker.' : 'Final: a few photos each, the best one kept, upscaled and checked.'}
          </span>
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor={id('cand')}>Photos to choose from: {quality === 'draft' ? 1 : candidates}</Label>
          <input
            id={id('cand')}
            type="range"
            min={1}
            max={4}
            step={1}
            value={quality === 'draft' ? 1 : candidates}
            disabled={quality === 'draft'}
            onChange={(e) => setCandidates(Number(e.target.value))}
            aria-describedby={id('cand-hint')}
            className="h-8 accent-studio-accent disabled:opacity-50"
          />
          <span id={id('cand-hint')} className="text-small text-studio-muted">
            {quality === 'draft' ? 'Only for Final.' : 'Made per concept and shape; the best one goes in the ads.'}
          </span>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1">
          <Label htmlFor={id('n')}>Concepts: {f.variants}</Label>
          <input id={id('n')} type="range" min={2} max={8} step={1} value={f.variants} onChange={(e) => set('variants', Number(e.target.value))} className="h-8 accent-studio-accent" />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor={id('model')}>Photo model</Label>
          <select id={id('model')} className={SELECT} value={f.image_model} onChange={(e) => set('image_model', e.target.value)} aria-describedby={id('model-hint')}>
            {IMAGE_MODELS.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
          {products.length > 0 && (
            <span id={id('model-hint')} className="text-small text-studio-muted">
              With product photos, the edit model places your product in each scene.
            </span>
          )}
        </div>
      </div>

      <SwitchRow
        id={id('rv')}
        checked={f.review}
        onChange={(v) => set('review', v)}
        title="Let me check the concepts first"
        hint={f.review ? 'It stops after the concepts so you can edit or untick them before any photo is made.' : 'One click: concepts, photos and finished ads in one go.'}
      />
      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" variant="primary" loading={create.isPending} aria-disabled={blocked || undefined} aria-describedby={id('why')}>
          <Megaphone aria-hidden />
          Make the ads
        </Button>
        <span id={id('why')} aria-live="polite" className="text-small text-studio-muted">
          {blocked ? 'Say what you are advertising first.' : ''}
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
