import { useQuery, type QueryClient } from '@tanstack/react-query'
import { api, onQuota } from '@/lib/api'
import type { FeatureKey, QuotaRefusal, Usage } from '@/lib/types'
import { useToasts } from '@/stores/toasts'
import { useMe } from './useAuth'
import { qk } from './keys'

// contract v39: the package and today's GPU meter. `watch` refetches it every minute (the shell does);
// everyone else just reads what's there so a page full of Generate buttons doesn't ask twenty times
export function useUsage({ watch = false }: { watch?: boolean } = {}) {
  const { data: me } = useMe()
  return useQuery<Usage | null>({
    queryKey: qk.usage,
    // an older server (or a test's catch-all mock) answers something else; treat that as no meter
    queryFn: async () => {
      const u = await api.account.usage()
      return u && typeof u === 'object' && 'package' in u ? u : null
    },
    enabled: watch && !!me,
    staleTime: 30_000,
    refetchInterval: watch ? 60_000 : false,
  })
}

// the shell keeps the usage fresh; until it has answered (or on an older server) a feature counts as on,
// and the server still refuses what the package switches off
export function useFeature(key: FeatureKey) {
  const { data } = useUsage()
  return data?.features?.[key] !== false
}

// studios nobody had before rights existed (contract v42) stay hidden until the server says they're on;
// everything else counts as on until it answers, as useFeature does
const OFF_UNTIL_TOLD: FeatureKey[] = ['print', 'presenter', 'bulk_edit', 'implant']

/** allows(feature) for hiding what the plan doesn't have; no feature means always shown. */
export function useAllows() {
  const { data } = useUsage()
  const { data: me } = useMe()
  return (key?: FeatureKey) => {
    if (!key || me?.admin) return true
    const said = data?.features?.[key]
    return said === undefined ? !OFF_UNTIL_TOLD.includes(key) : said
  }
}

export const fmtMinutes = (n: number) => n.toLocaleString('en-GB', { maximumFractionDigits: n < 10 ? 1 : 0 })

export const fmtClock = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }) : ''

export const remainingToday = (u: Usage | null | undefined) =>
  !u || u.unlimited || u.gpu_min_day == null ? null : Math.max(0, u.gpu_min_day - u.used_min_today)

// low: under a fifth of the day's minutes, or five minutes, whichever is more
export const isLow = (u: Usage | null | undefined) => {
  const left = remainingToday(u)
  return left != null && u?.gpu_min_day != null && left <= Math.max(5, u.gpu_min_day * 0.2)
}

export function quotaToast(r: QuotaRefusal): { title: string; detail: string } {
  if (r.reason === 'queued') {
    return { title: 'Your queue is full for now', detail: r.detail }
  }
  const back = r.resets_at ? (r.reason === 'day' ? `They come back at ${fmtClock(r.resets_at)}.` : `They come back on ${new Date(r.resets_at).toLocaleDateString('en-GB', { day: 'numeric', month: 'long' })}.`) : ''
  const of = r.limit_min != null ? `${fmtMinutes(r.used_min)} of ${fmtMinutes(r.limit_min)} GPU minutes used` : 'GPU minutes used up'
  return { title: r.reason === 'day' ? "That's today's GPU time" : "That's this month's GPU time", detail: `${of}. ${back}`.trim() }
}

// wired once in main.tsx: any 429 from the package limits shows the same calm note and refreshes the meter
export function wireQuota(qc: QueryClient) {
  onQuota((r) => {
    const t = quotaToast(r)
    useToasts.getState().push({ ...t, tone: 'note', to: '/settings/account', action: 'See usage' })
    void qc.invalidateQueries({ queryKey: qk.usage })
  })
}
