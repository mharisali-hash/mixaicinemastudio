import { useId, useState, type ReactNode } from 'react'
import { Sparkles } from 'lucide-react'
import { ErrorState } from '@/components/studio/states'
import { StatusPill } from '@/components/studio/status-pill'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { useLinkCode, useMyPreview, useTrySize } from '@/hooks/useImplant'
import {
  ccOk,
  LETTERS,
  PLACEMENT_LABEL,
  PROFILE_LABEL,
  SHAPE_LABEL,
  shortDay,
  VIEW_LABEL,
  type MyPreview,
  type Placement,
  type PreviewOption,
  type Profile,
  type ImplantShape,
} from '@/lib/implant'
import { generationStatus } from '@/lib/status'
import { announce } from '@/stores/ui'

// Urdu beside the English for the patient: full aerab, as everywhere in the app
function Ur({ children }: { children: ReactNode }) {
  return (
    <span lang="ur" dir="rtl" className="block text-body leading-loose text-studio-muted">
      {children}
    </span>
  )
}

// best effort only: the pictures are served inline and uncached, and the page offers no way to save them
const noSave = {
  onContextMenu: (e: React.MouseEvent) => e.preventDefault(),
  onDragStart: (e: React.DragEvent) => e.preventDefault(),
  draggable: false,
}

export function MyPreviewPage() {
  const q = useMyPreview()
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-6 px-4 py-6 md:px-8">
      <div>
        <h1 className="font-display text-title font-semibold">My preview</h1>
        <Ur>مِیرا پیشِ مَنْظَر</Ur>
      </div>
      {q.isPending && <Skeleton className="h-32" />}
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && !q.data.linked && <LinkForm />}
      {q.data?.linked && <Record me={q.data} />}
    </div>
  )
}

function LinkForm() {
  const uid = useId()
  const link = useLinkCode()
  const [code, setCode] = useState('')
  return (
    <form
      className="flex max-w-md flex-col gap-3 rounded-[6px] border border-studio-border-strong bg-studio-panel p-4"
      onSubmit={(e) => {
        e.preventDefault()
        if (code.trim()) link.mutate(code.trim(), { onSuccess: () => announce('Your clinic record is linked') })
      }}
    >
      <div>
        <label htmlFor={`${uid}-code`} className="text-body font-medium">
          Enter your clinic code
        </label>
        <Ur>اَپْنے کِلِینِک کا کوڈ لِکھِیں</Ur>
      </div>
      <p className="text-small text-studio-muted">Your clinic gave you an 8-letter code. It works once.</p>
      <Ur>یہ کوڈ آپ کو آپ کے کِلِینِک نے دِیا ہے۔ یہ صِرْف ایک بار کام کَرتا ہے۔</Ur>
      <Input
        id={`${uid}-code`}
        required
        autoComplete="one-time-code"
        autoCapitalize="characters"
        spellCheck={false}
        maxLength={20}
        className="w-48 font-mono tracking-widest"
        value={code}
        onChange={(e) => setCode(e.target.value.toUpperCase())}
      />
      {link.isError && (
        <p role="alert" className="text-small text-studio-danger">
          {link.error.message}
        </p>
      )}
      <Button type="submit" variant="primary" className="self-start" disabled={code.trim().length < 6} loading={link.isPending}>
        Link my record
      </Button>
    </form>
  )
}

const optionText = (o: Pick<PreviewOption, 'left_cc' | 'right_cc' | 'shape' | 'profile'>) =>
  `${o.left_cc === o.right_cc ? `${o.left_cc} cc` : `L ${o.left_cc} / R ${o.right_cc} cc`} · ${SHAPE_LABEL[o.shape] ?? o.shape} · ${PROFILE_LABEL[o.profile] ?? o.profile}`

