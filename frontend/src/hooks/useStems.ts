import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { StemsMode } from '@/lib/stems'
import { qk } from './keys'
import { upsertJob } from './useJobs'

const POLL_MS = 1500

/** The splits made from one track, polled while one of them is still running. */
export function useStems(id: string | null | undefined) {
  return useQuery({
    queryKey: qk.stems(id ?? ''),
    queryFn: () => api.audio.stemsOf(id!),
    enabled: !!id,
    refetchInterval: (q) => (q.state.data?.jobs.length ? POLL_MS : false),
  })
}

/** Whether songs can be sung by a child, a choir or a Library voice on this server. */
export function useSongVoices() {
  return useQuery({ queryKey: ['song-voices'], queryFn: () => api.audio.songVoices(), staleTime: 5 * 60_000 })
}

export function useSplitStems(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { mode: StemsMode; version?: string; lyrics?: boolean }) => api.audio.stems(id, body),
    onSuccess: (res) => {
      upsertJob(qc, res.job)
      qc.invalidateQueries({ queryKey: qk.stems(id) })
      qc.invalidateQueries({ queryKey: qk.mediaLists })
    },
  })
}
