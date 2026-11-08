import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { RemixRequest } from '@/lib/remix'
import { qk } from './keys'
import { upsertJob } from './useJobs'

const POLL_MS = 2000

export function useRemixRecipes() {
  return useQuery({ queryKey: qk.remixRecipes, queryFn: () => api.audio.remixRecipes(), staleTime: 60_000 })
}

/** Tempo and key of one song; cached on the server, so it only costs the first time. */
export function useRemixAnalysis(id: string | null | undefined) {
  return useQuery({ queryKey: qk.remixAnalysis(id ?? ''), queryFn: () => api.audio.remixAnalyse(id!), enabled: !!id, staleTime: Infinity, retry: false })
}

/** The remixes made from one song, polled while any is still being made. */
export function useRemixes(id: string | null | undefined) {
  return useQuery({
    queryKey: qk.remixes(id ?? ''),
    queryFn: () => api.audio.remixesOf(id!),
    enabled: !!id,
    refetchInterval: (q) => (q.state.data?.jobs.length ? POLL_MS : false),
  })
}

export function useMakeRemix() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: RemixRequest) => api.audio.remix(body),
    onSuccess: (res, body) => {
      res.jobs.forEach((j) => upsertJob(qc, j))
      body.sources.forEach((id) => qc.invalidateQueries({ queryKey: qk.remixes(id) }))
      qc.invalidateQueries({ queryKey: qk.mediaLists })
    },
  })
}
