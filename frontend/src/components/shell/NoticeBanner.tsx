import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { OctagonAlert, TriangleAlert } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useMe } from '@/hooks/useAuth'
import { qk } from '@/hooks/keys'
import { api } from '@/lib/api'
import type { Notice } from '@/lib/types'

const LEAD: Record<Notice['kind'], string> = {
  warning: 'A warning from a moderator',
  removal: 'A moderator removed one of your items',
  unblock: 'Your account is open again',
}

/** What a moderator told this person (contract v42). A hold stays for as long as it lasts; anything else stays
 * until they say they've read it. Sits under VerifyBanner at the top of the shell. */
export function NoticeBanner() {
  const { data: me } = useMe()
  const qc = useQueryClient()
  const { data: notices } = useQuery({
    queryKey: qk.notices,
    queryFn: api.account.notices,
    enabled: !!me,
    staleTime: 60_000,
    refetchInterval: 5 * 60_000,
  })
  const ack = useMutation({
    mutationFn: (id: string) => api.account.ackNotice(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.notices }),
  })

  if (!me) return null
  const latest = Array.isArray(notices) ? notices[0] : undefined
  return (
    <>
      {me.blocked && (
        <div
          role="region"
          aria-label="Account on hold"
          className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-studio-danger/40 bg-studio-danger/10 px-4 py-2 text-body"
        >
          <OctagonAlert aria-hidden className="size-4 shrink-0 text-studio-danger" />
          <p className="min-w-0 flex-1">
            <span className="font-medium">Your account is on hold: {me.blocked.reason || 'a moderator has paused it'}.</span>{' '}
            <span className="text-studio-muted">You can still look at and download your work, but you can't make anything new for now.</span>
          </p>
          {/* TODO: a "Contact support" link once the owner names the address */}
        </div>
      )}
      {latest && (
        <div
          role="region"
          aria-label="Message from a moderator"
          aria-live="polite"
          className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-studio-warning/30 bg-studio-warning/10 px-4 py-2 text-body"
        >
          <TriangleAlert aria-hidden className="size-4 shrink-0 text-studio-warning" />
          <p className="min-w-0 flex-1">
            <span className="font-medium">{LEAD[latest.kind] ?? LEAD.warning}:</span> <span>{latest.reason}</span>
            {notices && notices.length > 1 && <span className="text-studio-muted"> ({notices.length - 1} more)</span>}
          </p>
          <Button size="sm" variant="secondary" loading={ack.isPending} onClick={() => ack.mutate(latest.id)}>
            Got it
          </Button>
        </div>
      )}
    </>
  )
}
