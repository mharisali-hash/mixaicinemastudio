import { useId, useState } from 'react'
import { useNavigate } from 'react-router'
import { Plus, Printer, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { SwitchRow } from '@/components/studio/switch-row'
import { RefAdd, RefThumb } from '@/components/generate/RefSlot'
import { Segmented } from '@/features/image/ads/Segmented'
import { useBrandKits } from '@/hooks/useBrandKits'
import { useCreatePrint } from '@/hooks/usePrint'
import { AD_LANGUAGES } from '@/lib/ads'
import { guessLanguage, tooBig, type PrintCreate } from '@/lib/print'
import { announce } from '@/stores/ui'
import { defaultSize, SizePicker, type SizeValue } from './SizePicker'

const SELECT = 'h-8 w-full rounded-[6px] border border-studio-border-strong bg-studio-raised px-2 text-body focus-visible:outline-2 focus-visible:outline-studio-accent'
const MAX_OFFERS = 12
const MAX_PHOTOS = 6
const QUALITY = [
  { id: 'draft', label: 'Draft' },
  { id: 'final', label: 'Final' },
] as const

const LINE_FIELDS = [
  { key: 'tagline', label: 'Tagline', hint: 'Frukt & Grönt', max: 160 },
  { key: 'opening_hours', label: 'Opening hours', hint: 'Öppet alla dagar 08–22', max: 120 },
  { key: 'phone', label: 'Phone', hint: '08-123 45 67', max: 60 },
  { key: 'address', label: 'Address', hint: 'Storgatan 1, Stockholm', max: 200 },
  { key: 'website', label: 'Website', hint: 'example.se', max: 200 },
] as const

type LineKey = 'name' | (typeof LINE_FIELDS)[number]['key']

/** The print brief: the job and its size, the shop's own lines (set exactly as typed), the look, the options. */
export function PrintBriefForm() {
  const uid = useId()
  const id = (s: string) => `${uid}-${s}`
  const nav = useNavigate()
  const create = useCreatePrint()
  const { kits } = useBrandKits()
  const [size, setSize] = useState<SizeValue>(defaultSize)
  const [lines, setLines] = useState<Record<LineKey, string>>({ name: '', tagline: '', opening_hours: '', phone: '', address: '', website: '' })
  const [offers, setOffers] = useState<string[]>([])
  const [offerLine, setOfferLine] = useState('')
  const [business, setBusiness] = useState('')
  const [style, setStyle] = useState('')
  const [brief, setBrief] = useState('')
  // 'auto' reads the language from the shop's own words
  const [language, setLanguage] = useState('auto')
  const [kitId, setKitId] = useState('')
  const [photos, setPhotos] = useState<string[]>([])
  const [variants, setVariants] = useState(3)
  const [quality, setQuality] = useState<'draft' | 'final'>('final')
  const [printSafe, setPrintSafe] = useState(false)
  const [exportAll, setExportAll] = useState(false)

  const allText = [lines.name, lines.tagline, lines.opening_hours, lines.address, business, ...offers].join(' ')
  const guessed = guessLanguage(allText)
  const guessedLabel = AD_LANGUAGES.find((l) => l.value === guessed)?.label ?? 'English'
  const big = tooBig(size.width_cm, size.height_cm, size.dpi)
  const blocked = !lines.name.trim() ? 'Type the shop’s name first.' : big ? 'Pick a lower resolution for this size.' : ''

  const addOffer = () => {
    const v = offerLine.trim().replace(/\s+/g, ' ')
    if (v && !offers.includes(v) && offers.length < MAX_OFFERS) setOffers([...offers, v])
    setOfferLine('')
  }

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    if (blocked) return
    const body: PrintCreate = {
      job: size.job,
      width_cm: Math.round(size.width_cm * 100) / 100,
      height_cm: Math.round(size.height_cm * 100) / 100,
      dpi: size.dpi,
      shape: size.shape,
      viewing_distance: size.viewing_distance,
      business_type: business.trim(),
      lines: { ...Object.fromEntries(Object.entries(lines).map(([k, v]) => [k, v.trim()])), name: lines.name.trim(), offers },
      language: language === 'auto' ? guessed : language,
      style: style.trim(),
      brief: brief.trim(),
      brand_kit_id: kitId || null,
      photo_media_ids: photos,
      variants,
      quality,
      print_safe: printSafe,
      export_all: exportAll,
    }
    create.mutate(body, {
      onSuccess: (j) => {
        announce('Print job started. The designs are being made.')
        nav(`/admin/print/${j.id}`)
      },
    })
  }

  return (
    <form onSubmit={submit} aria-labelledby={id('h')} className="flex min-w-0 flex-col gap-4 rounded-[8px] border border-studio-border bg-studio-panel p-4 shadow-card">
      <h2 id={id('h')} className="font-display text-title font-semibold">
        New print job
      </h2>

      <SizePicker value={size} onChange={setSize} />

      <fieldset className="flex min-w-0 flex-col gap-2">
        <legend className="mb-1 text-small font-medium">The shop’s own lines</legend>
        <p id={id('lines-hint')} className="text-small text-studio-muted">
          Set exactly as you type them, in any language. Nothing here is rewritten, and no number is ever made up.
        </p>
        <div className="grid min-w-0 gap-2 sm:grid-cols-2">
          <div className="flex min-w-0 flex-col gap-1 sm:col-span-2">
            <Label htmlFor={id('name')}>Name (required)</Label>
            <Input
              id={id('name')}
              dir="auto"
              maxLength={120}
              value={lines.name}
              onChange={(e) => setLines({ ...lines, name: e.target.value })}
              placeholder="Stjärnans Livs"
              aria-describedby={id('lines-hint')}
              aria-required
            />
          </div>
          {LINE_FIELDS.map((f) => (
            <div key={f.key} className="flex min-w-0 flex-col gap-1">
              <Label htmlFor={id(f.key)}>{f.label}</Label>
              <Input id={id(f.key)} dir="auto" maxLength={f.max} value={lines[f.key]} onChange={(e) => setLines({ ...lines, [f.key]: e.target.value })} placeholder={f.hint} />
            </div>
          ))}
        </div>
        <div className="flex min-w-0 flex-col gap-1.5">
          <span id={id('off-h')} className="text-small font-medium">
            Offers and prices (optional)
          </span>
          {offers.length > 0 && (
            <ul aria-labelledby={id('off-h')} className="flex flex-wrap gap-1.5">
              {offers.map((o) => (
                <li key={o} className="inline-flex max-w-full items-center gap-1 rounded-[6px] border border-studio-border-strong bg-studio-raised py-0.5 pl-2 pr-0.5 text-small">
                  <span dir="auto" className="min-w-0 truncate">
                    {o}
                  </span>
                  <Button type="button" size="icon-sm" variant="ghost" aria-label={`Remove ${o}`} onClick={() => setOffers(offers.filter((x) => x !== o))}>
                    <X aria-hidden />
                  </Button>
                </li>
              ))}
            </ul>
          )}
          <div className="flex min-w-0 gap-2">
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
              placeholder="Bananer 19,90 kr/kg"
              className="min-w-0"
            />
            <Button type="button" variant="ghost" onClick={addOffer} disabled={!offerLine.trim() || offers.length >= MAX_OFFERS}>
              <Plus aria-hidden />
              Add
            </Button>
          </div>
        </div>
      </fieldset>

      <div className="grid min-w-0 gap-3 sm:grid-cols-2">
        <div className="flex min-w-0 flex-col gap-1">
          <Label htmlFor={id('biz')}>Kind of business</Label>
          <Input id={id('biz')} dir="auto" maxLength={160} value={business} onChange={(e) => setBusiness(e.target.value)} placeholder="livs / mini market" />
        </div>
        <div className="flex min-w-0 flex-col gap-1">
          <Label htmlFor={id('lang')}>Language</Label>
          <select id={id('lang')} className={SELECT} value={language} onChange={(e) => setLanguage(e.target.value)}>
            <option value="auto">Auto, from the lines ({guessedLabel})</option>
            {AD_LANGUAGES.map((l) => (
              <option key={l.value} value={l.value}>
                {l.label}
              </option>
            ))}
          </select>
        </div>
        <div className="flex min-w-0 flex-col gap-1 sm:col-span-2">
          <Label htmlFor={id('style')}>Style</Label>
          <Input id={id('style')} dir="auto" value={style} onChange={(e) => setStyle(e.target.value)} placeholder="Fresh and bright: greens, warm yellows, bold clean lettering" />
        </div>
        <div className="flex min-w-0 flex-col gap-1 sm:col-span-2">
          <Label htmlFor={id('brief')}>Anything else (optional)</Label>
          <Textarea id={id('brief')} dir="auto" rows={2} value={brief} onChange={(e) => setBrief(e.target.value)} />
        </div>
        <div className="flex min-w-0 flex-col gap-1">
          <Label htmlFor={id('kit')}>Brand kit</Label>
          <select id={id('kit')} className={SELECT} value={kitId} onChange={(e) => setKitId(e.target.value)}>
            <option value="">No brand kit</option>
            {kits.map((k) => (
              <option key={k.id} value={k.id}>
                {k.name}
              </option>
            ))}
          </select>
        </div>
        <div className="flex min-w-0 flex-col gap-1">
          <Label htmlFor={id('n')}>Design options: {variants}</Label>
          <input id={id('n')} type="range" min={3} max={6} step={1} value={variants} onChange={(e) => setVariants(Number(e.target.value))} className="h-8 accent-studio-accent" />
        </div>
      </div>

      <div className="flex flex-col gap-1.5" role="group" aria-labelledby={id('ph')}>
        <span id={id('ph')} className="text-small font-medium">
          Your own photos (optional, up to {MAX_PHOTOS})
        </span>
        <div className="flex flex-wrap items-start gap-2">
          {photos.map((p, i) => (
            <RefThumb key={p} id={p} label={`Photo ${i + 1}`} onRemove={() => setPhotos(photos.filter((x) => x !== p))} />
          ))}
          {photos.length < MAX_PHOTOS && (
            <RefAdd
              label="Photo"
              multiple
              max={MAX_PHOTOS - photos.length}
              taken={photos}
              description="Photo-led designs use these as they are, upscaled for print."
              onAdd={(items) => setPhotos([...photos, ...items.map((m) => m.id)].slice(0, MAX_PHOTOS))}
            />
          )}
        </div>
      </div>

      <div className="flex flex-col gap-1">
        <span aria-hidden className="text-small font-medium">
          Quality
        </span>
        <Segmented label="Quality" options={QUALITY} value={quality} onChange={setQuality} describedBy={id('q-hint')} className="self-start" />
        <span id={id('q-hint')} className="text-small text-studio-muted">
          {quality === 'draft' ? 'Draft: one picture each, no upscale; quick to look at.' : 'Final: the best of two pictures, upscaled for print.'}
        </span>
      </div>

      <SwitchRow id={id('safe')} checked={printSafe} onChange={setPrintSafe} title="Print-safe colours" hint="Tone down screen colours that would print dull. You can switch it per design later." />
      <SwitchRow
        id={id('files')}
        checked={exportAll}
        onChange={setExportAll}
        title="Make every option’s print files"
        hint={exportAll ? 'PDF, PNG, JPG, SVG and layers for every option as soon as they are laid out.' : 'Files are made when you download an option.'}
      />

      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" variant="primary" loading={create.isPending} aria-disabled={!!blocked || undefined} aria-describedby={id('why')}>
          <Printer aria-hidden />
          Design the options
        </Button>
        <span id={id('why')} aria-live="polite" className="text-small text-studio-muted">
          {blocked}
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
