import { useEffect, useId, useRef, useState } from 'react'
import { ImageIcon, ShieldAlert, Upload } from 'lucide-react'
import { StatusPill } from '@/components/studio/status-pill'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useChestBox, useConfirmPhotoAge, useUploadPhoto } from '@/hooks/useImplant'
import { CHEST_DEFAULT, cropBox, isDone, patientViews, VIEW_LABEL, type ChestBox, type Garment, type ImplantPicture, type Patient, type View } from '@/lib/implant'
import { generationStatus } from '@/lib/status'
import { announce } from '@/stores/ui'

export function Photos({ patient }: { patient: Patient }) {
  return (
    <section aria-labelledby="implant-photos-h" className="flex min-w-0 flex-col gap-3">
      <h2 id="implant-photos-h" className="font-display text-title font-semibold">
        Photos
      </h2>
      {patient.minor ? (
        <p role="note" className="rounded-[6px] border border-studio-border-strong bg-studio-panel p-3 text-body text-studio-muted">
          This patient is under 18, so no photos are taken or kept here. Plan the sizes below and show her the 3D model.
        </p>
      ) : (
        <ul className="grid min-w-0 gap-3 md:grid-cols-3">
          {patientViews(patient).map((v) => (
            <PhotoSlot key={v} patient={patient} view={v} />
          ))}
        </ul>
      )}
    </section>
  )
}

function PhotoSlot({ patient, view }: { patient: Patient; view: View }) {
  const uid = useId()
  const upload = useUploadPhoto(patient.id)
  const current = patient.photos[view]
  const name = patient.views?.[view] ?? VIEW_LABEL[view]
  // her own everyday photo is always in her clothes; the topless choice is for the clinical views only
  const own = view === 'own'
  const toplessOk = patient.consent.topless_ok && !own
  const [garment, setGarment] = useState<Garment>(current?.garment === 'topless' && toplessOk ? 'topless' : 'garment')
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<string | null>(null)
  // clinical shots crop the face off by default; her own photo keeps it, so she sees herself
  const [top, setTop] = useState(own ? 0 : 15)
  const [bottom, setBottom] = useState(100)
  const urlRef = useRef<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)

  useEffect(() => () => {
    if (urlRef.current) URL.revokeObjectURL(urlRef.current)
  }, [])

  const choose = (f: File | null) => {
    if (urlRef.current) URL.revokeObjectURL(urlRef.current)
    urlRef.current = f ? URL.createObjectURL(f) : null
    setFile(f)
    setPreview(urlRef.current)
    upload.reset()
  }

  // consent can be withdrawn after the choice was made
  const sendGarment: Garment = toplessOk ? garment : 'garment'
  const shown = preview ?? current?.url ?? null
  const [draft, setDraft] = useState<ChestBox | null>(null)
  const box = preview ? null : (draft ?? current?.chest_box ?? null)

  return (
    <li className="flex min-w-0 flex-col gap-2 rounded-[6px] border border-studio-border-strong bg-studio-panel p-3">
      <h3 className="text-body font-medium">{name}</h3>
      <div className="relative aspect-[3/4] overflow-hidden rounded-[4px] bg-studio-raised">
        {shown ? (
          <Fit src={shown} alt={preview ? `${name}: new photo, as it will be cropped` : `${name} photo`} box={box} />
        ) : (
          <span className="flex size-full items-center justify-center text-studio-muted">
            <ImageIcon aria-hidden className="size-6" />
            <span className="sr-only">No {name} photo yet</span>
          </span>
        )}
        {preview && (
          <>
            {/* the shaded bands are what the crop takes away */}
            <span aria-hidden className="absolute inset-x-0 top-0 bg-studio-text/55" style={{ height: `${top}%` }} />
            <span aria-hidden className="absolute inset-x-0 bottom-0 bg-studio-text/55" style={{ height: `${100 - bottom}%` }} />
          </>
        )}
      </div>
      {current && !preview && current.status !== 'ready' && current.status !== 'approved' && <StatusPill className="self-start" status={generationStatus(current.status)} />}
      {current?.age_flag && !preview && <AgeRecheck patient={patient} photo={current} name={name} />}
      {current && isDone(current) && !preview && !current.age_flag && <ChestArea patientId={patient.id} photo={current} view={view} name={name} draft={draft} setDraft={setDraft} />}

      {own ? (
        <p className="text-small text-studio-muted">
          A recent photo in fitted everyday clothes, standing or seated, with the chest clearly visible. Ask her to
          move the dupatta off the chest. Loose clothes show a softer change. Her clothes are never removed or changed.
        </p>
      ) : (
        <fieldset className="flex flex-col gap-1">
          <legend className="section-label mb-1">Garment</legend>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-body">
            <label className="flex items-center gap-1.5">
              <input type="radio" name={`${uid}-garment`} className="accent-studio-accent" checked={sendGarment === 'garment'} onChange={() => setGarment('garment')} />
              Bra/top
            </label>
            <label className="flex items-center gap-1.5 aria-disabled:opacity-60" aria-disabled={!toplessOk || undefined}>
              <input
                type="radio"
                name={`${uid}-garment`}
                className="accent-studio-accent"
                checked={sendGarment === 'topless'}
                aria-disabled={!toplessOk}
                aria-describedby={toplessOk ? undefined : `${uid}-why`}
                onChange={() => toplessOk && setGarment('topless')}
              />
              Topless
            </label>
          </div>
          {!toplessOk && (
            <p id={`${uid}-why`} className="text-small text-studio-muted">
              Topless needs the patient’s separate consent. Turn it on at the top of the page.
            </p>
          )}
        </fieldset>
      )}

      <div className="flex flex-col gap-1">
        <label htmlFor={`${uid}-file`} className="section-label">
          {current ? `Replace the ${name} photo` : `${name} photo`}
        </label>
        <input
          ref={fileInput}
          id={`${uid}-file`}
          type="file"
          accept="image/jpeg,image/png,image/webp"
          className="text-small file:mr-2 file:rounded-[6px] file:border file:border-studio-border-strong file:bg-studio-raised file:px-2 file:py-1 file:text-small"
          onChange={(e) => choose(e.target.files?.[0] ?? null)}
        />
      </div>

      {preview && (
        <div className="flex flex-col gap-1.5">
          <label htmlFor={`${uid}-top`} className="text-small">
            Cut from the top: {top}%
          </label>
          <input id={`${uid}-top`} type="range" min={0} max={60} step={1} value={top} aria-valuetext={`${top}%`} className="accent-studio-accent" onChange={(e) => setTop(Math.min(Number(e.target.value), bottom - 20))} />
          <label htmlFor={`${uid}-bottom`} className="text-small">
            Cut at the bottom: {bottom}%
          </label>
          <input id={`${uid}-bottom`} type="range" min={40} max={100} step={1} value={bottom} aria-valuetext={`${bottom}%`} className="accent-studio-accent" onChange={(e) => setBottom(Math.max(Number(e.target.value), top + 20))} />
        </div>
      )}

      {upload.isError && (
        <p role="alert" className="text-small text-studio-danger">
          {upload.error.message}
        </p>
      )}
      <Button
        type="button"
        size="sm"
        variant="primary"
        className="self-start"
        aria-label={`Upload the ${name} photo`}
        disabled={!file}
        loading={upload.isPending}
        onClick={() =>
          file &&
          upload.mutate(
            { file, view, garment: sendGarment, crop: top === 0 && bottom === 100 ? undefined : cropBox(top, bottom) },
            {
              onSuccess: () => {
                announce(`${name} photo uploaded`)
                choose(null)
                if (fileInput.current) fileInput.current.value = ''
              },
            },
          )
        }
      >
        <Upload aria-hidden />
        Upload
      </Button>
    </li>
  )
}

