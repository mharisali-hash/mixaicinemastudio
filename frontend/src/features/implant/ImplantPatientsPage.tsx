import { useId, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { Plus, Stethoscope } from 'lucide-react'
import { EmptyState, ErrorState } from '@/components/studio/states'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { useCreatePatient, usePatients } from '@/hooks/useImplant'
import { ADULT_AGE, SESSION_ONLY, sessionOnly, shortDay, yearsSince } from '@/lib/implant'
import { announce } from '@/stores/ui'

export function ImplantPatientsPage() {
  const list = usePatients()
  const [adding, setAdding] = useState(false)
  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-4 px-4 py-6 md:px-8">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="font-display text-title font-semibold">Implant Preview</h1>
          <p className="text-body text-studio-muted">Private to you. Nothing here appears anywhere else in the studio.</p>
        </div>
        <Button variant="primary" onClick={() => setAdding(true)}>
          <Plus aria-hidden />
          New patient
        </Button>
      </div>

      {list.isPending && <Skeleton className="h-24" />}
      {list.isError && <ErrorState error={list.error} onRetry={() => list.refetch()} />}
      {list.data?.length === 0 && (
        <EmptyState icon={<Stethoscope />} title="No patients yet">
          Add one to start a consultation.
        </EmptyState>
      )}
      {!!list.data?.length && (
        <ul aria-label="Patients" className="flex flex-col gap-2">
          {list.data.map((p) => (
            <li key={p.id}>
              <Link
                to={`/clinic/implant/${p.id}`}
                className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 rounded-[6px] border border-studio-border bg-studio-raised px-3 py-2.5 hover:border-studio-border-hover focus-visible:outline-2 focus-visible:outline-studio-accent"
              >
                <span className="min-w-0 flex-1 truncate text-body font-medium">{p.label}</span>
                <span className="text-small text-studio-muted">last seen {shortDay(p.last_activity_at)}</span>
                {p.age != null && <span className="text-small text-studio-muted">{p.age} yrs</span>}
                {p.minor && <Badge tone="warning">Under 18</Badge>}
                {p.date_of_birth == null && <Badge tone="warning">Add date of birth</Badge>}
                {p.patient_linked && <Badge>Patient account</Badge>}
                {sessionOnly(p) && <Badge tone="warning">Session only</Badge>}
                {p.approved && <Badge tone="success">Chart ready</Badge>}
              </Link>
            </li>
          ))}
        </ul>
      )}
      <NewPatientDialog open={adding} onOpenChange={setAdding} />
    </div>
  )
}

function NewPatientDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const uid = useId()
  const nav = useNavigate()
  const create = useCreatePatient()
  const [label, setLabel] = useState('')
  const [dob, setDob] = useState('')
  const [guardian, setGuardian] = useState('')
  const [relation, setRelation] = useState('')
  const [guardianOk, setGuardianOk] = useState(false)
  const [consent, setConsent] = useState(false)
  const [topless, setTopless] = useState(false)
  const [months, setMonths] = useState('24')
  const [once, setOnce] = useState(false)

  const today = new Date().toISOString().slice(0, 10)
  const age = dob ? yearsSince(dob) : null
  const dobOk = age != null && dob <= today && age <= 110
  const minor = dobOk && age < ADULT_AGE
  const missing = [
    !label.trim() && 'a patient code or name',
    !dobOk && 'the date of birth',
    !consent && 'the consent tick',
    minor && (!guardian.trim() || !guardianOk) && 'the guardian’s name and consent',
  ].filter(Boolean) as string[]
  const monthsOk = Number.isInteger(Number(months)) && Number(months) >= 1 && Number(months) <= 120
  const ready = missing.length === 0 && (once || monthsOk)

  const reset = () => {
    setLabel('')
    setOnce(false)
    setDob('')
    setGuardian('')
    setRelation('')
    setGuardianOk(false)
    setConsent(false)
    setTopless(false)
    setMonths('24')
    create.reset()
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) reset()
        onOpenChange(v)
      }}
    >
      <DialogContent aria-describedby={`${uid}-desc`}>
        <DialogHeader>
          <DialogTitle>New patient</DialogTitle>
          <DialogDescription id={`${uid}-desc`}>Only you will see this patient’s photos and charts.</DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault()
            if (!ready) return
            create.mutate(
              {
                label: label.trim(),
                date_of_birth: dob,
                consent_attested: consent,
                topless_ok: topless && !minor,
                retention_months: once ? SESSION_ONLY : Number(months),
                ...(minor ? { guardian: { name: guardian.trim(), relation: relation.trim(), consented: guardianOk } } : {}),
              },
              {
                onSuccess: (p) => {
                  announce(`${p.label} added`)
                  onOpenChange(false)
                  reset()
                  nav(`/clinic/implant/${p.id}`)
                },
              },
            )
          }}
        >
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${uid}-label`}>Patient code or name</Label>
            <Input id={`${uid}-label`} maxLength={120} required value={label} onChange={(e) => setLabel(e.target.value)} autoFocus />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${uid}-dob`}>Date of birth</Label>
            <div className="flex flex-wrap items-center gap-2">
              <Input id={`${uid}-dob`} type="date" required max={today} className="w-44" aria-invalid={!!dob && !dobOk} aria-describedby={`${uid}-age`} value={dob} onChange={(e) => setDob(e.target.value)} />
              <span id={`${uid}-age`} className="text-small text-studio-muted" aria-live="polite">
                {dobOk ? `${age} years old` : dob ? 'Check the date' : ''}
              </span>
            </div>
          </div>
          {minor && (
            <fieldset className="flex flex-col gap-2 rounded-[6px] border border-studio-warning/50 p-3">
              <legend className="px-1 text-body font-medium">Under 18: parent or guardian</legend>
              <p className="text-small text-studio-muted">Photos and photo previews aren’t available for patients under 18. You can still plan sizes and use the 3D model.</p>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor={`${uid}-gname`}>Guardian’s name</Label>
                <Input id={`${uid}-gname`} maxLength={120} required value={guardian} onChange={(e) => setGuardian(e.target.value)} />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor={`${uid}-grel`}>Relation to the patient</Label>
                <Input id={`${uid}-grel`} maxLength={60} placeholder="e.g. mother" value={relation} onChange={(e) => setRelation(e.target.value)} />
              </div>
              <label className="flex items-start gap-2.5 text-body">
                <input type="checkbox" required className="mt-1 accent-studio-accent" checked={guardianOk} onChange={(e) => setGuardianOk(e.target.checked)} />
                <span>The guardian signed the consent form <span className="text-studio-muted">(required)</span></span>
              </label>
            </fieldset>
          )}
          <div className="flex flex-col gap-0.5">
            <label className="flex items-start gap-2.5 text-body">
              <input type="checkbox" required className="mt-1 accent-studio-accent" aria-describedby={`${uid}-consent-help`} checked={consent} onChange={(e) => setConsent(e.target.checked)} />
              <span>The patient signed the consent form <span className="text-studio-muted">(required)</span></span>
            </label>
            <p id={`${uid}-consent-help`} className="pl-6 text-small text-studio-muted">
              Keep the signed form in your records. You can upload a copy on the patient page.
            </p>
          </div>
          {!minor && (
            <div className="flex flex-col gap-0.5">
            <label className="flex items-start gap-2.5 text-body">
              <input type="checkbox" className="mt-1 accent-studio-accent" aria-describedby={`${uid}-topless-help`} checked={topless} onChange={(e) => setTopless(e.target.checked)} />
              <span>Topless clinical photos allowed</span>
            </label>
            <p id={`${uid}-topless-help`} className="pl-6 text-small text-studio-muted">
              Only with the patient’s separate consent. Without it, use photos in a bra or fitted top.
            </p>
            </div>
          )}
          <div className="flex flex-col gap-1.5">
            <label className="flex items-center gap-2 text-body">
              <input type="checkbox" className="accent-studio-accent" checked={once} onChange={(e) => setOnce(e.target.checked)} aria-describedby={`${uid}-once-help`} />
              Session only
            </label>
            <p id={`${uid}-once-help`} className="pl-6 text-small text-studio-muted">
              Photos and results are deleted when you end the consultation, or 12 hours after the last change.
            </p>
            {!once && (
              <div className="flex flex-wrap items-center gap-2 text-body">
                <label htmlFor={`${uid}-months`}>Keep the file for</label>
                <Input id={`${uid}-months`} type="number" inputMode="numeric" min={1} max={120} className="w-20" aria-invalid={!monthsOk} value={months} onChange={(e) => setMonths(e.target.value)} />
                <span>months</span>
              </div>
            )}
          </div>
          {create.isError && (
            <p role="alert" className="text-small text-studio-danger">
              {create.error.message}
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" disabled={!ready} aria-describedby={ready ? undefined : `${uid}-why`} loading={create.isPending}>
              Create
            </Button>
          </DialogFooter>
          {!ready && (
            <p id={`${uid}-why`} className="text-right text-small text-studio-muted">
              {missing.length ? `Still needed: ${missing.join(', ')}.` : 'Keep the file for 1 to 120 months.'}
            </p>
          )}
        </form>
      </DialogContent>
    </Dialog>
  )
}
