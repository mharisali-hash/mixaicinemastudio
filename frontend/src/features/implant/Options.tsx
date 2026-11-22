import { useId, useState } from 'react'
import { Link2, Plus, Sparkles, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useOptionChecks, useRenderOptions } from '@/hooks/useImplant'
import {
  ccOk,
  ENGINE_LABEL,
  ENGINES,
  FILL_LABEL,
  flaggedPhotos,
  isDone,
  LETTERS,
  newOption,
  PLACEMENT_LABEL,
  PROCEDURE_LABEL,
  PROFILE_LABEL,
  patientViews,
  SHAPE_LABEL,
  type Engine,
  type ImplantOption,
  type OptionCheck,
  type Patient,
  type SideCheck,
} from '@/lib/implant'
import { plural } from '@/lib/utils'
import { announce } from '@/stores/ui'

const SELECT = 'h-8 w-full rounded-[6px] border border-studio-border-strong bg-studio-raised px-2 text-body focus-visible:outline-2 focus-visible:outline-studio-accent'
const PRESET = [300, 350, 400]
const ENGINE_HELP: Record<Engine, string> = {
  ai: 'Photorealistic: the chest is redrawn, the rest of her photo kept.',
  warp: 'Her own photo, reshaped by the implant volume. Nothing is redrawn.',
}

