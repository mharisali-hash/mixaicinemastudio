import { useId, useRef, useState } from 'react'
import { Check, Copy, UserPlus, UserX } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/alert-dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useInvitePatient, usePatchPatient, useUnlinkPatient } from '@/hooks/useImplant'
import { ADULT_AGE, PATIENT_KEEP, shortDay, yearsSince, type Patient } from '@/lib/implant'
import { announce } from '@/stores/ui'

const CARD = 'flex min-w-0 flex-col gap-3 rounded-[6px] border border-studio-border-strong bg-studio-panel p-3'

/** Date of birth, age and, under 18, the guardian. Older records without a date of birth are asked for one. */
export function AgeCard({ patient }: { patient: Patient }) {
  const uid = useId()
  const patch = usePatchPatient(patient.id)
  const [editing, setEditing] = useState(patient.date_of_birth == null)
  const [dob, setDob] = useState(patient.date_of_birth ?? '')
  const [gName, setGName] = useState('')
  const [gRel, setGRel] = useState('')
  const [gOk, setGOk] = useState(false)
  const today = new Date().toISOString().slice(0, 10)
  const age = dob ? yearsSince(dob) : null
  const dobOk = age != null && dob <= today && age <= 110
  const minorNext = dobOk && age < ADULT_AGE
  const guardian = patient.consent.guardian
  // a guardian is asked for when the record is (or is about to become) under 18 and has none yet
  const askGuardian = !guardian && (patient.minor || (editing && minorNext))
  const guardianReady = !askGuardian || (gName.trim() && gOk)

  return (
    <section aria-labelledby={`${uid}-h`} className={CARD}>
      <div className="flex flex-wrap items-center gap-2">
        <h2 id={`${uid}-h`} className="mr-auto text-body font-semibold">
          Age
        </h2>
        {patient.age != null && <span className="text-body">{patient.age} years old</span>}
        {patient.minor && <Badge tone="warning">Under 18</Badge>}
        {!editing && (
          <Button type="button" size="sm" variant="ghost" onClick={() => (setDob(patient.date_of_birth ?? ''), setEditing(true))}>
            Correct date of birth
          </Button>
        )}
      </div>
      {patient.date_of_birth == null && <p className="text-small text-studio-warning">This record was made before the date of birth was asked. Please add it.</p>}
      {patient.minor && (
        <p role="note" className="text-small text-studio-muted">
          Under 18: photos and photo previews aren’t available. Size planning and the 3D model below still work.
        </p>
      )}
      {guardian && (
        <p className="text-small">
          Guardian: {guardian.name}
          {guardian.relation && ` (${guardian.relation})`}, consent recorded {shortDay(guardian.consented_at)}
        </p>
      )}
      {(editing || askGuardian) && (
        <form
          className="flex flex-col gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            if (!guardianReady || (editing && !dobOk)) return
            patch.mutate(
              {
                ...(editing ? { date_of_birth: dob } : {}),
                ...(askGuardian ? { guardian: { name: gName.trim(), relation: gRel.trim(), consented: gOk } } : {}),
              },
              {
                onSuccess: (p) => {
                  announce(p.minor ? 'Saved. Under 18: photos removed, planning and 3D only.' : 'Date of birth saved')
                  setEditing(false)
                },
              },
            )
          }}
        >
          {editing && (
            <div className="flex flex-wrap items-center gap-2">
              <Label htmlFor={`${uid}-dob`}>Date of birth</Label>
              <Input id={`${uid}-dob`} type="date" required max={today} className="w-44" aria-invalid={!!dob && !dobOk} value={dob} onChange={(e) => setDob(e.target.value)} />
              <span className="text-small text-studio-muted" aria-live="polite">
                {dobOk ? `${age} years old` : dob ? 'Check the date' : ''}
              </span>
            </div>
          )}
          {editing && minorNext && !patient.minor && (
            <p role="alert" className="text-small text-studio-warning">
              Under 18: saving removes this patient’s photos, results and charts, and unlinks her account.
            </p>
          )}
          {askGuardian && (
            <fieldset className="flex flex-col gap-2">
              <legend className="mb-1 text-small font-medium">Parent or guardian</legend>
              <div className="flex flex-wrap gap-2">
                <Input aria-label="Guardian’s name" placeholder="Guardian’s name" maxLength={120} className="w-56" value={gName} onChange={(e) => setGName(e.target.value)} />
                <Input aria-label="Relation to the patient" placeholder="Relation, e.g. mother" maxLength={60} className="w-48" value={gRel} onChange={(e) => setGRel(e.target.value)} />
              </div>
              <label className="flex items-start gap-2.5 text-body">
                <input type="checkbox" className="mt-1 accent-studio-accent" checked={gOk} onChange={(e) => setGOk(e.target.checked)} />
                The guardian signed the consent form
              </label>
            </fieldset>
          )}
          {patch.isError && (
            <p role="alert" className="text-small text-studio-danger">
              {patch.error.message}
            </p>
          )}
          <div className="flex gap-2">
            <Button type="submit" size="sm" variant="primary" disabled={!guardianReady || (editing && !dobOk)} loading={patch.isPending}>
              Save
            </Button>
            {editing && patient.date_of_birth != null && (
              <Button type="button" size="sm" variant="ghost" onClick={() => setEditing(false)}>
                Cancel
              </Button>
            )}
          </div>
        </form>
      )}
    </section>
  )
}

