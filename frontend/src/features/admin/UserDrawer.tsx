import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CalendarPlus, Gift, PackagePlus, Sparkles } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/alert-dialog'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@/components/ui/sheet'
import { Switch } from '@/components/ui/switch'
import { GpuMeter } from '@/components/studio/gpu-meter'
import { qk } from '@/hooks/keys'
import { api } from '@/lib/api'
import type { AdminUser, AdminUserDetail, AdminUserPatch } from '@/lib/types'
import { Notice } from '@/features/auth/AuthLayout'
import { announce } from '@/stores/ui'
import { AssignPackageDialog } from './AssignPackageDialog'
import { UserRightsSection } from './UserRightsSection'
import { PayBadge, SubBadge, day, endOfDay, minutes, rupees, when } from './shared'

function useAdminAction<T>(fn: (arg: T) => Promise<unknown>, done: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      announce(done)
      void qc.invalidateQueries({ queryKey: ['admin-users'] })
      void qc.invalidateQueries({ queryKey: ['admin-subscriptions'] })
      void qc.invalidateQueries({ queryKey: ['admin-packages'] })
    },
  })
}

const num = (v: string) => (v.trim() === '' ? null : Number(v))

function Field({ id, label, hint, children }: { id: string; label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {hint && (
        <p id={`${id}-hint`} className="text-small text-studio-muted">
          {hint}
        </p>
      )}
    </div>
  )
}