// the age check wasn't sure this topless photo shows an adult; typing the date of birth again lets it be used
function AgeRecheck({ patient, photo, name }: { patient: Patient; photo: ImplantPicture; name: string }) {
  const uid = useId()
  const confirm = useConfirmPhotoAge(patient.id)
  const [dob, setDob] = useState('')
  const noDob = patient.date_of_birth == null
  return (
    <form
      className="flex flex-col gap-1.5 rounded-[6px] border border-studio-warning/50 bg-studio-raised p-2"
      onSubmit={(e) => {
        e.preventDefault()
        if (dob) confirm.mutate({ genId: photo.id, dob }, { onSuccess: () => announce(`${name} photo can be used`) })
      }}
    >
      <p className="flex items-start gap-1.5 text-small">
        <ShieldAlert aria-hidden className="mt-0.5 size-4 shrink-0 text-studio-warning" />
        <span>
          Our check couldn’t confirm this photo shows an adult. It won’t be used until you confirm her date of birth again.
          {noDob && ' Add the date of birth above first.'}
        </span>
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor={`${uid}-dob`} className="text-small">
          Date of birth
        </label>
        <Input id={`${uid}-dob`} type="date" required disabled={noDob} className="w-40" value={dob} onChange={(e) => setDob(e.target.value)} />
        <Button type="submit" size="sm" variant="secondary" disabled={!dob || noDob} loading={confirm.isPending}>
          Confirm
        </Button>
      </div>
      {confirm.isError && (
        <p role="alert" className="text-small text-studio-danger">
          {confirm.error.message}
        </p>
      )}
    </form>
  )
}