function Record({ me }: { me: MyPreview }) {
  return (
    <>
      <div className="flex flex-col gap-1 rounded-[6px] border border-studio-border-strong bg-studio-panel p-3">
        <p className="text-body">
          {me.clinic} · {me.label}
        </p>
        <p className="text-small text-studio-muted">Computer simulation for discussion with your surgeon. Actual results vary. Kept until {shortDay(me.kept_until)}.</p>
        <Ur>یہ صِرْف آپ کے سَرْجَن سے بات چِیت کے لِیے ایک نَمُونہ ہے؛ اَصْل نَتِیجہ مُخْتَلِف ہو سَکْتا ہے۔</Ur>
      </div>

      {me.approved && (
        <section aria-labelledby="mp-approved" className="flex flex-col gap-1">
          <h2 id="mp-approved" className="font-display text-title font-semibold">
            Chosen with your surgeon
          </h2>
          <Ur>آپ کے سَرْجَن کے ساتھ چُنا گَیا</Ur>
          <p className="text-body">
            {optionText(me.approved.option)} · {PLACEMENT_LABEL[me.approved.option.placement] ?? me.approved.option.placement}
            {me.approved.approved_at && <span className="text-studio-muted"> · {shortDay(me.approved.approved_at)}</span>}
          </p>
        </section>
      )}

      <section aria-labelledby="mp-photos" className="flex flex-col gap-2">
        <div>
          <h2 id="mp-photos" className="font-display text-title font-semibold">
            Your photos
          </h2>
          <Ur>آپ کی تَصْوِیریں</Ur>
        </div>
        {me.photos.length ? (
          <ul className="grid gap-3 sm:grid-cols-3">
            {me.photos.map((p) => (
              <li key={p.id} className="flex flex-col gap-1">
                {p.url && <img src={p.url} alt={`${p.view_label} photo`} className="aspect-[3/4] w-full select-none rounded-[4px] bg-studio-raised object-contain" {...noSave} />}
                <span className="text-small text-studio-muted">{p.view_label}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-body text-studio-muted">Your clinic hasn’t added photos yet.</p>
        )}
      </section>

      {me.can_try && <TrySize />}

      <section aria-labelledby="mp-results" className="flex flex-col gap-3">
        <div>
          <h2 id="mp-results" className="font-display text-title font-semibold">
            Previews
          </h2>
          <Ur>آپ کے نَمُونے</Ur>
        </div>
        {me.sessions.length === 0 && <p className="text-body text-studio-muted">No previews yet.</p>}
        {me.sessions.map((s) => (
          <div key={s.id} className="flex flex-col gap-2 rounded-[6px] border border-studio-border-strong bg-studio-panel p-3">
            <p className="flex flex-wrap items-center gap-2 text-small text-studio-muted">
              {shortDay(s.created_at)}
              <Badge tone={s.by === 'you' ? 'accent' : 'neutral'}>{s.by === 'you' ? 'Your try' : 'From your clinic'}</Badge>
            </p>
            {s.options.map((o, i) => (
              <div key={i} className="flex flex-col gap-1.5">
                <p className="text-body font-medium">
                  {s.options.length > 1 && `${LETTERS[i]} · `}
                  {optionText(o)}
                </p>
                <ul className="grid gap-3 sm:grid-cols-3">
                  {s.results
                    .filter((r) => r.option === i)
                    .map((r) => (
                      <li key={r.id} className="flex flex-col gap-1">
                        {r.url ? (
                          <img src={r.url} alt={`${optionText(o)}, ${VIEW_LABEL[r.view] ?? r.view}`} className="aspect-[3/4] w-full select-none rounded-[4px] bg-studio-raised object-contain" {...noSave} />
                        ) : (
                          <span className="flex aspect-[3/4] items-center justify-center rounded-[4px] bg-studio-raised">
                            <StatusPill status={generationStatus(r.status)} />
                          </span>
                        )}
                        <span className="text-small text-studio-muted">{VIEW_LABEL[r.view] ?? r.view}</span>
                      </li>
                    ))}
                </ul>
              </div>
            ))}
          </div>
        ))}
      </section>
    </>
  )
}

const PROFILES: Profile[] = ['low', 'moderate', 'moderate_plus', 'high', 'extra_high']
const SHAPES: ImplantShape[] = ['round', 'teardrop']
const PLACEMENTS: Placement[] = ['subglandular', 'submuscular', 'dual_plane']
const SELECT = 'h-8 rounded-[6px] border border-studio-border-strong bg-studio-raised px-2 text-body'

function TrySize() {
  const uid = useId()
  const go = useTrySize()
  const [cc, setCc] = useState('300')
  const [shape, setShape] = useState<ImplantShape>('round')
  const [profile, setProfile] = useState<Profile>('moderate_plus')
  const [placement, setPlacement] = useState<Placement>('dual_plane')
  const n = Number(cc)
  return (
    <section aria-labelledby={`${uid}-h`} className="flex flex-col gap-3 rounded-[6px] border border-studio-border-strong bg-studio-panel p-3">
      <div>
        <h2 id={`${uid}-h`} className="font-display text-title font-semibold">
          Try a size
        </h2>
        <Ur>دُوسْرا سائز آزْمائِیں</Ur>
        <p className="text-small text-studio-muted">A new preview on the photos your clinic took. Your surgeon sees it too.</p>
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col gap-1">
          <label htmlFor={`${uid}-cc`} className="text-small">
            Size (cc)
          </label>
          <Input id={`${uid}-cc`} type="number" inputMode="numeric" min={100} max={900} step={25} className="w-24" aria-invalid={!ccOk(n)} value={cc} onChange={(e) => setCc(e.target.value)} />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor={`${uid}-shape`} className="text-small">
            Shape
          </label>
          <select id={`${uid}-shape`} className={SELECT} value={shape} onChange={(e) => setShape(e.target.value as ImplantShape)}>
            {SHAPES.map((v) => (
              <option key={v} value={v}>
                {SHAPE_LABEL[v]}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor={`${uid}-profile`} className="text-small">
            Profile
          </label>
          <select id={`${uid}-profile`} className={SELECT} value={profile} onChange={(e) => setProfile(e.target.value as Profile)}>
            {PROFILES.map((v) => (
              <option key={v} value={v}>
                {PROFILE_LABEL[v]}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor={`${uid}-placement`} className="text-small">
            Placement
          </label>
          <select id={`${uid}-placement`} className={SELECT} value={placement} onChange={(e) => setPlacement(e.target.value as Placement)}>
            {PLACEMENTS.map((v) => (
              <option key={v} value={v}>
                {PLACEMENT_LABEL[v]}
              </option>
            ))}
          </select>
        </div>
        <Button
          type="button"
          variant="primary"
          disabled={!ccOk(n)}
          loading={go.isPending}
          onClick={() =>
            go.mutate(
              { left_cc: n, right_cc: n, shape, profile, placement, fill: 'silicone', procedure: 'augmentation' },
              { onSuccess: () => announce('Your preview is on its way. It appears below when ready.') },
            )
          }
        >
          <Sparkles aria-hidden />
          Show me
        </Button>
      </div>
      {!ccOk(n) && <p className="text-small text-studio-muted">From 100 to 900 cc.</p>}
      {go.isError && (
        <p role="alert" className="text-small text-studio-danger">
          {go.error.message}
        </p>
      )}
    </section>
  )
}
