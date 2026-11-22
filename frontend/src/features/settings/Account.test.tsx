import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { mockApi, renderAt } from '@/test/media-fixtures'
import { VerifyBanner } from '@/components/shell/VerifyBanner'
import { GenerateButton } from '@/components/generate/GenerateButton'
import { useToasts } from '@/stores/toasts'
import { Toaster } from '@/components/shell/Toaster'
import { wireQuota } from '@/hooks/useUsage'
import { api, onQuota } from '@/lib/api'
import type { Usage, User } from '@/lib/types'
import { AccountPage } from './AccountPage'

afterEach(() => vi.unstubAllGlobals())

const me: User = { id: 'u1', email: 'nia@example.com', display_name: 'Nia', workspace_id: 'w1', role: 'owner', verified: true }

function open(user: User, handler: (method: string, path: string, body: unknown) => unknown = () => undefined, path = '/settings/account') {
  const calls = mockApi((method, p, _q, body) => {
    const hit = handler(method, p, body)
    if (hit !== undefined) return hit
    if (p === '/auth/me') return user
  })
  renderAt(
    path,
    [
      { path: '/settings/account', element: <AccountPage /> },
      { path: '/login', element: <p>sign in page</p> },
      { path: '/', element: <GenerateButton verb="Generate" estimate={null} /> },
    ],
    (qc) => qc.setQueryData(['me'], user),
    <VerifyBanner />,
  )
  return { calls, user: userEvent.setup() }
}

describe('Settings: Account', () => {
  it('saves a new name; the email is read-only', async () => {
    const { calls, user } = open(me, (method, path, body) => {
      if (method === 'PATCH' && path === '/auth/me') return { ...me, ...(body as object) }
    })
    expect(screen.getByLabelText('Email')).toHaveAttribute('readonly')
    expect(screen.getByText('Confirmed')).toBeInTheDocument()
    const save = screen.getByRole('button', { name: 'Save name' })
    expect(save).toBeDisabled()
    await user.clear(screen.getByLabelText('Name'))
    await user.type(screen.getByLabelText('Name'), 'Nia Khan')
    await user.click(save)
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ display_name: 'Nia Khan' }))
    expect(await screen.findByText('Saved.')).toBeInTheDocument()
  })

  it('changes the password once the two new ones match', async () => {
    const { calls, user } = open(me, (method, path) => {
      if (method === 'POST' && path === '/auth/password') return me
    })
    await user.type(screen.getByLabelText('Current password'), 'old-password-1')
    await user.type(screen.getByLabelText('New password'), 'new-password-123')
    await user.type(screen.getByLabelText('Type it again'), 'new-password-124')
    await user.click(screen.getByRole('button', { name: 'Change password' }))
    expect(screen.getByRole('alert')).toHaveTextContent('don’t match')
    await user.clear(screen.getByLabelText('Type it again'))
    await user.type(screen.getByLabelText('Type it again'), 'new-password-123')
    await user.click(screen.getByRole('button', { name: 'Change password' }))
    expect(await screen.findByText(/Other browsers have been signed out/)).toBeInTheDocument()
    expect(calls.find((c) => c.path === '/auth/password')?.body).toEqual({ current_password: 'old-password-1', new_password: 'new-password-123' })
    expect(screen.getByLabelText('Current password')).toHaveValue('')
  })

  it('shows a wrong current password', async () => {
    const { user } = open(me, (method, path) => {
      if (method === 'POST' && path === '/auth/password')
        return new Response(JSON.stringify({ detail: "Your current password isn't right" }), { status: 400 })
    })
    await user.type(screen.getByLabelText('Current password'), 'nope')
    await user.type(screen.getByLabelText('New password'), 'new-password-123')
    await user.type(screen.getByLabelText('Type it again'), 'new-password-123')
    await user.click(screen.getByRole('button', { name: 'Change password' }))
    expect(await screen.findByRole('alert')).toHaveTextContent("current password isn't right")
  })

  it('switches adult content on only after all three ticks, and off in one click', async () => {
    const { calls, user } = open(me, (method, path, body) => {
      if (method === 'PATCH' && path === '/auth/me/adult') return { ...me, adult: (body as { on: boolean }).on }
    })
    const card = screen.getByRole('region', { name: 'Adult content (18+)' })
    await user.click(within(card).getByRole('button', { name: 'Switch on…' }))
    const dialog = await screen.findByRole('dialog', { name: 'Switch on adult content?' })
    const go = within(dialog).getByRole('button', { name: 'Switch on' })
    await user.click(within(dialog).getByLabelText('I am 18 or older'))
    await user.click(within(dialog).getByLabelText('Only made-up people; never real people’s photos'))
    expect(go).toBeDisabled()
    await user.click(within(dialog).getByLabelText('My work may be reviewed, and misuse blocks my account'))
    await user.click(go)
    await waitFor(() =>
      expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ on: true, confirm_18: true, confirm_made_up: true, confirm_review: true }),
    )
    await user.click(await within(card).findByRole('button', { name: 'Switch off' }))
    await waitFor(() => expect(calls.filter((c) => c.method === 'PATCH').at(-1)?.body).toEqual({ on: false }))
  })

  it('says when the team switched adult content off', () => {
    open({ ...me, adult_off: true })
    const card = screen.getByRole('region', { name: 'Adult content (18+)' })
    expect(card).toHaveTextContent('Our team switched this off')
    expect(within(card).queryByRole('button', { name: /Switch on/ })).toBeNull()
  })

  it('signs out everywhere after a confirm', async () => {
    const { calls, user } = open(me, (method, path) => {
      if (method === 'POST' && path === '/auth/logout-everywhere') return new Response(null, { status: 204 })
    })
    await user.click(screen.getByRole('button', { name: 'Sign out everywhere' }))
    const dialog = await screen.findByRole('alertdialog', { name: 'Sign out everywhere?' })
    await user.click(within(dialog).getByRole('button', { name: 'Sign out everywhere' }))
    expect(await screen.findByText('sign in page')).toBeInTheDocument()
    expect(calls.some((c) => c.method === 'POST' && c.path === '/auth/logout-everywhere')).toBe(true)
  })
})

