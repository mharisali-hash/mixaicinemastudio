import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { RemixRecipe } from '@/lib/remix'
import { feel, fits, steps, DEFAULT_STEER } from '@/lib/remix'
import { audioItem, stubMedia } from '@/test/audio-fixtures'
import { job, mockApi, renderAt } from '@/test/media-fixtures'
import { resetPlayer } from '../sharedAudio'
import { RemixPage } from './RemixPage'

const routes = [{ path: '/audio/remix', element: <RemixPage /> }]
const a = audioItem('a1', { title: 'Harbour song', duration_s: 92 })
const b = audioItem('b1', { title: 'Night market', duration_s: 120 })

const R = (id: string, label: string, extra: Partial<RemixRecipe> = {}): RemixRecipe => ({
  id: id as RemixRecipe['id'],
  label,
  hint: `${label} hint`,
  min_sources: 1,
  max_sources: 1,
  available: true,
  reason: null,
  ...extra,
})
const recipes = [
  R('slowed_reverb', 'Slowed + reverb'),
  R('lofi', 'Lo-fi'),
  R('new_beat', 'New beat'),
  R('new_voice', 'New voice', { available: false, reason: "Singing in another voice isn't set up on this server yet" }),
  R('mashup', 'Mashup', { min_sources: 2, max_sources: 3 }),
  R('medley', 'Medley', { min_sources: 2, max_sources: 3 }),
]

function api() {
  return mockApi((method, path) => {
    if (path === '/jobs' || path === '/models' || path === '/voices') return []
    if (path === '/media') return { items: [a, b] }
    if (method === 'GET' && path === '/media/a1') return { ...a, versions: [] }
    if (method === 'GET' && path === '/media/b1') return { ...b, versions: [] }
    if (path === '/audio/remix/recipes') return { recipes, styles: ['deep house, warm bass', 'trap, 808 bass'], max_sources: 3 }
    if (path === '/audio/remix/analyse/a1') return { item_id: 'a1', bpm: 96.2, key: 'A minor', camelot: '8A', duration_s: 92 }
    if (path === '/audio/remix/analyse/b1') return { item_id: 'b1', bpm: null, key: null, duration_s: 120 }
    if (method === 'GET' && path.startsWith('/audio/remix/')) return { source_id: 'a1', items: [], jobs: [] }
    if (method === 'POST' && path === '/audio/remix') return { items: [audioItem('r1', { title: 'Harbour song — Lo-fi' })], jobs: [job('j1', { type: 'audio_remix_fx' })] }
    return undefined
  })
}

const sent = (calls: { method: string; path: string; body: unknown }[]) => calls.filter((c) => c.method === 'POST' && c.path === '/audio/remix').map((c) => c.body)

beforeEach(() => {
  stubMedia()
  resetPlayer()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('remix helpers', () => {
  it('describes the feel and which recipes fit', () => {
    expect(feel({ bpm: 96.2, key: 'A minor' })).toBe('~96 BPM · A minor')
    expect(feel({ bpm: null, key: null })).toBeNull()
    expect(feel({ bpm: 120, key: 'C major', camelot: '8B' })).toBe('~120 BPM · C major (8B)')
    expect(fits({ min_sources: 2, max_sources: 3 }, 1)).toBe(false)
    expect(fits({ min_sources: 1, max_sources: 1 }, 2)).toBe(false)
    expect(steps({ ...DEFAULT_STEER, picked: ['mashup', 'key_tempo', 'new_beat'], semitones: -2, tempo: 1.1 })).toEqual([
      { recipe: 'mashup', options: { vocals_from: 0, music_from: 1 } },
      { recipe: 'key_tempo', options: { semitones: -2, tempo: 1.1 } },
      { recipe: 'new_beat', options: {} },
    ])
  })
})

describe('Remix', () => {
  it('starts empty, then makes four versions of one song with Auto', async () => {
    const calls = api()
    const user = userEvent.setup()
    renderAt('/audio/remix', routes)
    expect(screen.getByRole('heading', { name: 'Pick a song to remix' })).toBeInTheDocument()
    cleanup()

    renderAt('/audio/remix?songs=a1', routes)
    expect(await screen.findByText('Harbour song')).toBeInTheDocument()
    expect(await screen.findByText(/~96 BPM · A minor \(8A\)/)).toBeInTheDocument()
    await user.click(screen.getAllByRole('button', { name: /Auto remix/ })[0])
    await waitFor(() => expect(sent(calls)).toEqual([{ sources: ['a1'], auto: true }]))
  })

  it('steers: mashups need two songs, unavailable recipes say why', async () => {
    const calls = api()
    const user = userEvent.setup()
    renderAt('/audio/remix?songs=a1', routes)
    await user.click(await screen.findByText('Steer it yourself'))
    const mashup = await screen.findByRole('checkbox', { name: /Mashup/ })
    expect(mashup).toBeDisabled()
    expect(mashup).toHaveAccessibleDescription('Add a second song for this')
    expect(screen.getByRole('checkbox', { name: /New voice/ })).toHaveAccessibleDescription(/set up on this server/)

    await user.click(screen.getByRole('checkbox', { name: /Lo-fi/ }))
    await user.click(screen.getByRole('checkbox', { name: /New beat/ }))
    await user.click(screen.getByRole('button', { name: /deep house/ }))
    await user.click(screen.getByRole('button', { name: 'Make 2 remixes' }))
    await waitFor(() =>
      expect(sent(calls)).toEqual([{ sources: ['a1'], recipes: [{ recipe: 'lofi' }, { recipe: 'new_beat', options: { style: 'deep house, warm bass' } }] }]),
    )
  })

  it('makes a mashup of two songs, vocals from the second', async () => {
    const calls = api()
    const user = userEvent.setup()
    renderAt('/audio/remix?songs=a1,b1', routes)
    const songs = await screen.findByRole('region', { name: /Songs/ })
    expect(within(songs).getAllByRole('listitem')).toHaveLength(2)
    await user.click(await screen.findByText('Steer it yourself'))
    expect(await screen.findByRole('checkbox', { name: /Lo-fi/ })).toBeDisabled()
    await user.click(screen.getByRole('checkbox', { name: /Mashup/ }))
    await user.selectOptions(screen.getByRole('combobox', { name: /Vocals from/ }), 'Song 2')
    await user.selectOptions(screen.getByRole('combobox', { name: /music from/ }), 'Song 1')
    await user.click(screen.getByRole('button', { name: 'Make 1 remix' }))
    await waitFor(() => expect(sent(calls)).toEqual([{ sources: ['a1', 'b1'], recipes: [{ recipe: 'mashup', options: { vocals_from: 1, music_from: 0 } }] }]))

    await user.click(within(songs).getByRole('button', { name: /Remove song 2/ }))
    expect(screen.getByTestId('location')).toHaveTextContent('/audio/remix?songs=a1')
  })
})
