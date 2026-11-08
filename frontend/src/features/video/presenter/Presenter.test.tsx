import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { media, mockApi, renderAt } from '@/test/media-fixtures'
import type { PresenterVideo } from '@/lib/presenter'
import type { Generation } from '@/lib/types'
import { PresenterListPage } from './PresenterListPage'
import { PresenterPage } from './PresenterPage'

afterEach(() => vi.unstubAllGlobals())

const T = '2026-11-08T10:00:00Z'
const pic = (id: string) => ({ ...media(id), id, status: 'ready', media_url: `/m/${id}.png`, thumb_url: `/m/${id}.webp` }) as unknown as Generation

const routes = [
  { path: '/video/presenter', element: <PresenterListPage /> },
  { path: '/video/presenter/:id', element: <PresenterPage /> },
]

const pv = (extra: Partial<PresenterVideo> = {}): PresenterVideo => ({
  id: 'pv1', title: 'Bread at home', language: 'en', character_id: 'c1', character_name: 'Ayesha', look_id: null,
  settings: { aspect: '16:9', caption_style: 'pill', background: 'studio', lower_name: 'Ayesha', lower_role: '', title_card: true },
  scenes: [
    { id: 's1', text: 'Welcome to our kitchen.', background: '', duration_s: 3.2, speech: null, frame: pic('f1'), take: {} },
    { id: 's2', text: 'First we wake the yeast.', background: '', duration_s: 2.8, speech: null, frame: pic('f2'), take: {} },
  ],
  stages: [
    { key: 'speech', label: 'Voice', status: 'done' },
    { key: 'frames', label: 'Frames', status: 'review', detail: 'Check the frames, then make the video' },
    { key: 'render', label: 'Video', status: 'pending' },
    { key: 'assemble', label: 'Cut', status: 'pending' },
  ],
  outputs: {}, job: null, updated_at: T, ...extra,
})

const sent = (calls: { method: string; path: string; body: unknown }[], method: string, path: string) =>
  calls.filter((c) => c.method === method && c.path === path).map((c) => c.body)

describe('Presenter Video', () => {
  it('starts one from a character, a look and a script', async () => {
    const calls = mockApi((method, path) => {
      if (path === '/presenter' && method === 'GET') return []
      if (path === '/presenter' && method === 'POST') return pv()
      if (path === '/presenter/pv1') return pv()
      if (path.startsWith('/characters')) {
        if (path === '/characters/c1/looks') return { looks: [{ id: 'l1', name: 'Kitchen apron', is_default: true, picture: pic('lp') }], packs: [] }
        return [{ id: 'c1', name: 'Ayesha', voice_id: 'v1', portrait: pic('p1'), project_id: null, description: '', source: 'user', locked: false }]
      }
    })
    renderAt('/video/presenter', routes)
    const user = userEvent.setup()
    expect(await screen.findByText('Pick who presents it.')).toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Presenter'), await screen.findByRole('option', { name: 'Ayesha' }))
    await user.selectOptions(screen.getByLabelText('Look'), await screen.findByRole('option', { name: /Kitchen apron/ }))
    await user.type(screen.getByLabelText('Script'), 'Welcome to our kitchen.{Enter}{Enter}First we wake the yeast.')
    expect(screen.getByText(/2 scenes so far/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Make the video/ }))
    await waitFor(() => expect(sent(calls, 'POST', '/presenter')[0]).toMatchObject({ character_id: 'c1', look_id: 'l1', review: true }))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/video/presenter/pv1'))
  })

  it('reviews frames, saves an edited scene and makes the draft', async () => {
    const calls = mockApi((method, path) => {
      if (path === '/presenter/pv1' && method === 'GET') return pv()
      if (path === '/presenter/pv1' && method === 'PATCH') return pv({ updated_at: '2026-11-08T10:05:00Z' })
      if (path === '/presenter/pv1/render') return { id: 'j1', status: 'queued' }
      if (path.startsWith('/presenter/pv1/scenes/')) return { id: 'j2', status: 'queued' }
    })
    renderAt('/video/presenter/pv1', routes)
    const user = userEvent.setup()
    expect(await screen.findByText(/Check each scene's frame below/)).toBeInTheDocument()
    const scenes = screen.getAllByRole('listitem').filter((li) => within(li).queryByLabelText(/^Scene \d/))
    expect(scenes).toHaveLength(2)
    const box = screen.getByLabelText(/^Scene 2/)
    await user.clear(box)
    await user.type(box, 'First we wake the yeast with sugar.')
    await user.click(screen.getByRole('button', { name: 'Make the draft' }))
    // unsaved edits go in first, then the draft is made
    await waitFor(() => expect(sent(calls, 'POST', '/presenter/pv1/render')).toEqual([{ quality: 'draft' }]))
    expect(sent(calls, 'PATCH', '/presenter/pv1')[0]).toMatchObject({ scenes: [{ id: 's1' }, { id: 's2', text: 'First we wake the yeast with sugar.' }] })
    await user.click(screen.getByRole('button', { name: "Redraw scene 1's frame" }))
    await waitFor(() => expect(calls.some((c) => c.path.startsWith('/presenter/pv1/scenes/s1/redo'))).toBe(true))
  })
})