/** The patient's own account: a one-time code to link it, how long the record is kept, and unlinking. */
export function PatientAccount({ patient }: { patient: Patient }) {
  const uid = useId()
  const invite = useInvitePatient(patient.id)
  const unlink = useUnlinkPatient(patient.id)
  const patch = usePatchPatient(patient.id)
  const [code, setCode] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const field = useRef<HTMLInputElement>(null)
  const acct = patient.patient_account
  const kept = (PATIENT_KEEP as readonly number[]).includes(patient.retention_months)
  const [months, setMonths] = useState<number>(kept ? patient.retention_months : 3)
  const failed = invite.error ?? unlink.error ?? patch.error

  if (patient.minor) return null
  const noDob = patient.date_of_birth == null

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code ?? '')
      setCopied(true)
    } catch {
      field.current?.select()
    }
  }

  return (
    <section aria-labelledby={`${uid}-h`} className={CARD}>
      <div className="flex flex-wrap items-center gap-2">
        <h2 id={`${uid}-h`} className="mr-auto text-body font-semibold">
          Patient account
        </h2>
        {acct.linked ? <Badge tone="success">Linked: {acct.name}</Badge> : acct.invite_expires_at ? <Badge tone="warning">Code sent, until {shortDay(acct.invite_expires_at)}</Badge> : null}
      </div>
      <p className="text-small text-studio-muted">
        The patient sees her own photos and results under “My preview” and can try other sizes on the photos you took. She can’t upload, download or see anything else. You see every preview she makes.
      </p>

      <div className="flex flex-wrap items-center gap-2 text-body">
        <label htmlFor={`${uid}-keep`}>Keep this record for</label>
        <select
          id={`${uid}-keep`}
          className="h-8 rounded-[6px] border border-studio-border-strong bg-studio-raised px-2 text-body"
          value={months}
          disabled={patch.isPending}
          onChange={(e) => {
            const m = Number(e.target.value)
            setMonths(m)
            if (acct.linked || acct.invite_expires_at) patch.mutate({ retention_months: m }, { onSuccess: () => announce(`Kept for ${m} months`) })
          }}
        >
          {PATIENT_KEEP.map((m) => (
            <option key={m} value={m}>
              {m} {m === 1 ? 'month' : 'months'}
            </option>
          ))}
        </select>
        <span className="text-small text-studio-muted">after the last activity, then deleted with her access.</span>
      </div>

      {code && (
        <div className="flex flex-col gap-1.5 rounded-[6px] border border-studio-accent/40 bg-studio-raised p-3">
          <label htmlFor={`${uid}-code`} className="text-small font-medium">
            One-time code, shown only now
          </label>
          <div className="flex flex-wrap gap-2">
            <Input id={`${uid}-code`} ref={field} readOnly value={code} className="w-40 font-mono text-body tracking-widest" spellCheck={false} onFocus={(e) => e.currentTarget.select()} />
            <Button type="button" size="sm" onClick={copy} aria-live="polite">
              {copied ? <Check aria-hidden /> : <Copy aria-hidden />}
              {copied ? 'Copied' : 'Copy'}
            </Button>
          </div>
          <p className="text-small text-studio-muted">
            Ask her to sign in (or sign up) at jafat.ai, open <span className="font-mono">/my-preview</span> and type the code. It works once, for 7 days.
          </p>
        </div>
      )}

      {failed && (
        <p role="alert" className="text-small text-studio-danger">
          {failed.message}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        {acct.linked ? (
          <Button type="button" size="sm" variant="ghost" onClick={() => setConfirm(true)}>
            <UserX aria-hidden />
            Unlink patient
          </Button>
        ) : (
          <Button
            type="button"
            size="sm"
            variant="secondary"
            disabled={noDob}
            aria-describedby={noDob ? `${uid}-nodob` : undefined}
            loading={invite.isPending}
            onClick={() =>
              invite.mutate(months, {
                onSuccess: (r) => {
                  setCode(r.code)
                  setCopied(false)
                  announce('Invite code ready')
                },
              })
            }
          >
            <UserPlus aria-hidden />
            {acct.invite_expires_at ? 'New code' : 'Invite patient'}
          </Button>
        )}
        {noDob && (
          <p id={`${uid}-nodob`} className="text-small text-studio-muted">
            Add the date of birth first.
          </p>
        )}
      </div>
      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title="Unlink the patient?"
        description="She loses access to this record straight away. Her photos and results stay in your file."
        confirmLabel="Unlink"
        tone="danger"
        onConfirm={() =>
          unlink.mutate(undefined, {
            onSuccess: () => {
              setCode(null)
              announce('Patient unlinked')
            },
          })
        }
      />
    </section>
  )
}