// sized to the photo's own shape, so the 0–1000 box lands on the picture and not on the letterbox
function Fit({ src, alt, box }: { src: string; alt: string; box: ChestBox | null }) {
  const [ratio, setRatio] = useState(3 / 4)
  return (
    <span className="flex size-full items-center justify-center">
      <span className={`relative ${ratio <= 3 / 4 ? 'h-full' : 'w-full'}`} style={{ aspectRatio: ratio }}>
        <img src={src} alt={alt} className="size-full" onLoad={(e) => e.currentTarget.naturalHeight && setRatio(e.currentTarget.naturalWidth / e.currentTarget.naturalHeight)} />
        {box && (
          <span
            aria-hidden
            data-testid="chest-box"
            className="absolute rounded-[2px] border-2 border-dashed border-studio-accent"
            style={{ top: `${box[0] / 10}%`, left: `${box[1] / 10}%`, height: `${(box[2] - box[0]) / 10}%`, width: `${(box[3] - box[1]) / 10}%` }}
          />
        )}
      </span>
    </span>
  )
}

// the server's limits (finish.clean_box): at least 12% tall and 20% wide
const MIN_H = 12
const MIN_W = 20

function ChestArea({ patientId, photo, view, name, draft, setDraft }: { patientId: string; photo: ImplantPicture; view: View; name: string; draft: ChestBox | null; setDraft: (b: ChestBox | null) => void }) {
  const uid = useId()
  const save = useChestBox(patientId)
  const open = draft !== null
  // sliders work in whole percent; the box is per mille
  const [t, l, b, r] = (draft ?? photo.chest_box ?? CHEST_DEFAULT[view]).map((v) => Math.round(v / 10))
  const set = (next: [number, number, number, number]) => setDraft(next.map((v) => v * 10) as ChestBox)
  const sliders: { key: string; label: string; value: number; change: (v: number) => void }[] = [
    { key: 'top', label: 'Top', value: t, change: (v) => set([Math.min(v, b - MIN_H), l, b, r]) },
    { key: 'bottom', label: 'Bottom', value: b, change: (v) => set([t, l, Math.max(v, t + MIN_H), r]) },
    { key: 'left', label: 'Left', value: l, change: (v) => set([t, Math.min(v, r - MIN_W), b, r]) },
    { key: 'right', label: 'Right', value: r, change: (v) => set([t, l, b, Math.max(v, l + MIN_W)]) },
  ]

  return (
    <div className="flex flex-col gap-1.5">
      {!photo.chest_box && !open && <p className="text-small text-studio-muted">The chest area is found automatically on the first render; you can adjust it.</p>}
      <button
        type="button"
        aria-label={`Adjust chest area, ${name}`}
        aria-expanded={open}
        aria-controls={`${uid}-panel`}
        className="self-start rounded-[4px] text-small text-studio-accent underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-studio-accent"
        onClick={() => {
          save.reset()
          setDraft(open ? null : (photo.chest_box ?? CHEST_DEFAULT[view]))
        }}
      >
        Adjust chest area
      </button>
      {open && (
        <div id={`${uid}-panel`} className="flex flex-col gap-1.5">
          <p className="text-small text-studio-muted">Everything outside this area stays exactly as in the photo.</p>
          {sliders.map((s) => (
            <div key={s.key} className="flex flex-col gap-0.5">
              <label htmlFor={`${uid}-${s.key}`} className="text-small">
                {s.label}: {s.value}%
              </label>
              <input
                id={`${uid}-${s.key}`}
                type="range"
                min={0}
                max={100}
                step={1}
                value={s.value}
                aria-valuetext={`${s.value}%`}
                className="accent-studio-accent"
                onChange={(e) => s.change(Number(e.target.value))}
              />
            </div>
          ))}
          {save.isError && (
            <p role="alert" className="text-small text-studio-danger">
              {save.error.message}
            </p>
          )}
          <Button
            type="button"
            size="sm"
            variant="secondary"
            className="self-start"
            aria-label={`Save chest area, ${name}`}
            loading={save.isPending}
            onClick={() =>
              draft &&
              save.mutate(
                { genId: photo.id, box: draft },
                {
                  onSuccess: () => {
                    announce(`${name} chest area saved`)
                    setDraft(null)
                  },
                },
              )
            }
          >
            Save chest area
          </Button>
        </div>
      )}
    </div>
  )
}
