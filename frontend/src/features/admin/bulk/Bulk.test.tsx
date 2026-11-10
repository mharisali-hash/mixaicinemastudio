import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { mockApi, renderAt } from '@/test/media-fixtures'
import type { User } from '@/lib/types'
import type { BulkBatch } from './api'
import { BulkBatchPage } from './BulkBatchPage'
import { BulkPage } from './BulkPage'

afterEach(() => vi.unstubAllGlobals())

const admin: User = { id: 'u1', email: 'admin@mixai.local', display_name: 'Admin', workspace_id: 'w1', role: 'owner', admin: true, verified: true }

const batch = (extra: Partial<BulkBatch> = {}): BulkBatch => ({
  id: 'b1', name: 'Shoes', action: 'edit', options: { instruction: 'white background', magic_prompt: 'off', keywords: '' }, status: 'uploading',
  counts: { total: 4200, uploaded: 4200, queued: 0, running: 0, done: 0, failed: 0, cancelled: 0 }, run_job_id: null, eta_s: null,
  created_at: '2026-11-08T10:00:00Z', updated_at: '2026-11-08T10:00:00Z', ...extra,
})

describe('Admin: Bulk', () => {
  it('lists batches and creates one', async () => {
    const calls = mockApi((method, path) => {
      if (path === '/auth/me') return admin
      if (path === '/admin/bulk' && method === 'GET') return [batch({ status: 'running', counts: { ...batch().counts, done: 1200, uploaded: 3000 } })]
      if (path === '/admin/bulk' && method === 'POST') return batch({ id: 'b2', action: 'cutout' })
    })
    renderAt('/admin/bulk', [{ path: '/admin/bulk', element: <BulkPage /> }, { path: '/admin/bulk/:id', element: <p>batch page</p> }], (qc) => qc.setQueryData(['me'], admin))
    const table = await screen.findByRole('table', { name: 'Batches, 1 shown' })
    const [, row] = within(table).getAllByRole('row')
    expect(within(row).getByText('4,200')).toBeInTheDocument()
    expect(within(row).getByText('1,200')).toBeInTheDocument()
    expect(within(row).getByText('Running')).toBeInTheDocument()

    const user = userEvent.setup()
    await user.type(screen.getByLabelText('New batch'), 'Bags')
    await user.selectOptions(screen.getByLabelText('Action'), 'cutout')
    await user.click(screen.getByRole('button', { name: 'Create batch' }))
    await screen.findByText('batch page')
    expect(calls.find((c) => c.method === 'POST' && c.path === '/admin/bulk')?.body).toEqual({ name: 'Bags', action: 'cutout', options: {} })
  })

  it('Remove by words asks what should go in every picture and saves it', async () => {
    let current = batch({ action: 'remove', options: { keywords: '' } })
    const calls = mockApi((method, path) => {
      if (path === '/auth/me') return admin
      if (path === '/admin/bulk/b1' && method === 'GET') return current
      if (path === '/admin/bulk/b1' && method === 'PATCH') return (current = batch({ action: 'remove', options: { keywords: '', words: 'men' } }))
      if (path === '/admin/bulk/b1/items') return { items: [], next: null }
      if (path === '/admin/bulk/b1/exports') return { exports: [] }
    })
    renderAt('/admin/bulk/b1', [{ path: '/admin/bulk/:id', element: <BulkBatchPage /> }], (qc) => qc.setQueryData(['me'], admin))
    expect(await screen.findByText(/Remove by words · 4,200 pictures/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Instruction for every picture')).not.toBeInTheDocument()
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('What should go in every picture?'), 'men')
    await user.click(screen.getByRole('button', { name: 'Save settings' }))
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ options: { keywords: '', words: 'men' } }))
  })

  it('runs, pauses and says that other requests go first', async () => {
    let current = batch()
    const calls = mockApi((method, path) => {
      if (path === '/auth/me') return admin
      if (path === '/admin/bulk/b1' && method === 'GET') return current
      if (path === '/admin/bulk/b1/run') return (current = batch({ status: 'running', eta_s: 7200, counts: { ...current.counts, uploaded: 4100, queued: 2, done: 98 } }))
      if (path === '/admin/bulk/b1/pause') return (current = batch({ status: 'paused' }))
      if (path === '/admin/bulk/b1/items') return { items: [], next: null }
      if (path === '/admin/bulk/b1/exports') return { exports: [] }
    })
    renderAt('/admin/bulk/b1', [{ path: '/admin/bulk/:id', element: <BulkBatchPage /> }], (qc) => qc.setQueryData(['me'], admin))
    expect(await screen.findByRole('heading', { name: 'Shoes', level: 1 })).toBeInTheDocument()
    expect(screen.getByText(/yours included, goes first/)).toBeInTheDocument()
    expect(screen.getByLabelText('Instruction for every picture')).toHaveValue('white background')

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Run all 4,200' }))
    await screen.findByRole('button', { name: 'Pause' })
    expect(screen.getByText(/98 done · 0 failed · 2 working · 4,100 waiting · about 2.0 h left/)).toBeInTheDocument()
    // settings are locked while it runs
    expect(screen.getByLabelText('Instruction for every picture')).toBeDisabled()

    await user.click(screen.getByRole('button', { name: 'Pause' }))
    await screen.findByRole('button', { name: /Resume/ })
    expect(calls.filter((c) => c.method === 'POST').map((c) => c.path)).toEqual(['/admin/bulk/b1/run', '/admin/bulk/b1/pause'])
  })

  it('opens a picture full size and browses with the arrows', async () => {
    const item = (id: string, filename: string) => ({
      id, seq: 0, filename, status: 'done', error: null, instruction: null, prompt_used: `white background ${id}`, sample: false,
      source_url: `/api/admin/bulk/b1/items/${id}/source`, result_url: `/api/admin/bulk/b1/items/${id}/result`,
    })
    mockApi((method, path) => {
      if (path === '/auth/me') return admin
      if (path === '/admin/bulk/b1' && method === 'GET') return batch({ status: 'done', counts: { ...batch().counts, uploaded: 0, done: 4200 } })
      if (path === '/admin/bulk/b1/items') return { items: [item('i1', 'shoes/a.jpg'), item('i2', 'shoes/b.jpg')], next: null }
      if (path === '/admin/bulk/b1/exports') return { exports: [] }
    })
    renderAt('/admin/bulk/b1', [{ path: '/admin/bulk/:id', element: <BulkBatchPage /> }], (qc) => qc.setQueryData(['me'], admin))
    const grid = await screen.findByRole('list', { name: 'Pictures' })
    const user = userEvent.setup()
    await user.click(within(grid).getByRole('button', { name: 'View shoes/a.jpg full size' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('img', { name: 'shoes/a.jpg, after' })).toHaveAttribute('src', '/api/admin/bulk/b1/items/i1/result?w=0')
    expect(within(dialog).getByText('1 of 2')).toBeInTheDocument()
    await user.keyboard('{ArrowRight}')
    expect(within(dialog).getByRole('img', { name: 'shoes/b.jpg, after' })).toBeInTheDocument()
    expect(within(dialog).getByText('white background i2')).toBeInTheDocument()
  })

  it('asks before deleting a batch', async () => {
    const calls = mockApi((method, path) => {
      if (path === '/auth/me') return admin
      if (path === '/admin/bulk/b1' && method === 'GET') return batch()
      if (path === '/admin/bulk/b1/items') return { items: [], next: null }
      if (path === '/admin/bulk/b1/exports') return { exports: [] }
      if (method === 'DELETE') return new Response(null, { status: 204 })
    })
    renderAt('/admin/bulk/b1', [{ path: '/admin/bulk/:id', element: <BulkBatchPage /> }, { path: '/admin/bulk', element: <p>list</p> }], (qc) => qc.setQueryData(['me'], admin))
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Delete' }))
    const dialog = await screen.findByRole('alertdialog')
    expect(within(dialog).getByText(/4,200 pictures/)).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Delete batch' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE' && c.path === '/admin/bulk/b1')).toBe(true))
    await screen.findByText('list')
  })
})
