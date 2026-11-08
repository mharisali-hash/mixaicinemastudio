import { useId } from 'react'
import { Info } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Kbd } from '@/components/ui/kbd'
import { Tooltip } from '@/components/ui/tooltip'
import { useAuthConfig, useMe } from '@/hooks/useAuth'
import { fmtClock, fmtMinutes, isLow, remainingToday, useUsage } from '@/hooks/useUsage'
import { basisHint, estimateLabel } from '@/lib/estimate'
import { modKey } from '@/lib/keyboard'
import type { EstimateResult } from '@/lib/types'
import { cn } from '@/lib/utils'

interface Props {
  verb: string
  // "4 images", "5 s clip"
  what?: string | null
  estimate: EstimateResult | null
  icon?: React.ReactNode
  // why it can't run yet, in words someone can act on; null when it can
  blocked?: string | null
  // shown in place of the reason when nothing blocks (e.g. "The result is a new image")
  note?: React.ReactNode
  pending?: boolean
  className?: string
}

/** The submit button that says what it will make and roughly how long it takes. */
export function GenerateButton({ verb, what, estimate, icon, blocked: why, note, pending, className }: Props) {
  const uid = useId()
  const { data: me } = useMe()
  const byAdmin = useAuthConfig().data?.verify_by === 'admin'
  // contract v38: the server refuses new work until the email is confirmed, so say it here rather than fail later
  const blocked = me?.verified === false
      ? byAdmin ? 'Creating opens once your account is confirmed.' : 'Confirm your email to start creating. The link is in your inbox.'
      : why
  // contract v39: say so when today's GPU minutes are nearly gone; the server's 429 has the final word
  const { data: usage } = useUsage()
  const left = isLow(usage) ? remainingToday(usage) : null
  const low =
    left == null ? null : left <= 0 ? `No GPU minutes left today. They come back at ${fmtClock(usage?.resets_at)}.` : `${fmtMinutes(left)} GPU min left today`
  return (
    <div className={cn('flex flex-wrap items-center justify-end gap-x-3 gap-y-1.5', className)}>
      <p id={`${uid}-why`} className="mr-auto min-w-0 text-small text-studio-muted" aria-live="polite">
        {blocked ??
          (low ? (
            <>
              {note && (
                <>
                  {note}
                  {' · '}
                </>
              )}
              <span className="text-studio-warning">{low}</span>
            </>
          ) : (
            note
          ))}
      </p>
      {estimate && (
        <Tooltip content={basisHint(estimate)}>
          <button type="button" className="inline-flex items-center gap-1 rounded-[4px] px-1 text-small text-studio-muted hover:text-studio-text" aria-label={`About the estimate: ${basisHint(estimate)}`}>
            <Info aria-hidden className="size-3.5" />
            {estimate.basis === 'measured' ? 'measured' : 'rough'}
          </button>
        </Tooltip>
      )}
      <Button
        type="submit"
        size="lg"
        variant="primary"
        disabled={!!blocked}
        loading={pending}
        aria-describedby={blocked ? `${uid}-why` : undefined}
        aria-keyshortcuts="Control+Enter"
        className="max-sm:w-full"
      >
        {icon}
        {estimateLabel(verb, what ?? null, estimate)}
        <Kbd className="max-md:hidden">{modKey}+Enter</Kbd>
      </Button>
    </div>
  )
}
