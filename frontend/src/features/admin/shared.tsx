import { NavLink, Navigate, Outlet } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { Badge } from '@/components/ui/badge'
import { qk } from '@/hooks/keys'
import { useMe } from '@/hooks/useAuth'
import { api } from '@/lib/api'
import type { PaymentStatus, SubscriptionStatus } from '@/lib/types'
import { cn } from '@/lib/utils'

export const when = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : 'Never'

export const day = (iso: string | null | undefined, empty = '—') =>
  iso ? new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) : empty

export const minutes = (n: number | null | undefined) => (n == null ? 'No limit' : `${n.toLocaleString('en-GB', { maximumFractionDigits: 1 })} min`)

export const rupees = (n: number) => `Rs ${n.toLocaleString('en-PK')}`
// a package's price: nothing to pay reads as Free
export const pkr = (n: number) => (n === 0 ? 'Free' : rupees(n))

// a date input's value (YYYY-MM-DD) as the end of that day, in the admin's own time zone
export const endOfDay = (value: string) => (value ? new Date(`${value}T23:59:59`).toISOString() : null)
export const startOfDay = (value: string) => (value ? new Date(`${value}T00:00:00`).toISOString() : null)

export const PERIOD_LABEL: Record<string, string> = { day: 'a day', week: 'a week', month: 'a month', year: 'a year', none: 'one-off' }

const SUB_TONE: Record<SubscriptionStatus, 'success' | 'neutral' | 'warning' | 'danger'> = {
  active: 'success',
  expired: 'neutral',
  cancelled: 'neutral',
  pending_payment: 'warning',
}
const SUB_LABEL: Record<SubscriptionStatus, string> = { active: 'Active', expired: 'Expired', cancelled: 'Cancelled', pending_payment: 'Awaiting payment' }

export function SubBadge({ status }: { status: SubscriptionStatus }) {
  return <Badge tone={SUB_TONE[status]}>{SUB_LABEL[status]}</Badge>
}

const PAY_TONE: Record<PaymentStatus, 'success' | 'neutral' | 'warning' | 'danger'> = { paid: 'success', pending: 'warning', failed: 'danger', refunded: 'neutral' }

export function PayBadge({ status }: { status: PaymentStatus }) {
  return <Badge tone={PAY_TONE[status]}>{status[0].toUpperCase() + status.slice(1)}</Badge>
}

export const tableWrap = 'overflow-x-auto rounded-[8px] border border-studio-border bg-studio-raised'
export const th = 'px-3 py-2 font-medium'
export const td = 'px-3 py-1.5'
export const linkButton =
  'rounded-[4px] text-left text-studio-accent underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-studio-gold/50'

export function usePackages(archived = false) {
  const { data: me } = useMe()
  return useQuery({ queryKey: qk.adminPackages(archived), queryFn: () => api.admin.packages(archived), enabled: !!me?.admin })
}

export function AdminPage({ title, intro, actions, children, id }: { title: string; intro: string; actions?: React.ReactNode; children: React.ReactNode; id: string }) {
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-4 px-4 py-6 md:px-8" aria-labelledby={id} role="region">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 id={id} className="font-display text-title font-semibold">
            {title}
          </h1>
          <p className="text-body text-studio-muted">{intro}</p>
        </div>
        {actions}
      </div>
      {children}
    </div>
  )
}

const SECTIONS = [
  { to: '/admin', label: 'Overview', end: true },
  { to: '/admin/users', label: 'Users' },
  { to: '/admin/packages', label: 'Packages' },
  { to: '/admin/subscriptions', label: 'Subscriptions' },
  { to: '/admin/payments', label: 'Payments' },
  { to: '/admin/usage', label: 'Usage' },
  { to: '/admin/audit', label: 'Audit log' },
  { to: '/admin/print', label: 'Print Studio' },
  { to: '/admin/bulk', label: 'Bulk' },
]

/** The Admin section: its own sub-navigation over each page. Anyone not an admin is sent home. */
export function AdminLayout() {
  const { data: me } = useMe()
  if (me && !me.admin) return <Navigate to="/" replace />
  return (
    <main data-f6-region tabIndex={-1} className="relative h-full overflow-y-auto focus-visible:outline-none" aria-label="Admin">
      <nav aria-label="Admin sections" className="sticky top-0 z-10 border-b border-studio-border bg-studio-panel/95 px-4 md:px-8">
        <ul className="mx-auto flex max-w-6xl gap-1 overflow-x-auto py-1.5">
          {SECTIONS.map((s) => (
            <li key={s.to}>
              <NavLink
                to={s.to}
                end={s.end}
                className={({ isActive }) =>
                  cn(
                    'block whitespace-nowrap rounded-[6px] px-3 py-1.5 text-body hover:bg-studio-panel-hover focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-studio-gold/50',
                    isActive ? 'bg-studio-accent-soft font-medium text-studio-accent-hover' : 'text-studio-muted',
                  )
                }
              >
                {s.label}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
      <Outlet />
    </main>
  )
}
