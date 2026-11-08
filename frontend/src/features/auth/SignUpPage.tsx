import { useState } from 'react'
import { Link, Navigate } from 'react-router'
import { useMutation } from '@tanstack/react-query'
import { MailCheck, UserPlus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useAuthConfig, useMe } from '@/hooks/useAuth'
import { api } from '@/lib/api'
import { AuthLayout, CARD, LINK, MIN_PASSWORD, Notice, StrengthHint } from './AuthLayout'

const backToSignIn = (
  <Link to="/login" className={LINK}>
    Back to sign in
  </Link>
)

export function CheckInbox({ email }: { email: string }) {
  const config = useAuthConfig()
  if (config.data?.verify_by === 'admin') {
    return (
      <div className={CARD}>
        <div className="flex items-start gap-3">
          <MailCheck aria-hidden className="mt-1 size-5 shrink-0 text-studio-accent" />
          <div>
            <h2 className="font-display text-title font-semibold">Thanks for signing up</h2>
            <p className="mt-1 text-body text-studio-muted">
              We'll confirm your account shortly. You can sign in and look around now; creating opens once it's confirmed.
            </p>
          </div>
        </div>
        <Link to="/login" className={LINK}>
          Sign in
        </Link>
      </div>
    )
  }
  return <InboxLink email={email} />
}

function InboxLink({ email }: { email: string }) {
  const resend = useMutation({ mutationFn: () => api.auth.resendVerification(email) })
  return (
    <div className={CARD}>
      <div className="flex items-start gap-3">
        <MailCheck aria-hidden className="mt-1 size-5 shrink-0 text-studio-accent" />
        <div>
          <h2 className="font-display text-title font-semibold">Check your inbox</h2>
          <p className="mt-1 text-body text-studio-muted">
            If <span className="font-medium text-studio-text">{email}</span> can be used, a link to confirm it is on its way.
            Open it within 48 hours to start creating.
          </p>
        </div>
      </div>
      {resend.isSuccess && <Notice tone="ok">Sent again. It can take a minute or two; check the spam folder too.</Notice>}
      {resend.isError && <Notice>{resend.error.message}</Notice>}
      <Button variant="secondary" className="self-start" loading={resend.isPending} onClick={() => resend.mutate()}>
        Send the link again
      </Button>
    </div>
  )
}

export function SignUpPage() {
  const me = useMe()
  const config = useAuthConfig()
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const signup = useMutation({
    mutationFn: () => api.auth.signup({ name: name.trim(), email: email.trim(), password }),
  })

  if (me.data) return <Navigate to="/" replace />

  if (config.data && config.data.signup !== 'open') {
    return (
      <AuthLayout subtitle="Create an account" footer={backToSignIn}>
        <div className={CARD}>
          <p className="text-body">
            {config.data.signup === 'invite'
              ? 'New accounts are by invitation only. Ask the studio’s admin to add you.'
              : 'Sign-up is closed at the moment.'}
          </p>
        </div>
      </AuthLayout>
    )
  }

  if (signup.isSuccess) {
    return (
      <AuthLayout subtitle="Nearly there" footer={backToSignIn}>
        <CheckInbox email={email.trim()} />
      </AuthLayout>
    )
  }

  const short = password.length > 0 && password.length < MIN_PASSWORD
  return (
    <AuthLayout
      subtitle="Create your own studio"
      footer={
        <p className="text-studio-muted">
          Already have an account?{' '}
          <Link to="/login" className={LINK}>
            Sign in
          </Link>
        </p>
      }
    >
      <form
        className={CARD}
        noValidate
        onSubmit={(e) => {
          e.preventDefault()
          signup.mutate()
        }}
      >
        {signup.isError && <Notice>{signup.error.message}</Notice>}
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="name">Name</Label>
          <Input id="name" autoComplete="name" autoFocus required value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="email">Email</Label>
          <Input id="email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="password">Password</Label>
          <Input
            id="password"
            type="password"
            autoComplete="new-password"
            required
            minLength={MIN_PASSWORD}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            aria-describedby="password-hint"
            aria-invalid={short || undefined}
          />
          <StrengthHint password={password} id="password-hint" />
        </div>
        <Button
          type="submit"
          variant="primary"
          size="lg"
          loading={signup.isPending}
          disabled={!name.trim() || !email.trim() || password.length < MIN_PASSWORD}
        >
          <UserPlus aria-hidden />
          Create account
        </Button>
      </form>
    </AuthLayout>
  )
}
