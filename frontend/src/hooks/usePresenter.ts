import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { presenterApi, presenterBusy, type PresenterCreate } from '@/lib/presenter'

const POLL_MS = 2500

export function usePresenters() {
  return useQuery({ queryKey: ['presenters'], queryFn: presenterApi.list })
}

/** One presenter video, polled while its job runs. */
export function usePresenter(id: string) {
  return useQuery({
    queryKey: ['presenter', id],
    queryFn: () => presenterApi.get(id),
    refetchInterval: (q) => (presenterBusy(q.state.data) ? POLL_MS : false),
  })
}

function useRefresh(id?: string) {
  const qc = useQueryClient()
  return () => {
    if (id) qc.invalidateQueries({ queryKey: ['presenter', id] })
    qc.invalidateQueries({ queryKey: ['presenters'] })
  }
}

export function useCreatePresenter() {
  const refresh = useRefresh()
  return useMutation({ mutationFn: (body: PresenterCreate) => presenterApi.create(body), onSuccess: refresh })
}

export function usePresenterActions(id: string) {
  const refresh = useRefresh(id)
  const opts = { onSuccess: refresh }
  return {
    update: useMutation({ mutationFn: (body: Parameters<typeof presenterApi.update>[1]) => presenterApi.update(id, body), ...opts }),
    proceed: useMutation({ mutationFn: () => presenterApi.continue(id), ...opts }),
    render: useMutation({ mutationFn: (q: 'draft' | 'final') => presenterApi.render(id, q), ...opts }),
    redo: useMutation({ mutationFn: ({ sceneId, what }: { sceneId: string; what: 'frame' | 'take' }) => presenterApi.redo(id, sceneId, what), ...opts }),
    remove: useMutation({ mutationFn: () => presenterApi.remove(id), ...opts }),
  }
}
