import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { blockingStep, initialStep, nudge, stepStates, tap, timingProblem, type PoetryShort, type PoetryStageKey, type PoetryStageStatus } from '@/lib/poetry'
import type { Generation } from '@/lib/types'
import { T, job, mockApi, renderAt } from '@/test/media-fixtures'
import { PoetryListPage } from './PoetryListPage'
import { PoetryShortPage } from './PoetryShortPage'

const routes = [
  { path: '/video/poetry', element: <PoetryListPage /> },
  { path: '/video/poetry/:id', element: <PoetryShortPage /> },
]

const gen = (id: string, extra: Partial<Generation> = {}): Generation => ({
  id,
  target_type: 'media',
  target_id: 't',
  kind: 'image',
  version: 1,
  status: 'ready',
  prompt: '',
  params: {},
  seed: 1,
  media_url: `/m/${id}`,
  created_at: T,
  ...extra,
})

const LINES = ['جَب مَیں بِکھَرنے لَگا', 'خاک ہَوا میں اُڑی', 'شام کی لَو جَلی', 'رات نے بات سُنی']

function short(stages: Partial<Record<PoetryStageKey, PoetryStageStatus>>, extra: Partial<PoetryShort> = {}): PoetryShort {
  const keys: PoetryStageKey[] = ['poem', 'song', 'timing', 'scenes', 'render']
  const review = keys.find((k) => stages[k] === 'review') ?? null
  return {
    id: 'p1',
    title: 'بِکھَرنا',
    language: 'ur',
    stage: review ?? 'poem',
    status: review ? 'review' : 'idle',
    review,
    stages: keys.map((k) => ({ key: k, label: k[0].toUpperCase() + k.slice(1), status: stages[k] ?? 'pending', detail: null })),
    settings: {
      topic: 'dust',
      mood: '',
      style: '',
      vocal: 'female',
      length_s: 30,
      character_id: null,
      character: '',
      setting: '',
      aspect: '9:16',
      quality: 'draft',
      image_model: 'krea2_turbo',
      title_card: false,
      review: true,
      whisper: true,
    },
    poem: { lyrics: `[verse]\n${LINES.join('\n')}`, tags: 'slow ghazal', own: false },
    lines: LINES.map((text, i) => ({ i, text, section: i < 2 ? 'verse' : 'chorus', part: i < 2 ? 'verse:1' : 'chorus:1', start: i * 5 + 1, end: i * 5 + 4 })),
    song: { media_id: 'm1', duration_s: 30, generation: gen('song', { kind: 'song', media_url: '/m/song.flac' }) },
    timing: { source: 'phrases', edited: false, phrases: [] },
    scenes: [],
    outputs: null,
    job: null,
    created_at: T,
    updated_at: T,
    ...extra,
  }
}

const SCENES = [0, 1].map((k) => ({
  id: `s${k + 1}`,
  n: k + 1,
  first: k * 2,
  last: k * 2 + 1,
  start: k * 15,
  end: k * 15 + 15,
  image_prompt: `A woman seen from behind, scene ${k + 1}`,
  motion_prompt: 'slow drift',
  status: 'done' as const,
  error: null,
  keyframe: gen(`kf${k + 1}`),
  clip: gen(`c${k + 1}`, { kind: 'video', media_url: `/m/c${k + 1}.mp4` }),
}))

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('timing helpers', () => {
  const rows = [
    { start: 1, end: 4 },
    { start: 5, end: 8 },
    { start: 9, end: 12 },
  ]
  it('nudges inside the neighbours', () => {
    expect(nudge(rows, 1, 'start', -0.1, 30)[1].start).toBe(4.9)
    // never back over the line before
    expect(nudge([{ start: 1, end: 4 }, { start: 4.05, end: 8 }], 1, 'start', -0.1, 30)[1].start).toBe(4)
    expect(nudge(rows, 2, 'end', 100, 30)[2].end).toBe(30)
  })
  it('tap to time starts a line and ends the one before', () => {
    const out = tap(rows, 1, 6.5)
    expect(out[0].end).toBe(6.5)
    expect(out[1].start).toBe(6.5)
    // a late tap pushes the next line along instead of overlapping it
    const late = tap(rows, 1, 10)
    expect(late[2].start).toBeGreaterThanOrEqual(late[1].end)
    expect(timingProblem(late, 30)).toBeNull()
  })
  it('says what is wrong in the server’s words', () => {
    expect(timingProblem([{ start: 2, end: 1 }], 30)).toBe('Line 1 ends before it starts')
    expect(timingProblem([{ start: 0, end: 3 }, { start: 2, end: 4 }], 30)).toBe('Line 2 starts before line 1 ends')
    expect(timingProblem([{ start: 0, end: 40 }], 30)).toMatch(/after the song/)
  })
  it('steps open on the stage waiting for review', () => {
    const ps = short({ poem: 'done', song: 'done', timing: 'review' })
    expect(initialStep(ps)).toBe(3)
    expect(stepStates(ps)).toMatchObject({ poem: 'done', timing: 'review', scenes: 'todo' })
    expect(blockingStep(ps, 3)?.step.id).toBe('timing')
  })
})

