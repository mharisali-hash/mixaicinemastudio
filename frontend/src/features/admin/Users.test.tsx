import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { mockApi, renderAt } from '@/test/media-fixtures'
import type { AdminUser, AdminUserDetail, Package, Usage, User } from '@/lib/types'
import { UsersPage } from './UsersPage'

afterEach(() => vi.unstubAllGlobals())

const admin: User = { id: 'u1', email: 'admin@mixai.local', display_name: 'Admin', workspace_id: 'w1', role: 'owner', admin: true, verified: true }

const row = (id: string, extra: Partial<AdminUser> = {}): AdminUser => ({
  id, email: `${id}@example.com`, display_name: id.toUpperCase(), is_admin: false, is_active: true, verified: true, workspace_id: `w-${id}`,
  created_at: '2026-10-01T10:00:00Z', last_login_at: null, gpu_min_today: 0, gpu_min_month: 0, jobs: 0, tokens: 0, ...extra,
})

function open(me: User, rows: AdminUser[], handler: (method: string, path: string, body: unknown, q: Record<string, string>) => unknown = () => undefined) {
  const calls = mockApi((method, path, q, body) => {
    const hit = handler(method, path, body, q)
    if (hit !== undefined) return hit
    if (path === '/auth/me') return me
    if (path === '/admin/users') return rows
  })
  renderAt('/admin/users', [{ path: '/admin/users', element: <UsersPage /> }, { path: '/', element: <p>home</p> }], (qc) =>
    qc.setQueryData(['me'], me),
  )
  return { calls, user: userEvent.setup() }
}

