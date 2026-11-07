import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { media, mockApi, renderAt } from '@/test/media-fixtures'
import type { CharacterLook } from '@/lib/looks'
import type { Character, Generation } from '@/lib/types'
import { CharacterPage } from './CharacterPage'

afterEach(() => vi.unstubAllGlobals())

const T = '2026-11-07T10:00:00Z'
const pic = (id: string, status = 'ready') =>
  ({ ...media(id), id, status, media_url: `/m/${id}.png`, thumb_url: `/m/${id}.webp` }) as unknown as Generation

const character = (extra: Partial<Character> = {}): Character => ({
  id: 'c1', project_id: null, name: 'Ayesha', description: 'a teacher', source: 'user', locked: true,
  profile: {}, voice_id: null, studio: true, films: [], portrait: pic('p1'), ...extra,
})

const look = (id: string, name: string, extra: Partial<CharacterLook> = {}): CharacterLook => ({
  id, character_id: 'c1', name, outfit: 'a white kurta', setting: 'a courtyard', pose: 'standing', prompt: '',
  is_default: false, sort: 0, picture: pic(`g-${id}`), created_at: T, ...extra,
})

const PACKS = [{ id: 'traditional', label: 'Traditional', looks: ['Shalwar kameez', 'Sherwani', 'Dupatta', 'Waistcoat'] }]

function page(looks: CharacterLook[], who = character()) {
  const calls = mockApi((method, path) => {
    if (path === '/characters/c1') return method === 'PATCH' ? who : who
    if (path === '/characters/c1/looks' && method === 'GET') return { looks, packs: PACKS }
    if (path === '/characters/c1/looks/pack') return { looks: [look('n1', 'Shalwar kameez')] }
    if (path === '/characters/c1/looks' && method === 'POST') return look('n2', 'Eid morning')
    if (path.startsWith('/characters/c1/looks/')) return method === 'DELETE' ? new Response(null, { status: 204 }) : looks[0]
    if (path === '/voices') return []
    if (path === '/characters/c1/test-clip') return { item: null, text: '' }
  })
  renderAt('/characters/c1', [{ path: '/characters/:characterId', element: <CharacterPage /> }])
  return { calls, user: userEvent.setup() }
}

const sent = (calls: { method: string; path: string; body: unknown }[], method: string, path: string) =>
  calls.filter((c) => c.method === method && c.path === path).map((c) => c.body)

describe('Character Looks', () => {
  it('lists looks with the default marked, adds a pack and makes another the default', async () => {
    const { calls, user } = page([look('l1', 'Office', { is_default: true }), look('l2', 'Garden')])
    await user.click(await screen.findByRole('tab', { name: 'Looks' }))
    const list = await screen.findByRole('list', { name: 'Looks' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(2)
    expect(within(list).getByText('Default')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Traditional' }))
    await waitFor(() => expect(sent(calls, 'POST', '/characters/c1/looks/pack')).toEqual([{ pack: 'traditional' }]))
    await user.click(screen.getByRole('button', { name: 'Make Garden the default look' }))
    await waitFor(() => expect(sent(calls, 'PATCH', '/characters/c1/looks/l2')).toEqual([{ is_default: true }]))
  })

  it('adds your own look and saves how they move on camera', async () => {
    const { calls, user } = page([])
    await user.click(await screen.findByRole('tab', { name: 'Looks' }))
    expect(await screen.findByText('No looks yet')).toBeInTheDocument()
    const form = screen.getByRole('form', { name: 'Add a look' })
    await user.type(within(form).getByLabelText('Name'), 'Eid morning')
    await user.type(within(form).getByLabelText('Outfit'), 'a white kurta')
    await user.click(within(form).getByRole('button', { name: 'Add look' }))
    await waitFor(() => expect(sent(calls, 'POST', '/characters/c1/looks')[0]).toMatchObject({ name: 'Eid morning', outfit: 'a white kurta' }))
    await user.click(screen.getByRole('radio', { name: /Lively/ }))
    await user.type(screen.getByLabelText('Gestures and movement'), 'nods often')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(sent(calls, 'PATCH', '/characters/c1')).toEqual([{ performance: { expressiveness: 'lively', gestures: 'nods often' } }]))
  })

  it('asks for a portrait before any look can be drawn', async () => {
    const { user } = page([], character({ portrait: null }))
    await user.click(await screen.findByRole('tab', { name: 'Looks' }))
    expect(await screen.findByText(/Make Ayesha's portrait first/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Traditional' })).toBeDisabled()
  })
})
