import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { busy, type Ad, type EditOp, type Episode, type EpisodeCreate, type EpisodePatch, type InsertBody, type PublishBody, type ShowCreate, type ShowPatch, type VideoBody } from '@/lib/podcast'
import type { Job } from '@/lib/types'
import { qk } from './keys'
import { upsertJob } from './useJobs'

const POLL_MS = 1500

// a job of the episode's own (write, mix, publish, video) is on its way; line renders are the script's
export const episodeBusy = (ep: Episode | undefined) => !!ep && Object.values(ep.jobs).some((j) => busy(j))

export function useShows() {
  return useQuery({ queryKey: qk.podcastShows, queryFn: () => api.podcast.shows() })
}

export function useEpisodes() {
  return useQuery({ queryKey: qk.podcastEpisodes, queryFn: () => api.podcast.episodes() })
}

export function useEpisode(id: string | undefined) {
  return useQuery({
    queryKey: qk.podcastEpisode(id ?? ''),
    queryFn: () => api.podcast.get(id!),
    enabled: !!id,
    refetchInterval: (q) => (episodeBusy(q.state.data) ? POLL_MS : false),
  })
}

function put(qc: QueryClient, ep: Episode) {
  qc.setQueryData(qk.podcastEpisode(ep.id), ep)
  qc.invalidateQueries({ queryKey: qk.podcastEpisodes })
  // hosts and the medium reach the AI video on the server (its cast, or the record itself)
  qc.invalidateQueries({ queryKey: qk.episodeAiVideo(ep.id) })
  qc.invalidateQueries({ queryKey: ['podcast', 'ai-video'] })
  // the script under it may have changed (hosts → cast, pasted lines, recordings)
  if (ep.script_id) qc.invalidateQueries({ queryKey: qk.script(ep.script_id) })
}

function afterJob(qc: QueryClient, id: string, jobs: Job[]) {
  jobs.forEach((j) => upsertJob(qc, j))
  qc.invalidateQueries({ queryKey: qk.jobs })
  qc.invalidateQueries({ queryKey: qk.podcastEpisode(id) })
}

export function useCreateShow() {
  const qc = useQueryClient()
  return useMutation({ mutationFn: (body: ShowCreate) => api.podcast.createShow(body), onSuccess: () => qc.invalidateQueries({ queryKey: qk.podcastShows }) })
}

export function useUpdateShow() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: ShowPatch }) => api.podcast.updateShow(id, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.podcastShows }),
  })
}

export function useAddAd() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ad }: { id: string; ad: Omit<Ad, 'id'> }) => api.podcast.addAd(id, ad),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.podcastShows }),
  })
}

export function useCreateEpisode() {
  const qc = useQueryClient()
  return useMutation({ mutationFn: (body: EpisodeCreate) => api.podcast.create(body), onSuccess: (ep) => put(qc, ep) })
}

export function useDeleteEpisode() {
  const qc = useQueryClient()
  return useMutation({ mutationFn: (id: string) => api.podcast.remove(id), onSuccess: () => qc.invalidateQueries({ queryKey: qk.podcastEpisodes }) })
}

export function usePatchEpisode(id: string) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: (body: EpisodePatch) => api.podcast.update(id, body), onSuccess: (ep) => put(qc, ep) })
}

export function useAddSource(id: string) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: (file: File) => api.podcast.addSource(id, file), onSuccess: (ep) => put(qc, ep) })
}

export function useRemoveSource(id: string) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: (docId: string) => api.podcast.removeSource(id, docId), onSuccess: (ep) => put(qc, ep) })
}

export function usePasteScript(id: string) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: (text: string) => api.podcast.pasteScript(id, text), onSuccess: (ep) => put(qc, ep) })
}

export function useWriteEpisode(id: string) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: () => api.podcast.write(id), onSuccess: (job) => afterJob(qc, id, [job]) })
}

export function useEditLines(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { op: EditOp; line_ids?: string[]; chapter_id?: string; note?: string }) => api.podcast.editLines(id, body),
    onSuccess: (job) => afterJob(qc, id, [job]),
  })
}

export function useRenderEpisode(id: string, scriptId: string | null) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (lineIds?: string[]) => api.podcast.render(id, lineIds),
    onSuccess: (res) => {
      afterJob(qc, id, res.jobs ?? [])
      if (scriptId) qc.invalidateQueries({ queryKey: qk.script(scriptId) })
    },
  })
}

export function useRecordLine(id: string) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: (body: { line_id: string; media_id: string }) => api.podcast.recordLine(id, body), onSuccess: (ep) => put(qc, ep) })
}

export function useInsert(id: string) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: (body: InsertBody) => api.podcast.insert(id, body), onSuccess: (ep) => put(qc, ep) })
}

export function useMixEpisode(id: string) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: () => api.podcast.mix(id), onSuccess: (job) => afterJob(qc, id, [job]) })
}

export function useNotes(id: string) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: () => api.podcast.notes(id), onSuccess: (job) => afterJob(qc, id, [job]) })
}

export function usePublish(id: string) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: (body: PublishBody) => api.podcast.publish(id, body), onSuccess: (job) => afterJob(qc, id, [job]) })
}

export function useVideoPodcast(id: string) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: (body: VideoBody) => api.podcast.video(id, body), onSuccess: (job) => afterJob(qc, id, [job]) })
}