describe('verification banner', () => {
  it('stays hidden for a confirmed user', () => {
    open(me)
    expect(screen.queryByRole('region', { name: 'Confirm your email' })).toBeNull()
  })

  it('explains, resends, and switches the generate buttons off', async () => {
    const { calls, user } = open({ ...me, verified: false }, (method, path) => {
      if (method === 'POST' && path === '/auth/verify/resend') return { detail: 'ok' }
    }, '/')
    const banner = screen.getByRole('region', { name: 'Confirm your email' })
    expect(banner).toHaveTextContent('We sent a link to nia@example.com')
    expect(screen.getByRole('button', { name: /^Generate/ })).toBeDisabled()
    expect(screen.getByText(/Confirm your email to start creating. The link is in your inbox./)).toBeInTheDocument()
    await user.click(within(banner).getByRole('button', { name: 'Send the link again' }))
    expect(await within(banner).findByText('Sent. Check your inbox.')).toBeInTheDocument()
    expect(calls.find((c) => c.path === '/auth/verify/resend')?.body).toEqual({})
  })

  it('celebrates a freshly confirmed email and tidies the address', async () => {
    useToasts.setState({ toasts: [] })
    open(me, () => undefined, '/?verified=1')
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent(/^\/$/))
    expect(useToasts.getState().toasts.map((t) => t.title)).toEqual(['Email confirmed'])
  })
})

const usage = (extra: Partial<Usage> = {}): Usage => ({
  package: { id: 'p-free', name: 'Free', kind: 'standard' }, unlimited: false, gpu_min_day: 30, gpu_min_month: null, max_queued: 2, grant_min: 0,
  used_min_today: 12, used_min_month: 40, queued: 1, resets_at: '2026-10-18T00:00:00Z', month_resets_at: '2026-11-01T00:00:00Z',
  subscription: { id: 's1', status: 'active', starts_at: '2026-10-01T00:00:00Z', ends_at: null, source: 'signup_default' },
  features: {} as Usage['features'], models: null, ...extra,
})

const clock = (iso: string) => new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })

describe('Package and GPU meter (contract v39)', () => {
  afterEach(() => {
    onQuota(null)
    useToasts.setState({ toasts: [] })
  })

  it('shows the package and today\'s minutes on the account page', async () => {
    open(me, (_m, path) => (path === '/account/usage' ? usage() : undefined))
    const card = await screen.findByRole('region', { name: 'Package' })
    expect(await within(card).findByText('Free')).toBeInTheDocument()
    expect(await within(card).findByText(`12 of 30 min today, resets ${clock('2026-10-18T00:00:00Z')}`)).toBeInTheDocument()
    expect(within(card).getByRole('progressbar', { name: /GPU minutes used today/ })).toHaveAttribute('aria-valuenow', '40')
    expect(within(card).getByText('1 of 2 jobs on the way')).toBeInTheDocument()
  })

  it('says how many minutes are left on the Generate button when they run low', async () => {
    mockApi(() => undefined)
    const { qc } = renderAt('/', [{ path: '/', element: <GenerateButton verb="Generate" estimate={null} note="The result is a new image" /> }], (q) => {
      q.setQueryData(['me'], me)
      q.setQueryData(['account-usage'], usage())
    })
    expect(screen.getByText('The result is a new image')).toBeInTheDocument()
    expect(screen.queryByText(/GPU min left today/)).toBeNull()
    act(() => qc.setQueryData(['account-usage'], usage({ used_min_today: 26.5 })))
    expect(await screen.findByText('3.5 GPU min left today')).toBeInTheDocument()
    act(() => qc.setQueryData(['account-usage'], usage({ used_min_today: 31 })))
    expect(await screen.findByText(`No GPU minutes left today. They come back at ${clock('2026-10-18T00:00:00Z')}.`)).toBeInTheDocument()
    // the server has the last word, so the button stays usable
    expect(screen.getByRole('button', { name: /Generate/ })).toBeEnabled()
  })

  it('puts a calm toast up when a request hits the limit', async () => {
    const calls = mockApi((_m, path) => {
      if (path === '/images/generate') {
        return new Response(
          JSON.stringify({ detail: "You've used today's 30 GPU minutes.", reason: 'day', used_min: 30, limit_min: 30, resets_at: '2026-10-18T00:00:00Z', queued: null, max_queued: null }),
          { status: 429, headers: { 'Content-Type': 'application/json' } },
        )
      }
    })
    const { qc } = renderAt('/', [{ path: '/', element: <p>studio</p> }], (q) => q.setQueryData(['me'], me), <Toaster />)
    wireQuota(qc)
    await expect(api.images.generate({ prompt: 'a red kite' } as never)).rejects.toMatchObject({ status: 429 })
    const toast = await screen.findByText("That's today's GPU time")
    expect(toast).toBeInTheDocument()
    expect(screen.getByText(`30 of 30 GPU minutes used. They come back at ${clock('2026-10-18T00:00:00Z')}.`)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'See usage' })).toBeInTheDocument()
    expect(calls.some((c) => c.path === '/images/generate')).toBe(true)
  })
})
