import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { lookBusy, looksApi, type LookIn, type LookPackId } from '@/lib/looks'

const POLL_MS = 3000
const key = (cid: string) => ['character-looks', cid] as const

/** A character's looks, polled while any picture is still being drawn. */
export function useCharacterLooks(cid: string) {
  return useQuery({
    queryKey: key(cid),
    queryFn: () => looksApi.list(cid),
    refetchInterval: (q) => (q.state.data?.looks.some(lookBusy) ? POLL_MS : false),
  })
}

function useLooksMutation<A>(cid: string, fn: (a: A) => Promise<unknown>) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: fn, onSuccess: () => qc.invalidateQueries({ queryKey: key(cid) }) })
}

export const useAddLook = (cid: string) => useLooksMutation(cid, (body: LookIn) => looksApi.add(cid, body))
export const useLookPack = (cid: string) => useLooksMutation(cid, (pack: LookPackId) => looksApi.pack(cid, pack))
export const useSetDefaultLook = (cid: string) => useLooksMutation(cid, (lid: string) => looksApi.update(cid, lid, { is_default: true }))
export const useRemoveLook = (cid: string) => useLooksMutation(cid, (lid: string) => looksApi.remove(cid, lid))
export const useRedrawLook = (cid: string) =>
  useLooksMutation(cid, ({ lid, note }: { lid: string; note?: string }) => looksApi.generate(cid, lid, note ?? ''))
