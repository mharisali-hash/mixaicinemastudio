import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { mockApi, renderAt } from '@/test/media-fixtures'
import type { ReviewItem, RightsOverview, User } from '@/lib/types'
import { NoticeBanner } from '@/components/shell/NoticeBanner'
import { FeatureGate } from '@/components/shell/FeatureGate'
import { ModerationLogPage, PeoplePage, ReviewPage } from './ModerationPages'
import { RightsPage } from './RightsPage'
import { AdminLayout } from './shared'

afterEach(() => vi.unstubAllGlobals())

const base: User = { id: 'u2', email: 'mo@example.com', display_name: 'Mo', workspace_id: 'w2', role: 'owner', verified: true }
const manager: User = { ...base, staff: 'manager' }
const admin: User = { ...base, id: 'u1', admin: true, staff: 'admin' }

const item = (id: string, extra: Partial<ReviewItem> = {}): ReviewItem => ({
  id, kind: 'image', origin: 'generated', title: `Kite ${id}`, prompt: 'a red kite', media_type: 'image/png', width: 64, height: 48,
  duration_s: null, created_at: '2026-11-08T10:00:00Z', owner: { id: 'ben', email: 'ben@example.com', name: 'Ben' },
  reviewed_at: null, removed_at: null, removed_reason: '', thumb_url: `/api/moderation/items/${id}/thumb`, file_url: null, ...extra,
})

describe('Moderation', () => {
  it('a manager sees only the moderation pages and is sent there from the admin ones', async () => {
    mockApi((_, path) => (path === '/moderation/items' ? { items: [], next: null } : undefined))
    renderAt(
      '/admin/packages',
      [{ path: '/admin/*', element: <AdminLayout /> }],
      (qc) => qc.setQueryData(['me'], manager),
    )
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/admin/review'))
    const nav = screen.getByRole('navigation', { name: 'Admin sections' })
    expect(within(nav).getAllByRole('link').map((a) => a.textContent)).toEqual(['Review', 'People', 'Moderation log'])
  })

  it('marks items fine in bulk and removes one with a reason that counts as a warning', async () => {
    const calls = mockApi((method, path) => {
      if (path === '/moderation/items') return { items: [item('a'), item('b')], next: null }
      if (path === '/moderation/users') return { warn_limit: 3, users: [] }
      if (method === 'POST') return {}
    })
    renderAt('/admin/review', [{ path: '/admin/review', element: <ReviewPage /> }], (qc) => qc.setQueryData(['me'], manager))
    const user = userEvent.setup()
    await user.click(await screen.findByRole('checkbox', { name: 'Select Kite a' }))
    await user.click(screen.getByRole('button', { name: /Looks fine/ }))
    await waitFor(() => expect(calls.find((c) => c.path === '/moderation/items/reviewed')?.body).toEqual({ ids: ['a'] }))

    await user.click(screen.getByRole('button', { name: 'Remove Kite b' }))
    const dialog = await screen.findByRole('dialog')
    const submit = within(dialog).getByRole('button', { name: 'Remove' })
    expect(submit).toBeDisabled()
    await user.selectOptions(within(dialog).getByLabelText('Reason'), 'Spam or misuse of the studio')
    await user.click(submit)
    await waitFor(() =>
      expect(calls.find((c) => c.path === '/moderation/items/b/remove')?.body).toEqual({ reason: 'Spam or misuse of the studio', warn: true, email: false }),
    )
  })

  it('suggests a hold at the warning limit and needs a reason for it', async () => {
    const calls = mockApi((method, path) => {
      if (path === '/moderation/users')
        return { warn_limit: 3, users: [{ id: 'ben', email: 'ben@example.com', name: 'Ben', is_active: true, warnings: 3, blocked_at: null, blocked_reason: '' }] }
      if (method === 'POST') return {}
    })
    renderAt('/admin/people', [{ path: '/admin/people', element: <PeoplePage /> }], (qc) => qc.setQueryData(['me'], manager))
    const user = userEvent.setup()
    const hold = await screen.findByRole('button', { name: /Put on hold/ })
    expect(hold.className).toMatch(/danger/)
    await user.click(hold)
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('In your words'), 'Third warning ignored')
    await user.click(within(dialog).getByRole('button', { name: 'Put on hold' }))
    await waitFor(() => expect(calls.find((c) => c.path === '/moderation/users/ben/block')?.body).toEqual({ reason: 'Third warning ignored', email: false }))
  })

  it('lists the log in plain words', async () => {
    mockApi((_, path) =>
      path === '/moderation/log'
        ? [{ id: 'l1', at: '2026-11-08T10:00:00Z', actor: 'mo@example.com', action: 'moderation_warning', target_type: 'user', target_id: 'ben', detail: { reason: 'Be careful' } }]
        : undefined,
    )
    renderAt('/admin/moderation-log', [{ path: '/admin/moderation-log', element: <ModerationLogPage /> }], (qc) => qc.setQueryData(['me'], manager))
    expect(await screen.findByText('Sent a warning')).toBeInTheDocument()
    expect(screen.getByText('Be careful')).toBeInTheDocument()
  })
})