describe('Poetry Short page', () => {
  it('shows the poem for review, right to left, and approves it', async () => {
    const ps = short({ poem: 'review' })
    const calls = mockApi((method, path) => {
      if (method === 'POST' && path === '/poetry/p1/continue') return job('j1', { type: 'poetry_short' })
      if (path === '/poetry/p1') return ps
      return []
    })
    renderAt('/video/poetry/p1', routes)
    const poem = await screen.findByRole('textbox', { name: 'The poem' })
    expect(poem).toHaveAttribute('dir', 'auto')
    expect(poem).toHaveAttribute('lang', 'ur')
    expect(screen.getByRole('tab', { name: /Poem, needs your OK/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('button', { name: /Add aerab/ })).toBeInTheDocument()
    // no licence talk anywhere
    expect(document.body.textContent).not.toMatch(/licen[cs]e|commercial/i)
    await userEvent.click(screen.getByRole('button', { name: 'Approve and continue to Song' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.path === '/poetry/p1/continue')).toBe(true))
    expect(await screen.findByRole('tab', { name: /Song/, selected: true })).toBeInTheDocument()
  })

  it('nudges a line and saves the timings', async () => {
    const ps = short({ poem: 'done', song: 'done', timing: 'review' })
    const calls = mockApi((method, path) => {
      if (method === 'PATCH' && path === '/poetry/p1/timings') return ps
      if (path === '/poetry/p1') return ps
      return []
    })
    renderAt('/video/poetry/p1', routes)
    const save = await screen.findByRole('button', { name: 'Save timings' })
    expect(save).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Line 2 start 0.1 s later' }))
    expect(within(screen.getByRole('group', { name: 'Line 2 start' })).getByText('0:06.1')).toBeInTheDocument()
    await userEvent.click(save)
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')).toBeTruthy())
    const body = calls.find((c) => c.method === 'PATCH')!.body as { lines: { start: number; end: number }[] }
    expect(body.lines).toHaveLength(4)
    expect(body.lines[1]).toEqual({ start: 6.1, end: 9 })
  })

  it('makes one scene again with an edited prompt', async () => {
    const ps = short({ poem: 'done', song: 'done', timing: 'done', scenes: 'review' }, { scenes: SCENES })
    const calls = mockApi((method, path) => {
      if (method === 'POST' && path.endsWith('/regenerate')) return ps
      if (path === '/poetry/p1') return ps
      return []
    })
    renderAt('/video/poetry/p1', routes)
    expect(await screen.findByText('2 of 2 scenes ready')).toBeInTheDocument()
    const card = screen.getByRole('listitem', { name: /Scene 2/ })
    const prompt = within(card).getByLabelText('Picture prompt')
    await userEvent.clear(prompt)
    await userEvent.type(prompt, 'A lantern in the rain')
    await userEvent.click(within(card).getByRole('button', { name: 'New picture' }))
    await waitFor(() => expect(calls.find((c) => c.path === '/poetry/p1/scenes/s2/regenerate')).toBeTruthy())
    expect(calls.find((c) => c.path === '/poetry/p1/scenes/s2/regenerate')!.body).toEqual({ what: 'keyframe', image_prompt: 'A lantern in the rain' })
  })

  it('plays and downloads the finished Short', async () => {
    const done = { poem: 'done', song: 'done', timing: 'done', scenes: 'done', render: 'done' } as const
    const ps = short(done, {
      stage: 'done',
      status: 'done',
      scenes: SCENES,
      outputs: {
        media_id: 'v1',
        generation_id: 'gv',
        duration_s: 30,
        width: 1080,
        height: 1920,
        chapters: [{ title: 'Verse 1', start: 0, end: 12 }],
        video_url: '/api/media/v.mp4',
        preview_url: '/api/media/v_preview.mp4',
        srt_url: '/api/media/v.srt',
        stale: false,
      },
    })
    mockApi((_m, path) => (path === '/poetry/p1' ? ps : []))
    renderAt('/video/poetry/p1', routes)
    expect(await screen.findByRole('link', { name: 'Download the video' })).toHaveAttribute('href', '/api/media/v.mp4')
    expect(screen.getByRole('link', { name: 'Subtitles (.srt)' })).toHaveAttribute('href', '/api/media/v.srt')
    expect(screen.getByLabelText(/preview/)).toHaveAttribute('src', '/api/media/v_preview.mp4')
    expect(screen.getByRole('button', { name: 'Cut it again' })).toBeEnabled()
  })
})

describe('new Poetry Short: a voice of mine, up to 4 minutes', () => {
  it('needs the voice picked, then sends it with the longer length', async () => {
    const calls = mockApi((method, path) => {
      if (path === '/audio/song-voices') return { available: true, vocals: ['boy', 'girl', 'choir', 'voice'] }
      if (path === '/voices') return [{ id: 'v9', name: 'Hamza', kind: 'design', language: 'ur' }]
      if (path === '/gallery') return { items: [], total: 0 }
      if (method === 'POST' && path === '/poetry') return short({ poem: 'running' })
      if (path === '/poetry/p1') return short({ poem: 'running' })
      return []
    })
    renderAt('/video/poetry', routes)
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('What is the poem about?'), 'the moon over the river')
    await user.selectOptions(screen.getByLabelText('Voice'), await screen.findByRole('option', { name: 'A voice of mine' }))
    expect(screen.getByText('Pick the voice to sing in.')).toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Sung in the voice of'), await screen.findByRole('option', { name: 'Hamza' }))
    const len = screen.getByLabelText(/^Length/)
    fireEvent.change(len, { target: { value: '240' } })
    expect(screen.getByText('Length: 4 min')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Make the Short/ }))
    await waitFor(() => expect(calls.find((c) => c.method === 'POST' && c.path === '/poetry')).toBeTruthy())
    expect(calls.find((c) => c.method === 'POST' && c.path === '/poetry')!.body).toMatchObject({ vocal: 'voice', sing_voice_id: 'v9', length_s: 240 })
  })
})

describe('new Poetry Short', () => {
  it('needs a topic, then starts one', async () => {
    const calls = mockApi((method, path) => {
      if (method === 'POST' && path === '/poetry') return short({ poem: 'running' })
      if (path === '/poetry/p1') return short({ poem: 'running' })
      return []
    })
    renderAt('/video/poetry', routes)
    expect(await screen.findByText('Say what the poem is about first.')).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('What is the poem about?'), 'dust in the wind')
    await userEvent.click(screen.getByRole('switch', { name: /Let me check each step/ }))
    await userEvent.click(screen.getByRole('radio', { name: /Highlight/ }))
    await userEvent.click(screen.getByRole('button', { name: /Make the Short/ }))
    await waitFor(() => expect(calls.find((c) => c.method === 'POST' && c.path === '/poetry')).toBeTruthy())
    const body = calls.find((c) => c.method === 'POST' && c.path === '/poetry')!.body as Record<string, unknown>
    expect(body).toMatchObject({ topic: 'dust in the wind', language: 'ur', aspect: '9:16', review: false, poem: '', caption_style: 'highlight' })
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/video/poetry/p1'))
  })
})