function GrantDialog({ user, open, onOpenChange }: { user: AdminUser; open: boolean; onOpenChange: (o: boolean) => void }) {
  const [min, setMin] = useState('15')
  const [reason, setReason] = useState('')
  const [until, setUntil] = useState('')
  const grant = useAdminAction(
    () => api.admin.grantMinutes(user.id, { gpu_min: Number(min), reason: reason.trim(), expires_at: endOfDay(until) }),
    `${min} minutes granted to ${user.display_name || user.email}`,
  )
  const bad = !(Number(min) > 0)
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Grant GPU minutes</DialogTitle>
          <DialogDescription>Extra minutes on top of their package, every day until the grant ends.</DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-3"
          noValidate
          onSubmit={(e) => {
            e.preventDefault()
            if (!bad) grant.mutate(undefined, { onSuccess: () => onOpenChange(false) })
          }}
        >
          {grant.isError && <Notice>{grant.error.message}</Notice>}
          <Field id="grant-min" label="Minutes a day">
            <Input id="grant-min" type="number" min={1} step={1} inputMode="numeric" value={min} onChange={(e) => setMin(e.target.value)} aria-invalid={bad || undefined} />
          </Field>
          <Field id="grant-until" label="Until" hint="Empty: just for today (until midnight UTC).">
            <Input id="grant-until" type="date" value={until} onChange={(e) => setUntil(e.target.value)} aria-describedby="grant-until-hint" />
          </Field>
          <Field id="grant-reason" label="Reason">
            <Input id="grant-reason" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Sorry about the outage" />
          </Field>
          <DialogFooter>
            <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" loading={grant.isPending} disabled={bad}>
              <Gift aria-hidden />
              Grant minutes
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function CustomDialog({ user, open, onOpenChange }: { user: AdminUser; open: boolean; onOpenChange: (o: boolean) => void }) {
  const [name, setName] = useState(`${user.display_name || user.email.split('@')[0]}'s package`)
  const [perDay, setPerDay] = useState('60')
  const [queued, setQueued] = useState('3')
  const [price, setPrice] = useState('0')
  const [end, setEnd] = useState('')
  const make = useAdminAction(
    () =>
      api.admin.customPackage(user.id, {
        name: name.trim(),
        gpu_min_day: num(perDay),
        max_queued: num(queued),
        price_pkr: Number(price) || 0,
        period: 'none',
        ends_at: endOfDay(end),
      }),
    `Custom package made for ${user.display_name || user.email}`,
  )
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Custom package</DialogTitle>
          <DialogDescription>Made for this person only, and put on them straight away.</DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-3"
          noValidate
          onSubmit={(e) => {
            e.preventDefault()
            if (name.trim()) make.mutate(undefined, { onSuccess: () => onOpenChange(false) })
          }}
        >
          {make.isError && <Notice>{make.error.message}</Notice>}
          <Field id="custom-name" label="Name">
            <Input id="custom-name" value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <div className="grid gap-3 sm:grid-cols-3">
            <Field id="custom-day" label="GPU min a day">
              <Input id="custom-day" type="number" min={0} value={perDay} onChange={(e) => setPerDay(e.target.value)} placeholder="No limit" />
            </Field>
            <Field id="custom-queued" label="Jobs at once">
              <Input id="custom-queued" type="number" min={0} value={queued} onChange={(e) => setQueued(e.target.value)} placeholder="No limit" />
            </Field>
            <Field id="custom-price" label="Price (PKR)">
              <Input id="custom-price" type="number" min={0} value={price} onChange={(e) => setPrice(e.target.value)} />
            </Field>
          </div>
          <Field id="custom-end" label="Ends" hint="Empty: until you change it.">
            <Input id="custom-end" type="date" value={end} onChange={(e) => setEnd(e.target.value)} aria-describedby="custom-end-hint" />
          </Field>
          <DialogFooter>
            <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" loading={make.isPending} disabled={!name.trim()}>
              Make and assign
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function LimitsForm({ user, detail }: { user: AdminUser; detail: AdminUserDetail }) {
  const o = detail.limits_override
  const str = (v: number | null) => (v == null ? '' : String(v))
  const [perDay, setPerDay] = useState(str(o.gpu_min_day))
  const [perMonth, setPerMonth] = useState(str(o.gpu_min_month))
  const [queued, setQueued] = useState(str(o.max_queued))
  const save = useAdminAction(
    (body: AdminUserPatch) => api.admin.patchUser(user.id, body),
    `Limits saved for ${user.display_name || user.email}`,
  )
  return (
    <form
      className="flex flex-col gap-2"
      noValidate
      onSubmit={(e) => {
        e.preventDefault()
        save.mutate({ limit_gpu_min_day: num(perDay), limit_gpu_min_month: num(perMonth), limit_max_queued: num(queued) })
      }}
    >
      <p className="text-small text-studio-muted">Leave a box empty to use the package’s own limit.</p>
      <div className="grid grid-cols-3 gap-2">
        <Field id="ov-day" label="Min a day">
          <Input id="ov-day" type="number" min={0} value={perDay} onChange={(e) => setPerDay(e.target.value)} />
        </Field>
        <Field id="ov-month" label="Min a month">
          <Input id="ov-month" type="number" min={0} value={perMonth} onChange={(e) => setPerMonth(e.target.value)} />
        </Field>
        <Field id="ov-queued" label="Jobs at once">
          <Input id="ov-queued" type="number" min={0} value={queued} onChange={(e) => setQueued(e.target.value)} />
        </Field>
      </div>
      {save.isError && <Notice>{save.error.message}</Notice>}
      <Button type="submit" variant="secondary" size="sm" className="self-start" loading={save.isPending}>
        Save limits
      </Button>
    </form>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-2 border-t border-studio-border pt-3" aria-label={title}>
      <h3 className="font-display text-panel font-semibold">{title}</h3>
      {children}
    </section>
  )
}

function Billing({ user, detail }: { user: AdminUser; detail: AdminUserDetail }) {
  const [dialog, setDialog] = useState<'assign' | 'grant' | 'custom' | null>(null)
  const [cancelling, setCancelling] = useState(false)
  const [days, setDays] = useState('7')
  const sub = detail.subscription
  const demo = useAdminAction((repeat: boolean) => api.admin.startDemo(user.id, { allow_repeat: repeat }), `Demo started for ${user.display_name || user.email}`)
  const extend = useAdminAction((id: string) => api.admin.extendSubscription(id, { days: Number(days) }), 'Package extended')
  const cancel = useAdminAction((id: string) => api.admin.cancelSubscription(id), 'Package cancelled')
  const revoke = useAdminAction((id: string) => api.admin.revokeGrant(id), 'Grant withdrawn')
  const failed = demo.error ?? extend.error ?? cancel.error ?? revoke.error

  if (user.is_admin) {
    return (
      <Section title="Package">
        <p className="text-body">Admins have no limits.</p>
        <GpuMeter usage={detail.usage} />
      </Section>
    )
  }
  return (
    <>
      <Section title="Package">
        <p className="flex flex-wrap items-center gap-2 text-body">
          <span className="font-medium">{detail.usage.package.name}</span>
          {sub ? <SubBadge status={sub.status} /> : <Badge>Fallback</Badge>}
          {sub?.ends_at && <span className="text-studio-muted">ends {day(sub.ends_at)}</span>}
        </p>
        <GpuMeter usage={detail.usage} />
        <p className="text-small text-studio-muted">
          This month {minutes(detail.usage.used_min_month)} · {detail.usage.queued} of {detail.usage.max_queued ?? '∞'} jobs on the way
        </p>
        {failed && <Notice>{failed.message}</Notice>}
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="secondary" onClick={() => setDialog('assign')}>
            <PackagePlus aria-hidden />
            Change package
          </Button>
          <Button size="sm" variant="secondary" loading={demo.isPending} onClick={() => demo.mutate(detail.had_demo)}>
            <Sparkles aria-hidden />
            {detail.had_demo ? 'Start another demo' : 'Start a demo'}
          </Button>
          <Button size="sm" variant="secondary" onClick={() => setDialog('grant')}>
            <Gift aria-hidden />
            Grant minutes
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setDialog('custom')}>
            Custom package
          </Button>
        </div>
        {sub && sub.source !== 'signup_default' && (
          <div className="flex flex-wrap items-end gap-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="extend-days">Extend by (days)</Label>
              <Input id="extend-days" type="number" min={1} className="w-24" value={days} onChange={(e) => setDays(e.target.value)} />
            </div>
            <Button size="sm" variant="secondary" disabled={!(Number(days) > 0)} loading={extend.isPending} onClick={() => extend.mutate(sub.id)}>
              <CalendarPlus aria-hidden />
              Extend
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setCancelling(true)}>
              Cancel package
            </Button>
          </div>
        )}
      </Section>

      <Section title="Own limits">
        <LimitsForm key={detail.user.id} user={user} detail={detail} />
      </Section>

      <Section title="Grants">
        {detail.grants.length === 0 ? (
          <p className="text-body text-studio-muted">None yet.</p>
        ) : (
          <ul className="flex flex-col gap-1.5 text-body">
            {detail.grants.map((g) => (
              <li key={g.id} className="flex flex-wrap items-center gap-2">
                <span className="tabular-nums">+{minutes(g.gpu_min)} a day</span>
                <span className="text-studio-muted">until {when(g.expires_at)}</span>
                {g.reason && <span className="text-studio-muted">· {g.reason}</span>}
                {g.live ? (
                  <Button size="sm" variant="ghost" className="ml-auto" onClick={() => revoke.mutate(g.id)} aria-label={`Withdraw the grant of ${g.gpu_min} minutes`}>
                    Withdraw
                  </Button>
                ) : (
                  <Badge className="ml-auto">{g.revoked_at ? 'Withdrawn' : 'Ended'}</Badge>
                )}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Payments">
        {detail.payments.length === 0 ? (
          <p className="text-body text-studio-muted">None yet.</p>
        ) : (
          <ul className="flex flex-col gap-1 text-body">
            {detail.payments.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center gap-2">
                <span className="tabular-nums">{rupees(p.amount_pkr)}</span>
                <PayBadge status={p.status} />
                <span className="text-studio-muted">
                  {p.package_name || 'No package'} · {day(p.created_at)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <AssignPackageDialog user={user} open={dialog === 'assign'} onOpenChange={(o) => setDialog(o ? 'assign' : null)} />
      <GrantDialog user={user} open={dialog === 'grant'} onOpenChange={(o) => setDialog(o ? 'grant' : null)} />
      <CustomDialog user={user} open={dialog === 'custom'} onOpenChange={(o) => setDialog(o ? 'custom' : null)} />
      {sub && (
        <ConfirmDialog
          open={cancelling}
          onOpenChange={setCancelling}
          title="Cancel this package?"
          description={`${user.display_name || user.email} goes back to Free straight away. Jobs already running finish.`}
          confirmLabel="Cancel package"
          tone="danger"
          onConfirm={() => cancel.mutate(sub.id)}
        />
      )}
    </>
  )
}

function RecentJobs({ detail }: { detail: AdminUserDetail }) {
  if (detail.recent_jobs.length === 0) return <p className="text-body text-studio-muted">No jobs yet.</p>
  return (
    <ul className="flex flex-col gap-1 text-small">
      {detail.recent_jobs.slice(0, 10).map((j) => (
        <li key={j.id} className="flex flex-wrap gap-x-2">
          <span className="font-medium">{j.type}</span>
          <span className="text-studio-muted">{j.status}</span>
          <span className="text-studio-muted">{when(j.created_at)}</span>
          {j.gpu_min > 0 && <span className="tabular-nums text-studio-muted">{minutes(j.gpu_min)}</span>}
        </li>
      ))}
    </ul>
  )
}

export function UserDrawer({ user, self, onClose, onPatch }: { user: AdminUser | null; self: boolean; onClose: () => void; onPatch: (body: AdminUserPatch) => void }) {
  const reset = useMutation({ mutationFn: (id: string) => api.admin.sendReset(id) })
  const resend = useMutation({ mutationFn: (id: string) => api.admin.resendVerification(id) })
  const detail = useQuery({
    queryKey: qk.adminUser(user?.id ?? ''),
    queryFn: async () => {
      const d = await api.admin.user(user!.id)
      return d && typeof d === 'object' && 'user' in d ? d : null
    },
    enabled: !!user,
  })
  const done = reset.data?.detail ?? resend.data?.detail
  const failed = reset.error ?? resend.error
  return (
    <Sheet
      open={!!user}
      onOpenChange={(o) => {
        if (!o) {
          reset.reset()
          resend.reset()
          onClose()
        }
      }}
    >
      <SheetContent aria-describedby="user-drawer-desc" className="w-[520px]">
        {user && (
          <div className="flex h-full flex-col gap-4 overflow-y-auto p-5">
            <div className="pr-8">
              <SheetTitle className="font-display text-title font-semibold">{user.display_name || user.email}</SheetTitle>
              <SheetDescription id="user-drawer-desc" className="break-all text-body text-studio-muted">
                {user.email}
              </SheetDescription>
            </div>
            <dl className="grid grid-cols-[130px_1fr] gap-x-3 gap-y-1.5 text-body">
              <dt className="text-studio-muted">Joined</dt>
              <dd>{when(user.created_at)}</dd>
              <dt className="text-studio-muted">Last sign-in</dt>
              <dd>{when(user.last_login_at)}</dd>
              <dt className="text-studio-muted">GPU today</dt>
              <dd>{minutes(user.gpu_min_today)}</dd>
              <dt className="text-studio-muted">GPU this month</dt>
              <dd>{minutes(user.gpu_min_month)}</dd>
              <dt className="text-studio-muted">Jobs</dt>
              <dd>{user.jobs}</dd>
              <dt className="text-studio-muted">Live API tokens</dt>
              <dd>{user.tokens}</dd>
              <dt className="text-studio-muted">Email</dt>
              <dd>{user.verified ? 'Confirmed' : 'Not confirmed yet'}</dd>
            </dl>
            {done && <Notice tone="ok">{done}</Notice>}
            {failed && <Notice>{failed.message}</Notice>}
            <div className="flex flex-col gap-2">
              {!user.verified && (
                <Button variant="secondary" className="self-start" loading={resend.isPending} onClick={() => resend.mutate(user.id)}>
                  Send the confirm link again
                </Button>
              )}
              {!user.verified && (
                <Button variant="secondary" className="self-start" onClick={() => onPatch({ verified: true })}>
                  Mark the email as confirmed
                </Button>
              )}
              <Button variant="secondary" className="self-start" disabled={!user.is_active} loading={reset.isPending} onClick={() => reset.mutate(user.id)}>
                Email a password reset link
              </Button>
            </div>
            <label className="flex items-center gap-3 text-body">
              <Switch checked={!!user.implant_access} onCheckedChange={(v) => onPatch({ implant_access: v })} />
              Implant Preview access
            </label>
            {self && <p className="text-small text-studio-muted">This is you, so it can’t be switched off or lose admin here.</p>}
            {detail.isError ? (
              <Notice>{detail.error.message}</Notice>
            ) : detail.data ? (
              <>
                <Billing user={user} detail={detail.data} />
                <Section title="Rights">
                  <UserRightsSection user={user} />
                </Section>
                <Section title="Recent jobs">
                  <RecentJobs detail={detail.data} />
                </Section>
              </>
            ) : detail.isPending ? (
              <p role="status" className="text-body text-studio-muted">
                Loading their package…
              </p>
            ) : null}
          </div>
        )}
      </SheetContent>
    </Sheet>
  )
}
