import { useEffect } from 'react'
import { useSearchParams } from 'react-router'
import { useMutation } from '@tanstack/react-query'
import { MailWarning } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useAuthConfig, useMe } from '@/hooks/useAuth'
import { api } from '@/lib/api'
import { useToasts } from '@/stores/toasts'

/** Across the top of the shell until the email is confirmed (contract v38); the server refuses new work till then. */
export function VerifyBanner() {
  const { data: me } = useMe()
  const byAdmin = useAuthConfig().data?.verify_by === 'admin'
  const [params, setParams] = useSearchParams()
  const resend = useMutation({ mutationFn: () => api.auth.resendVerification() })

  // the confirm link lands on /?verified=1; say so once and tidy the address bar
  const justVerified = params.get('verified') === '1'
  useEffect(() => {
    if (!justVerified) return
    useToasts.getState().push({ title: 'Email confirmed', detail: 'Your studio is ready. Happy creating.', tone: 'done' })
    const next = new URLSearchParams(params)
    next.delete('verified')
    setParams(next, { replace: true })
  }, [justVerified, params, setParams])

  if (!me || me.verified !== false) return null
  return (
    <div
      role="region"
      aria-label="Confirm your email"
      className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-studio-warning/30 bg-studio-warning/10 px-4 py-2 text-body"
    >
      <MailWarning aria-hidden className="size-4 shrink-0 text-studio-warning" />
      <p className="min-w-0 flex-1">
        <span className="font-medium">{byAdmin ? 'Your account is waiting to be confirmed.' : 'Confirm your email to start creating.'}</span>{' '}
        <span className="text-studio-muted">
          {byAdmin
            ? "We'll confirm it shortly. You can look around until then, but creating stays off."
            : `We sent a link to ${me.email}. You can look around until then, but generating stays off.`}
        </span>
      </p>
      {!byAdmin && (
        <>
      <span aria-live="polite" className="text-small text-studio-muted">
        {resend.isSuccess ? 'Sent. Check your inbox.' : resend.isError ? resend.error.message : ''}
      </span>
      <Button size="sm" variant="secondary" loading={resend.isPending} disabled={resend.isSuccess} onClick={() => resend.mutate()}>
        Send the link again
      </Button>
        </>
      )}
    </div>
  )
}
