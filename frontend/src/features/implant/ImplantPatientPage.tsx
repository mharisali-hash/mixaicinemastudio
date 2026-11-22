import { useEffect, useId, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { ArrowLeft, Clock, FileDown, Trash2 } from 'lucide-react'
import { ErrorState } from '@/components/studio/states'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/alert-dialog'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { useConsentForm, useDeletePatient, useEndConsultation, usePatchPatient, usePatient } from '@/hooks/useImplant'
import { ENGINE_LABEL, implantBusy, LETTERS, sessionOnly, shortDay, type Patient } from '@/lib/implant'
import { announce } from '@/stores/ui'
import { BodyModel } from './BodyModel'
import { Measurements } from './Measurements'
import { Options } from './Options'
import { AgeCard, PatientAccount } from './PatientAccount'
import { Photos } from './Photos'
import { Results } from './Results'

export function ImplantPatientPage() {
  const { id } = useParams()
  const q = usePatient(id)
  if (q.isPending) return <Skeleton className="m-6 h-40" />
  if (q.isError) return <ErrorState className="m-6" error={q.error} onRetry={() => q.refetch()} />
  return <PatientView patient={q.data} />
}

function PatientView({ patient }: { patient: Patient }) {
  const busy = implantBusy(patient)
  const was = useRef(busy)
  useEffect(() => {
    if (was.current && !busy) announce('The pictures have finished.')
    was.current = busy
  }, [busy])

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-6 md:px-8">
      <Header patient={patient} />
      <div className="grid min-w-0 gap-3 md:grid-cols-2">
        <AgeCard key={patient.date_of_birth ?? 'none'} patient={patient} />
        <PatientAccount patient={patient} />
      </div>
      <Photos patient={patient} />
      <Measurements key={patient.id} patient={patient} />
      <Options patient={patient} />
      <Results patient={patient} />
      <BodyModel patient={patient} />
      <ChartSection patient={patient} />
    </div>
  )
}

function Header({ patient }: { patient: Patient }) {
  const uid = useId()
  const nav = useNavigate()
  const patch = usePatchPatient(patient.id)
  const form = useConsentForm(patient.id)
  const del = useDeletePatient(patient.id)
  const end = useEndConsultation(patient.id)
  const [confirm, setConfirm] = useState(false)
  const [ending, setEnding] = useState(false)
  const failed = patch.error ?? form.error ?? del.error ?? end.error

  return (
    <div className="flex min-w-0 flex-col gap-3">
      <div className="flex min-w-0 flex-wrap items-start gap-3">
        <Button asChild variant="ghost" size="sm">
          <Link to="/clinic/implant">
            <ArrowLeft aria-hidden />
            Patients
          </Link>
        </Button>
        <div className="order-last min-w-0 basis-full sm:order-none sm:basis-auto sm:flex-1">
          <h1 className="break-words font-display text-title font-semibold">{patient.label}</h1>
          <Badge tone="success">Consent recorded {shortDay(patient.consent.at)}</Badge>
        </div>
        <Button type="button" variant="ghost" size="sm" className="ml-auto sm:ml-0" onClick={() => setConfirm(true)}>
          <Trash2 aria-hidden />
          Delete patient
        </Button>
      </div>
      <div className="flex min-w-0 flex-wrap items-center gap-x-6 gap-y-2 rounded-[6px] border border-studio-border-strong bg-studio-panel p-3">
        <label className="flex items-center gap-2.5 text-body">
          <Switch
            checked={patient.consent.topless_ok}
            disabled={patch.isPending || patient.minor}
            onCheckedChange={(v) => patch.mutate({ topless_ok: v }, { onSuccess: () => announce(v ? 'Topless photos allowed' : 'Topless photos no longer allowed') })}
          />
          Topless clinical photos allowed
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor={`${uid}-form`} className="text-body">
            Signed consent form
          </label>
          <input
            id={`${uid}-form`}
            type="file"
            accept="application/pdf,image/jpeg,image/png"
            aria-describedby={`${uid}-form-state`}
            disabled={form.isPending}
            className="text-small file:mr-2 file:rounded-[6px] file:border file:border-studio-border-strong file:bg-studio-raised file:px-2 file:py-1 file:text-small"
            onChange={(e) => {
              const f = e.target.files?.[0]
              if (f) form.mutate(f, { onSuccess: () => announce('Consent form uploaded') })
              e.target.value = ''
            }}
          />
          <span id={`${uid}-form-state`} className="text-small text-studio-muted">
            {form.isPending ? 'Uploading…' : patient.consent.form ? 'A copy is on file.' : 'No copy uploaded yet. PDF, JPEG or PNG.'}
          </span>
        </div>
      </div>
      {sessionOnly(patient) && (
        <div role="note" className="flex min-w-0 flex-wrap items-center gap-3 rounded-[6px] border border-studio-warning/50 bg-studio-raised p-3">
          <Clock aria-hidden className="size-4 shrink-0 text-studio-warning" />
          <p className="min-w-0 flex-1 text-body">
            Session only: the photos and results are deleted when you end the consultation, or 12 hours after the last change. Download the chart first if you need it.
          </p>
          <Button type="button" size="sm" variant="danger" onClick={() => setEnding(true)}>
            End consultation and delete
          </Button>
        </div>
      )}
      {failed && (
        <p role="alert" className="text-small text-studio-danger">
          {failed.message}
        </p>
      )}
      <ConfirmDialog
        open={ending}
        onOpenChange={setEnding}
        title="End the consultation?"
        description={`End ${patient.label}’s consultation and delete every photo, result and chart now? This can’t be undone.`}
        confirmLabel="End and delete"
        tone="danger"
        onConfirm={() =>
          end.mutate(undefined, {
            onSuccess: () => {
              announce(`${patient.label}’s consultation ended; everything was deleted`)
              nav('/clinic/implant')
            },
          })
        }
      />
      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title="Delete patient?"
        description={`Delete ${patient.label} and every photo, result and chart? This can’t be undone.`}
        confirmLabel="Delete patient"
        tone="danger"
        onConfirm={() =>
          del.mutate(undefined, {
            onSuccess: () => {
              announce(`${patient.label} deleted`)
              nav('/clinic/implant')
            },
          })
        }
      />
    </div>
  )
}

function ChartSection({ patient }: { patient: Patient }) {
  const { chart } = patient
  return (
    <section aria-labelledby="implant-chart-h" className="flex min-w-0 flex-col gap-2">
      <h2 id="implant-chart-h" className="font-display text-title font-semibold">
        Chart
      </h2>
      {chart.url ? (
        <div className="flex flex-wrap items-center gap-3 rounded-[6px] border border-studio-border-strong bg-studio-panel p-3">
          <p className="text-body">
            Chart approved {shortDay(chart.approved_at)} · option {chart.option != null ? LETTERS[chart.option] : '—'}
            {chart.engine === 'warp' && ` (${ENGINE_LABEL.warp})`} · version {chart.versions}
          </p>
          <Button asChild size="sm" variant="primary">
            <a href={chart.url} download>
              <FileDown aria-hidden />
              Download chart
            </a>
          </Button>
        </div>
      ) : (
        <p className="text-body text-studio-muted">No chart yet. Approve an option above to make one.</p>
      )}
    </section>
  )
}
