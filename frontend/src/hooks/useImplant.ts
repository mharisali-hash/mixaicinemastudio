import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { useMe } from '@/hooks/useAuth'
import { qk } from '@/hooks/keys'
import { ccOk, implantApi, implantBusy, myPreviewApi, previewBusy, type ChestBox, type Engine, type Garment, type ImplantOption, type Measurements, type MyPreview, type Patient, type PreviewOption, type PatientCreate, type PatientPatch, type View } from '@/lib/implant'

const POLL_MS = 3000
const LIST = ['implant', 'list'] as const

export const patientKey = (id: string) => ['implant', id] as const

export function usePatients() {
  const { data: me } = useMe()
  return useQuery({ queryKey: LIST, queryFn: implantApi.list, enabled: !!(me?.admin || me?.implant) })
}

export function usePatient(id: string | undefined) {
  return useQuery({
    queryKey: patientKey(id ?? ''),
    queryFn: () => implantApi.get(id!),
    enabled: !!id,
    refetchInterval: (q) => (implantBusy(q.state.data) ? POLL_MS : false),
  })
}

function put(qc: QueryClient, p: Patient) {
  qc.setQueryData(patientKey(p.id), p)
  void qc.invalidateQueries({ queryKey: LIST })
}

export function useCreatePatient() {
  const qc = useQueryClient()
  return useMutation({ mutationFn: (body: PatientCreate) => implantApi.create(body), onSuccess: (p) => put(qc, p) })
}

export function usePatchPatient(id: string) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: (body: PatientPatch) => implantApi.patch(id, body), onSuccess: (p) => put(qc, p) })
}

export function useDeletePatient(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => implantApi.remove(id),
    onSuccess: () => {
      qc.removeQueries({ queryKey: patientKey(id) })
      void qc.invalidateQueries({ queryKey: LIST })
    },
  })
}

/** Session only: ends the consultation, which deletes the patient with every photo and picture. */
export function useEndConsultation(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => implantApi.end(id),
    onSuccess: () => {
      qc.removeQueries({ queryKey: patientKey(id) })
      void qc.invalidateQueries({ queryKey: LIST })
    },
  })
}

export function useConsentForm(id: string) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: (file: File) => implantApi.consentForm(id, file), onSuccess: (p) => put(qc, p) })
}

export function useUploadPhoto(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { file: File; view: View; garment: Garment; crop?: string }) => implantApi.photo(id, body),
    // the answer is only the photo, so fetch the patient again for the slot
    onSuccess: () => qc.invalidateQueries({ queryKey: patientKey(id) }),
  })
}

export function useRenderOptions(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ options, engines }: { options: ImplantOption[]; engines: Engine[] }) => implantApi.render(id, options, engines),
    onSuccess: (p) => put(qc, p),
  })
}

export function useApproveOption(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ sessionId, ...body }: { sessionId: string; option: number; engine: Engine; notes: string }) => implantApi.approve(id, sessionId, body),
    onSuccess: (p) => put(qc, p),
  })
}

export function useRetryPicture(id: string) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: (jobId: string) => implantApi.retry(jobId), onSuccess: () => qc.invalidateQueries({ queryKey: patientKey(id) }) })
}

export function useChestBox(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ genId, box }: { genId: string; box: ChestBox }) => implantApi.chestBox(genId, box),
    onSuccess: () => qc.invalidateQueries({ queryKey: patientKey(id) }),
  })
}

function useDebounced<T>(value: T, ms: number) {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return v
}

/** Sizes and warnings for the options as typed; one call for all of them, after the typing stops. */
export function useOptionChecks(id: string, options: ImplantOption[], measurements: Measurements) {
  const key = useDebounced(JSON.stringify({ options, measurements }), 400)
  const sent = (JSON.parse(key) as { options: ImplantOption[] }).options
  return useQuery({
    // outside patientKey, so refreshing the patient doesn't ask again
    queryKey: ['implant-check', id, key],
    queryFn: () => implantApi.check(id, sent),
    enabled: sent.every((o) => ccOk(o.left_cc) && ccOk(o.right_cc)),
    staleTime: Infinity,
    placeholderData: (prev) => prev,
    retry: false,
  })
}

export function useInvitePatient(id: string) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: (months?: number) => implantApi.invite(id, months), onSuccess: (r) => put(qc, r.patient) })
}

export function useUnlinkPatient(id: string) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: () => implantApi.unlink(id), onSuccess: (p) => put(qc, p) })
}

export function useConfirmPhotoAge(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ genId, dob }: { genId: string; dob: string }) => implantApi.confirmAge(id, genId, dob),
    onSuccess: () => qc.invalidateQueries({ queryKey: patientKey(id) }),
  })
}

// ---------------------------------------------------------------- My preview (the patient's side)

const PREVIEW = ['my-preview'] as const

export function useMyPreview() {
  const qc = useQueryClient()
  return useQuery({
    queryKey: PREVIEW,
    queryFn: () => myPreviewApi.get(qc.getQueryData(PREVIEW) !== undefined),
    refetchInterval: (q) => (previewBusy(q.state.data) ? POLL_MS : false),
  })
}

export function useLinkCode() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (code: string) => myPreviewApi.link(code),
    onSuccess: (p: MyPreview) => {
      qc.setQueryData(PREVIEW, p)
      // the rail's "My preview" entry follows the link
      void qc.invalidateQueries({ queryKey: qk.me })
    },
  })
}

export function useTrySize() {
  const qc = useQueryClient()
  return useMutation({ mutationFn: (o: Omit<PreviewOption, 'label'>) => myPreviewApi.tryOn(o), onSuccess: (p) => qc.setQueryData(PREVIEW, p) })
}