export function Options({ patient }: { patient: Patient }) {
  const uid = useId()
  const render = useRenderOptions(patient.id)
  const [options, setOptions] = useState<ImplantOption[]>([newOption()])
  // "same both sides" keeps the right side following the left until it's switched off
  const [same, setSame] = useState<boolean[]>([true])
  const checks = useOptionChecks(patient.id, options, patient.measurements)
  const [engines, setEngines] = useState<Engine[]>(ENGINES)

  const photos = patientViews(patient).filter((v) => isDone(patient.photos[v])).length
  const valid = options.every((o) => ccOk(o.left_cc) && ccOk(o.right_cc))
  const why = patient.minor
    ? 'Under 18: size planning and the 3D model only.'
    : flaggedPhotos(patient).length
      ? 'Confirm the date of birth on the flagged photo first.'
      : !photos
        ? 'Upload at least one photo first.'
        : !valid ? 'Each side takes 100 to 900 cc.' : !engines.length ? 'Choose at least one way to show it.' : null

  const edit = (i: number, patch: Partial<ImplantOption>) =>
    setOptions((all) => all.map((o, j) => (j !== i ? o : { ...o, ...patch, ...(same[i] && patch.left_cc !== undefined ? { right_cc: patch.left_cc } : {}) })))

  return (
    <section id="implant-options" aria-labelledby={`${uid}-h`} className="flex min-w-0 flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <h2 id={`${uid}-h`} className="mr-auto font-display text-title font-semibold">
          Options
        </h2>
        <Button
          type="button"
          size="sm"
          variant="secondary"
          onClick={() => {
            setOptions(PRESET.map((cc) => newOption(cc)))
            setSame(PRESET.map(() => true))
          }}
        >
          300 / 350 / 400 cc moderate-plus
        </Button>
        {options.length < 3 && (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => {
              setOptions((all) => [...all, newOption(all[all.length - 1]?.left_cc ?? 300)])
              setSame((s) => [...s, true])
            }}
          >
            <Plus aria-hidden />
            Add an option
          </Button>
        )}
      </div>

      <ul className="grid min-w-0 gap-3 md:grid-cols-3">
        {options.map((o, i) => (
          <li key={i} className="min-w-0">
            <fieldset className="flex flex-col gap-2 rounded-[6px] border border-studio-border-strong bg-studio-panel p-3">
              <legend className="sr-only">Option {LETTERS[i]}</legend>
              <div className="flex items-center gap-2">
                <span aria-hidden className="font-display text-title font-semibold">
                  {LETTERS[i]}
                </span>
                {options.length > 1 && (
                  <Button
                    type="button"
                    size="icon-sm"
                    variant="ghost"
                    className="ml-auto"
                    aria-label={`Remove option ${LETTERS[i]}`}
                    onClick={() => {
                      setOptions((all) => all.filter((_, j) => j !== i))
                      setSame((s) => s.filter((_, j) => j !== i))
                    }}
                  >
                    <Trash2 aria-hidden />
                  </Button>
                )}
              </div>
              <Field id={`${uid}-${i}-label`} label="Label">
                <Input id={`${uid}-${i}-label`} maxLength={60} placeholder="For example: Natural" value={o.label} onChange={(e) => edit(i, { label: e.target.value })} />
              </Field>
              <div className="grid grid-cols-2 gap-2">
                <Field id={`${uid}-${i}-l`} label="Left (cc)">
                  <Input id={`${uid}-${i}-l`} type="number" inputMode="numeric" min={100} max={900} step={5} aria-invalid={!ccOk(o.left_cc)} value={o.left_cc || ''} onChange={(e) => edit(i, { left_cc: Number(e.target.value) })} />
                </Field>
                <Field id={`${uid}-${i}-r`} label="Right (cc)">
                  <Input
                    id={`${uid}-${i}-r`}
                    type="number"
                    inputMode="numeric"
                    min={100}
                    max={900}
                    step={5}
                    aria-invalid={!ccOk(o.right_cc)}
                    value={o.right_cc || ''}
                    onChange={(e) => {
                      setSame((s) => s.map((v, j) => (j === i ? false : v)))
                      edit(i, { right_cc: Number(e.target.value) })
                    }}
                  />
                </Field>
              </div>
              <button
                type="button"
                aria-pressed={same[i]}
                className="flex items-center gap-1 self-start rounded-[4px] text-small text-studio-accent underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-studio-accent"
                onClick={() => {
                  const on = !same[i]
                  setSame((s) => s.map((v, j) => (j === i ? on : v)))
                  if (on) setOptions((all) => all.map((x, j) => (j === i ? { ...x, right_cc: x.left_cc } : x)))
                }}
              >
                <Link2 aria-hidden className="size-3.5" />
                Same both sides
              </button>
              <Pick id={`${uid}-${i}-profile`} label="Profile" value={o.profile} labels={PROFILE_LABEL} onChange={(profile) => edit(i, { profile })} />
              <Pick id={`${uid}-${i}-shape`} label="Shape" value={o.shape} labels={SHAPE_LABEL} onChange={(shape) => edit(i, { shape })} />
              <Pick id={`${uid}-${i}-placement`} label="Placement" value={o.placement} labels={PLACEMENT_LABEL} onChange={(placement) => edit(i, { placement })} />
              <Pick id={`${uid}-${i}-fill`} label="Fill" value={o.fill} labels={FILL_LABEL} onChange={(fill) => edit(i, { fill })} />
              <Pick id={`${uid}-${i}-procedure`} label="Procedure" value={o.procedure} labels={PROCEDURE_LABEL} onChange={(procedure) => edit(i, { procedure })} />
            </fieldset>
            <Fit check={checks.data?.[i]} braSize={patient.measurements.bra_size?.trim()} />
          </li>
        ))}
      </ul>

      {checks.data && <p className="text-small text-studio-muted">Approximate; confirm with the manufacturer’s chart.</p>}
      <fieldset className="flex flex-col gap-1.5">
        <legend className="section-label mb-1">Show it as</legend>
        <div className="flex flex-wrap gap-x-6 gap-y-2">
          {ENGINES.map((e) => (
            <label key={e} className="flex max-w-sm items-start gap-2 text-body">
              <input
                type="checkbox"
                className="mt-1 accent-studio-accent"
                checked={engines.includes(e)}
                onChange={(ev) => setEngines((all) => (ev.target.checked ? ENGINES.filter((x) => x === e || all.includes(x)) : all.filter((x) => x !== e)))}
              />
              <span>
                {ENGINE_LABEL[e]}
                <span className="block text-small text-studio-muted">{ENGINE_HELP[e]}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>
      {render.isError && (
        <p role="alert" className="text-small text-studio-danger">
          {render.error.message}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          size="lg"
          variant="primary"
          disabled={!!why}
          aria-describedby={why ? `${uid}-why` : undefined}
          loading={render.isPending}
          onClick={() =>
            render.mutate(
              { options: options.map((o) => ({ ...o, label: o.label.trim() })), engines },
              { onSuccess: () => announce('Rendering the options. The pictures appear below as they finish.') },
            )
          }
        >
          <Sparkles aria-hidden />
          Render the options
          <span className="font-normal opacity-85">
            · {plural(options.length, 'option')} × {plural(photos, 'photo')}
            {engines.length > 1 && ' × 2 ways'} = {plural(options.length * photos * engines.length, 'picture')}
          </span>
        </Button>
        {why && (
          <p id={`${uid}-why`} className="text-small text-studio-muted">
            {why}
          </p>
        )}
      </div>
    </section>
  )
}

const cups = (n: number) => `about +${n} cup${n === 1 ? '' : 's'}`
const dims = (s: SideCheck) => `≈ ${s.width_cm} cm wide · ${s.projection_cm} cm projection · ${cups(s.cup_change)}`

function Fit({ check, braSize }: { check: OptionCheck | undefined; braSize?: string }) {
  const cupLine = (cup: string | null | undefined) => (cup ? `From her current ${braSize} to ${cup}` : null)
  const l = cupLine(check?.sides.left.target_cup)
  const r = cupLine(check?.sides.right.target_cup)
  // the live region stays mounted so the first answer is read out too
  return (
    <div role="status" aria-live="polite" className="mt-2 flex flex-col gap-1 text-small">
      {check && (
        <>
          {dims(check.sides.left) === dims(check.sides.right) ? (
            <p>{dims(check.sides.left)}</p>
          ) : (
            <>
              <p>L {dims(check.sides.left)}</p>
              <p>R {dims(check.sides.right)}</p>
            </>
          )}
          {!braSize ? (
            <p className="text-studio-muted">Add her bra size in Measurements to see the expected cup.</p>
          ) : l === r ? (
            l && <p>{l}</p>
          ) : (
            <>
              {l && <p>L {l}</p>}
              {r && <p>R {r}</p>}
            </>
          )}
          {check.checks.length > 0 && (
            <ul className="flex flex-col gap-1">
              {check.checks.map((c, j) => (
                <li key={j} className={c.level === 'warn' ? 'rounded-[4px] border border-studio-warning px-2 py-1 text-studio-warning' : 'text-studio-muted'}>
                  {c.level === 'warn' && <span className="sr-only">Warning: </span>}
                  {c.text}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}

function Field({ id, label, children }: { id: string; label: string; children: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <label htmlFor={id} className="text-small">
        {label}
      </label>
      {children}
    </div>
  )
}

function Pick<T extends string>({ id, label, value, labels, onChange }: { id: string; label: string; value: T; labels: Record<T, string>; onChange: (v: T) => void }) {
  return (
    <Field id={id} label={label}>
      <select id={id} className={SELECT} value={value} onChange={(e) => onChange(e.target.value as T)}>
        {(Object.keys(labels) as T[]).map((k) => (
          <option key={k} value={k}>
            {labels[k]}
          </option>
        ))}
      </select>
    </Field>
  )
}