describe('Admin: Users', () => {
  it('lists users with confirmed state, switches, minutes and last sign-in', async () => {
    open(admin, [row('u1', { email: 'admin@mixai.local', display_name: 'Admin', is_admin: true }), row('bea', { verified: false, gpu_min_today: 12.5, last_login_at: '2026-10-17T09:30:00Z' })])
    const table = await screen.findByRole('table', { name: 'Users, 2 shown' })
    const [, mine, bea] = within(table).getAllByRole('row')
    expect(within(mine).getByText('(you)')).toBeInTheDocument()
    // your own switches are locked so you can't lock yourself out
    expect(within(mine).getByRole('switch', { name: 'Admin active' })).toBeDisabled()
    expect(within(mine).getByRole('switch', { name: 'Admin admin' })).toBeChecked()
    expect(within(bea).getByText('Waiting')).toBeInTheDocument()
    expect(within(bea).getByText('12.5 min')).toBeInTheDocument()
    expect(within(bea).getByText(/17 Oct 2026/)).toBeInTheDocument()
    expect(within(bea).getByRole('switch', { name: 'BEA active' })).toBeEnabled()
  })

  it('switches a user off and searches', async () => {
    const { calls, user } = open(admin, [row('bea')], (method, path, body) => {
      if (method === 'PATCH' && path === '/admin/users/bea') return { ...row('bea'), ...(body as object) }
    })
    await user.click(await screen.findByRole('switch', { name: 'BEA active' }))
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ is_active: false }))

    await user.type(screen.getByLabelText('Search'), 'bea')
    await user.selectOptions(screen.getByLabelText('Show'), 'unverified')
    await waitFor(() => expect(calls.some((c) => c.path === '/admin/users' && c.query.q === 'bea' && c.query.status === 'unverified')).toBe(true))
  })

  it('opens a drawer with details and actions', async () => {
    const { calls, user } = open(admin, [row('bea', { verified: false, jobs: 4, tokens: 1 })], (method, path) => {
      if (method === 'POST' && path === '/admin/users/bea/resend-verification') return { detail: 'A new confirmation link is on its way to bea@example.com' }
    })
    await user.click(await screen.findByRole('button', { name: 'Details for BEA' }))
    const drawer = await screen.findByRole('dialog', { name: 'BEA' })
    expect(within(drawer).getByText('Not confirmed yet')).toBeInTheDocument()
    await user.click(within(drawer).getByRole('button', { name: 'Send the confirm link again' }))
    expect(await within(drawer).findByText(/on its way to bea@example.com/)).toBeInTheDocument()
    expect(calls.some((c) => c.path === '/admin/users/bea/resend-verification')).toBe(true)
  })

  it('adds a user by invite', async () => {
    const { calls, user } = open(admin, [], (method, path, body) => {
      if (method === 'POST' && path === '/admin/users') return row('dee', body as Partial<AdminUser>)
    })
    expect(await screen.findByText('Nobody matches')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Add a user' }))
    const dialog = await screen.findByRole('dialog', { name: 'Add a user' })
    await user.type(within(dialog).getByLabelText('Name'), 'Dee')
    await user.type(within(dialog).getByLabelText('Email'), 'dee@example.com')
    await user.click(within(dialog).getByRole('button', { name: 'Send invite' }))
    await waitFor(() => expect(calls.find((c) => c.method === 'POST' && c.path === '/admin/users')?.body).toEqual({ email: 'dee@example.com', name: 'Dee', admin: false }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('shows the package and changes it, grants minutes, from the drawer', async () => {
    const bea = row('bea', { package_name: 'Free', gpu_min_day_limit: 30, gpu_min_today: 12 })
    const usage: Usage = {
      package: { id: 'p-free', name: 'Free', kind: 'standard' }, unlimited: false, gpu_min_day: 30, gpu_min_month: null, max_queued: 2, grant_min: 0,
      used_min_today: 12, used_min_month: 40, queued: 1, resets_at: '2026-10-18T00:00:00Z', month_resets_at: '2026-11-01T00:00:00Z',
      subscription: null, features: {} as Usage['features'], models: null,
    }
    const detail: AdminUserDetail = {
      user: bea, usage, limits_override: { gpu_min_day: null, gpu_min_month: null, max_queued: null }, subscription: null, subscriptions: [],
      grants: [], payments: [], recent_jobs: [], had_demo: false,
    }
    const pro = { id: 'p-pro', name: 'Pro', price_pkr: 2999, custom_for_user_id: null } as Package
    const { calls, user } = open(admin, [bea], (method, path) => {
      if (method === 'GET' && path === '/admin/users/bea') return detail
      if (path === '/admin/packages') return [{ id: 'p-free', name: 'Free', price_pkr: 0, custom_for_user_id: null }, pro]
      if (method === 'POST' && path === '/admin/users/bea/subscription') return { id: 's1', package_name: 'Pro' }
      if (method === 'POST' && path === '/admin/users/bea/grants') return { id: 'g1' }
    })
    const table = await screen.findByRole('table', { name: 'Users, 1 shown' })
    expect(within(table).getByText('Free')).toBeInTheDocument()
    expect(within(table).getByText('30 min a day')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Details for BEA' }))
    const drawer = await screen.findByRole('dialog', { name: 'BEA' })
    expect(await within(drawer).findByText(/12 of 30 min today/)).toBeInTheDocument()

    await user.click(within(drawer).getByRole('button', { name: 'Change package' }))
    const change = await screen.findByRole('dialog', { name: /Change package/ })
    await user.selectOptions(within(change).getByLabelText('Package'), 'p-pro')
    await user.type(within(change).getByLabelText('Ends'), '2026-12-31')
    await user.click(within(change).getByRole('button', { name: 'Change package' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.path === '/admin/users/bea/subscription')).toBe(true))
    const sent = calls.find((c) => c.path === '/admin/users/bea/subscription')!.body as { package_id: string; ends_at: string; starts_at: string | null }
    expect(sent.package_id).toBe('p-pro')
    expect(sent.starts_at).toBeNull()
    expect(new Date(sent.ends_at).getDate()).toBe(31)

    await user.click(within(drawer).getByRole('button', { name: 'Grant minutes' }))
    const grant = await screen.findByRole('dialog', { name: 'Grant GPU minutes' })
    await user.clear(within(grant).getByLabelText('Minutes a day'))
    await user.type(within(grant).getByLabelText('Minutes a day'), '20')
    await user.type(within(grant).getByLabelText('Reason'), 'Launch week')
    await user.click(within(grant).getByRole('button', { name: 'Grant minutes' }))
    await waitFor(() =>
      expect(calls.find((c) => c.method === 'POST' && c.path === '/admin/users/bea/grants')?.body).toEqual({ gpu_min: 20, reason: 'Launch week', expires_at: null }),
    )
  })

  it('sends anyone who is not an admin home', async () => {
    const { calls } = open({ ...admin, admin: false }, [row('bea')])
    expect(await screen.findByText('home')).toBeInTheDocument()
    expect(calls.some((c) => c.path === '/admin/users')).toBe(false)
  })
})
