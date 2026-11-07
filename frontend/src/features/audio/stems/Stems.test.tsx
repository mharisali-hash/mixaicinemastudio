import { act, cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { StemSplit, StemsResponse } from '@/lib/stems'
import { audioItem, stubMedia } from '@/test/audio-fixtures'
import { T, job, media, mockApi, renderAt } from '@/test/media-fixtures'
import { resetPlayer } from '../sharedAudio'
import { StemsPage } from './StemsPage'

const routes = [{ path: '/audio/stems', element: <StemsPage /> }]
const clip = media('v1', { kind: 'video', title: 'Harbour at dusk', duration_s: 6, media_url: '/media/v1.mp4', thumb_url: '/media/v1.webp' })
const song = audioItem('a1', { title: 'Harbour song', duration_s: 92 })

const stem = (id: string, name: string, source = 'Harbour at dusk') =>
  audioItem(id, { title: `${source} — ${name}`, tags: ['stems', 'stems:v1'], media_url: `/media/${id}.flac`, duration_s: 6 })

const twoStems: StemSplit = { id: 'j-old', mode: 'two', created_at: T, stems: ['vocals', 'instrumental'], items: [stem('s1', 'Vocals'), stem('s2', 'Instrumental')] }
const fourStems: StemSplit = {
  id: 'j9',
  mode: 'four',
  created_at: '2026-10-08T10:00:00Z',
  stems: ['vocals', 'drums', 'bass', 'other'],
  items: [stem('s3', 'Vocals'), stem('s4', 'Drums'), stem('s5', 'Bass'), stem('s6', 'Other')],
}

const response = (splits: StemSplit[], jobs = [] as StemsResponse['jobs']): StemsResponse => ({ source_id: 'v1', splits, items: splits.flatMap((s) => s.items), jobs })

interface Opts {
  // GETs answered with the job still running before the split shows up
  runningFor?: number
  refuse?: string
}

function api(opts: Opts = {}) {
  let started = false
  let polls = opts.runningFor ?? 1
  return mockApi((method, path, query) => {
    if (path === '/jobs' || path === '/models') return []
    if (path === '/media') return { items: query.kind === 'video' ? [clip] : [song] }
    if (method === 'GET' && path === '/media/v1') return { ...clip, versions: [] }
    if (method === 'GET' && path === '/media/a1') return { ...song, versions: [] }
    if (method === 'GET' && (path === '/media/gone' || path === '/audio/gone/stems')) return new Response(JSON.stringify({ detail: 'Track not found' }), { status: 404 })
    if (method === 'POST' && path === '/audio/v1/stems') {
      if (opts.refuse) return new Response(JSON.stringify({ detail: opts.refuse }), { status: 422 })
      started = true
      return { job: job('j9', { type: 'audio_stems', lane: 'audio' }), items: [] }
    }
    if (method === 'GET' && path === '/audio/v1/stems') {
      if (started && polls > 0) {
        polls -= 1
        return response([twoStems], [job('j9', { type: 'audio_stems', lane: 'audio', status: 'running', progress: 0.4, message: 'Separating the stems' })])
      }
      return response(started ? [fourStems, twoStems] : [twoStems])
    }
    if (method === 'GET' && path === '/audio/a1/stems') return { source_id: 'a1', splits: [], items: [], jobs: [] }
    return undefined
  })
}

const sent = (calls: { method: string; path: string; body: unknown }[], method: string, path: string) =>
  calls.filter((c) => c.method === method && c.path === path).map((c) => c.body)

beforeEach(() => {
  stubMedia()
  resetPlayer()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('Stems', () => {
  it('splits a picked video into four stems and lists them above the earlier split', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })  // so the 1.5 s job poll can be skipped below
    const calls = api()
    const user = userEvent.setup()
    renderAt('/audio/stems', routes)
    expect(screen.getByRole('heading', { name: 'Pick a track to split' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Pick a video' }))
    await user.click(await screen.findByRole('button', { name: /Harbour at dusk/ }))
    await user.click(screen.getByRole('button', { name: /Split this/ }))
    expect(screen.getByTestId('location')).toHaveTextContent('/audio/stems?source=v1')

    const panel = await screen.findByRole('region', { name: /Harbour at dusk/ })
    expect(within(panel).getByRole('radio', { name: 'Vocals + instrumental' })).toBeChecked()
    expect(within(panel).getByRole('radio', { name: 'Vocals, drums, bass, other' })).toHaveAccessibleDescription(/twice as long/)
    await user.click(within(panel).getByRole('radio', { name: 'Vocals, drums, bass, other' }))
    await user.click(within(panel).getByRole('button', { name: 'Split' }))
    await waitFor(() => expect(sent(calls, 'POST', '/audio/v1/stems')).toEqual([{ mode: 'four' }]))

    expect(await within(panel).findByRole('progressbar', { name: 'Split progress' })).toBeInTheDocument()
    expect(within(panel).getByRole('button', { name: 'Split' })).toBeDisabled()

    await act(() => vi.advanceTimersByTimeAsync(1500))
    const fresh = await screen.findByRole('list', { name: 'Stems, This split' })
    expect(within(fresh).getAllByRole('listitem')).toHaveLength(4)
    expect(within(fresh).getByRole('button', { name: 'Open Harbour at dusk — Drums' })).toBeInTheDocument()
    const earlier = screen.getByRole('region', { name: 'Previous splits of this track' })
    expect(within(earlier).getByRole('heading', { name: /^Two stems · / })).toBeInTheDocument()
    expect(within(earlier).getAllByRole('listitem')).toHaveLength(2)
    expect(within(panel).getByRole('button', { name: 'Split' })).toBeEnabled()
  })

  it('can also write out the lyrics, shown with a Copy button', async () => {
    const withWords = { ...twoStems, lyrics: 'First line we heard\nSecond line we heard' }
    const calls = mockApi((method, path) => {
      if (path === '/jobs' || path === '/models') return []
      if (method === 'GET' && path === '/media/v1') return { ...clip, versions: [] }
      if (method === 'POST' && path === '/audio/v1/stems') return { job: job('j9', { type: 'audio_stems' }), items: [] }
      if (method === 'GET' && path === '/audio/v1/stems') return response([withWords])
    })
    const user = userEvent.setup()
    renderAt('/audio/stems?source=v1', routes)
    await user.click(await screen.findByRole('checkbox', { name: /Also write out the lyrics/ }))
    await user.click(screen.getByRole('button', { name: 'Split' }))
    expect(sent(calls, 'POST', '/audio/v1/stems')[0]).toEqual({ mode: 'two', lyrics: true })
    expect(await screen.findByText(/First line we heard/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Copy' })).toBeInTheDocument()
  })

  it('opens from a tile link with ?source and says when a track was never split', async () => {
    api()
    renderAt('/audio/stems?source=a1', routes)
    expect(await screen.findByRole('region', { name: /Harbour song/ })).toBeInTheDocument()
    expect(await screen.findByText('Not split yet.')).toBeInTheDocument()
  })

  it('every stem tile links back here to split it again', async () => {
    api()
    const user = userEvent.setup()
    renderAt('/audio/stems?source=v1', routes)
    const earlier = await screen.findByRole('list', { name: /^Stems, Two stems/ })
    await user.click(within(earlier).getAllByRole('button', { name: /^More for/ })[0])
    expect(await screen.findByRole('menuitem', { name: 'Split stems' })).toHaveAttribute('href', '/audio/stems?source=s1')
    expect(screen.getByRole('menuitem', { name: 'Open in Editor' })).toHaveAttribute('href', '/audio/edit/s1')
  })

  it('shows why a split could not start, and a track that is gone', async () => {
    api({ refuse: 'This video has no sound to split' })
    const user = userEvent.setup()
    const { unmount } = renderAt('/audio/stems?source=v1', routes)
    const panel = await screen.findByRole('region', { name: /Harbour at dusk/ })
    await user.click(within(panel).getByRole('button', { name: 'Split' }))
    const alert = await within(panel).findByRole('alert')
    expect(alert).toHaveTextContent("Couldn't start the split")
    expect(alert).toHaveTextContent('This video has no sound to split')
    unmount()

    renderAt('/audio/stems?source=gone', routes)
    expect(await screen.findByText("Couldn't open that track")).toBeInTheDocument()
  })
})