describe('What the person sees', () => {
  it('shows a hold that stays, and a warning that goes once read', async () => {
    const calls = mockApi((method, path) => {
      if (path === '/account/notices') return [{ id: 'n1', kind: 'warning', reason: 'Not allowed here', item_id: null, at: '2026-11-08T10:00:00Z' }]
      if (method === 'POST') return {}
    })
    const held: User = { ...base, blocked: { reason: 'Repeated spam', at: '2026-11-08T10:00:00Z' } }
    renderAt('/', [{ path: '/', element: <NoticeBanner /> }], (qc) => qc.setQueryData(['me'], held))
    expect(await screen.findByRole('region', { name: 'Account on hold' })).toHaveTextContent('Repeated spam')
    const warning = await screen.findByRole('region', { name: 'Message from a moderator' })
    expect(warning).toHaveTextContent('Not allowed here')
    await userEvent.setup().click(within(warning).getByRole('button', { name: 'Got it' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.path === '/account/notices/n1/ack')).toBe(true))
  })

  it('a studio the plan lacks shows a note instead of the page', async () => {
    mockApi(() => undefined)
    renderAt('/audio/podcast', [{ path: '/audio/podcast', element: <FeatureGate><p>podcast page</p></FeatureGate> }], (qc) => {
      qc.setQueryData(['me'], base)
      qc.setQueryData(['account-usage'], { package: { id: 'p', name: 'Free', kind: 'standard' }, features: { podcast: false } })
    })
    expect(await screen.findByRole('heading', { name: "This studio isn't part of your plan" })).toBeInTheDocument()
    expect(screen.queryByText('podcast page')).not.toBeInTheDocument()
  })
})

describe('Admin: Rights', () => {
  const overview: RightsOverview = {
    features: [
      { key: 'image', label: 'Image studio', released: true, own_switch: false },
      { key: 'presenter', label: 'Presenter Video', released: false, own_switch: false },
      { key: 'implant', label: 'Implant Preview', released: false, own_switch: true },
    ],
    models: [{ key: 'flux', label: 'FLUX', type: 'image', released: true }],
    packages: [
      { id: 'free', name: 'Free', kind: 'standard', custom: false, features: {}, models: null },
      { id: 'pro', name: 'Pro', kind: 'standard', custom: false, features: {}, models: null },
    ],
  }

  it('releases what is waiting, edits the grid by keyboard and saves a column', async () => {
    const calls = mockApi((method, path) => {
      if (path === '/admin/rights') return overview
      if (method !== 'GET') return {}
    })
    renderAt('/admin/rights', [{ path: '/admin/rights', element: <RightsPage /> }], (qc) => qc.setQueryData(['me'], admin))
    const user = userEvent.setup()
    // implant has its own switch per person, so it's never "waiting"
    const waiting = await screen.findByRole('region', { name: /Waiting for release/ })
    expect(waiting).toHaveTextContent('Presenter Video')
    expect(waiting).not.toHaveTextContent('Implant Preview')
    await user.click(await screen.findByRole('button', { name: 'Release Presenter Video' }))
    await waitFor(() => expect(calls.find((c) => c.path === '/admin/rights/release')?.body).toEqual({ kind: 'feature', key: 'presenter' }))

    const cell = screen.getByRole('checkbox', { name: 'Free: Image studio' })
    cell.focus()
    await user.keyboard('{ArrowRight}')
    expect(screen.getByRole('checkbox', { name: 'Pro: Image studio' })).toHaveFocus()
    await user.keyboard(' ')
    const save = screen.getAllByRole('button', { name: 'Save' })[0]
    await user.click(save)
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH' && c.path === '/admin/packages/pro')?.body).toEqual({ features: { image: false }, models: null }))
    expect(screen.queryByRole('checkbox', { name: 'Free: Implant Preview' })).not.toBeInTheDocument()
  })
})
