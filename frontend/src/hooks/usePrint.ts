import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { useAllows } from '@/hooks/useUsage'
import { printApi, printBusy, type DesignPatch, type PrintCreate, type PrintDesign, type PrintJob, type PrintJobType } from '@/lib/print'
import type { Job } from '@/lib/types'
import { qk } from './keys'
import { upsertJob } from './useJobs'

const POLL_MS = 2500

export const printKey = (id: string) => ['print', id] as const
const LIST = ['print', 'list'] as const

export function usePrintList() {
  const allows = useAllows()
  return useQuery({ queryKey: LIST, queryFn: printApi.list, enabled: allows('print') })
}

export function usePrintJob(id: string | undefined) {
  return useQuery({
    queryKey: printKey(id ?? ''),
    queryFn: () => printApi.get(id!),
    enabled: !!id,
    refetchInterval: (q) => (printBusy(q.state.data) ? POLL_MS : false),
  })
}

function put(qc: QueryClient, j: PrintJob) {
  qc.setQueryData(printKey(j.id), j)
  qc.invalidateQueries({ queryKey: LIST })
}

function afterJob(qc: QueryClient, id: string, job: Job) {
  upsertJob(qc, job)
  qc.invalidateQueries({ queryKey: qk.jobs })
  qc.invalidateQueries({ queryKey: printKey(id) })
}

export function useCreatePrint() {
  const qc = useQueryClient()
  return useMutation({ mutationFn: (body: PrintCreate) => printApi.create(body), onSuccess: (j) => put(qc, j) })
}

export function useDeletePrint() {
  const qc = useQueryClient()
  return useMutation({ mutationFn: (id: string) => printApi.remove(id), onSuccess: () => qc.invalidateQueries({ queryKey: LIST }) })
}

export function useContinuePrint(id: string) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: () => printApi.continue(id), onSuccess: (job) => afterJob(qc, id, job) })
}

export function usePatchDesign(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ designId, ...body }: DesignPatch & { designId: string }) => printApi.patchDesign(id, designId, body),
    onSuccess: (d: PrintDesign) => {
      qc.setQueryData<PrintJob>(printKey(id), (old) => (old ? { ...old, designs: old.designs.map((x) => (x.id === d.id ? d : x)) } : old))
    },
  })
}

export function useMakeFiles(id: string) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: (designIds: string[] | null) => printApi.files(id, designIds), onSuccess: (job) => afterJob(qc, id, job) })
}

export function useResize(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ designId, ...body }: { designId: string; width_cm: number; height_cm: number; job?: PrintJobType }) => printApi.resize(id, designId, body),
    onSuccess: (j) => put(qc, j),
  })
}

export function useRegeneratePicture(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ conceptId, what }: { conceptId: string; what: 'picture' | 'copy' }) => printApi.regenerate(id, conceptId, what),
    onSuccess: (j) => {
      put(qc, j)
      qc.invalidateQueries({ queryKey: qk.jobs })
    },
  })
}
