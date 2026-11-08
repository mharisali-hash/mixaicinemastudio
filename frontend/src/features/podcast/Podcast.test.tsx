import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Script, ScriptLine } from '@/lib/dialogue'
import type { GalleryResponse } from '@/lib/gallery'
import type { Episode, EpisodeSettings } from '@/lib/podcast'
import { stubMedia } from '@/test/audio-fixtures'
import { T, job, mockApi, renderAt } from '@/test/media-fixtures'
import { ENGINES, VOICES } from '@/test/voice-fixtures'
import { resetPlayer } from '@/features/audio/sharedAudio'
import { EpisodePage } from './EpisodePage'
import { PodcastListPage } from './PodcastListPage'

const routes = [
  { path: '/podcast', element: <PodcastListPage /> },
  { path: '/podcast/:id', element: <EpisodePage /> },
]

const SETTINGS: EpisodeSettings = {
  intro: { media_id: null, lead_s: 4, overlap_s: 3, gain_db: 0 },
  outro: { media_id: null, overlap_s: 2, gain_db: 0, max_s: 30 },
  bed: { media_id: null, gain_db: -20 },
  duck_db: 12,
  gap_s: 0.35,
  chapter_gap_s: 1,
  loudness: 'stereo',
}

const HOSTS = [
  { id: 'h1', name: 'Ali', role: 'host' as const, persona: 'curious', voice_id: null, me: false },
  { id: 'h2', name: 'Sara', role: 'cohost' as const, persona: '', voice_id: null, me: true },
]

const line = (id: string, text: string, extra: Partial<ScriptLine> = {}): ScriptLine => ({
  id,
  speaker_id: 'h1',
  text,
  voice_id: null,
  speed: null,
  emotion: null,
  pause_after_s: null,
  para: false,
  kind: 'speech',
  media_id: null,
  chapter_id: 'c1',
  status: 'empty',
  ...extra,
})

const LINES = [
  line('a', 'Welcome to the show.', { status: 'ready', take_id: 'g1', take_url: '/m/a.flac', duration_s: 2 }),
  line('b', 'Glad to be here.', { speaker_id: 'h2', kind: 'recording' }),
  line('c', 'The bridge opened.', { chapter_id: 'c2', status: 'stale', take_id: 'g3' }),
  line('d', 'See you next week.', { chapter_id: 'c2' }),
]

const script = (lines: ScriptLine[] = LINES): Script => ({
  id: 's1',
  title: 'Bridge day',
  language: 'en',
  engine: 'auto',
  project_id: null,
  cast: [
    { id: 'narrator', name: 'Narrator', voice_id: null, color: '#5B4630' },
    { id: 'h1', name: 'Ali', voice_id: null, color: '#2F5D62' },
    { id: 'h2', name: 'Sara', voice_id: null, color: '#8B2E2E' },
  ],
  lines,
  settings: { gap_s: 0.35, paragraph_gap_s: 0.8, loudness: 'none', aerab: true },
  mix: null,
  mix_job: null,
  stems_job: null,
  counts: { lines: lines.length, ready: 1, stale: 1, empty: 2, rendering: 0, failed: 0 },
  created_at: T,
  updated_at: T,
})

const episode = (extra: Partial<Episode> = {}): Episode => ({
  id: 'e1',
  show_id: null,
  show_name: null,
  title: 'Bridge day',
  format: 'conversation',
  medium: 'audio',
  language: 'en',
  target_minutes: 5,
  step: 1,
  cover_media_id: null,
  cover_url: null,
  sources: { topic: 'A new bridge', text: '', docs: [], has_script: true },
  hosts: HOSTS,
  chapters: [
    { id: 'c1', title: 'Hello' },
    { id: 'c2', title: 'The news' },
  ],
  script_id: 's1',
  settings: SETTINGS,
  notes: null,
  outputs: { mix: null, publish: null, video: null },
  jobs: {},
  words: 15,
  minutes: 0.1,
  counts: { lines: 4, ready: 1, stale: 1, empty: 2, rendering: 0, failed: 0 },
  created_at: T,
  updated_at: T,
  ...extra,
})

const GALLERY: GalleryResponse = {
  items: [
    {
      id: 'ur-dadi',
      voice_id: 'gallery:ur-dadi',
      name: 'Dadi Amma, grandmother',
      language: 'en',
      languages: ['en'],
      gender: 'female',
      age: 'elder',
      accent: 'Urdu',
      styles: ['storyteller'],
      engine: 'omnivoice',
      kind: 'design',
      instruct: 'female, elderly',
      seed: 3,
      sample_url: '/api/voices/gallery/ur-dadi/sample',
      sample_ready: true,
      added_voice_id: null,
      starred: false,
    },
  ],
  total: 1,
  facets: { language: [], gender: [], age: [], style: [], engine: [] },
}

const MIXED = {
  media_id: 'mx',
  generation_id: 'gm',
  url: '/m/mix.flac',
  duration_s: 70,
  built_at: T,
  stale: false,
  timeline: [],
  chapters: [
    { id: 'c1', title: 'Hello', start: 0, end: 30 },
    { id: 'c2', title: 'The news', start: 30, end: 70 },
  ],
}

function api(initial: Episode, s: Script = script()) {
  let ep = initial
  return mockApi((method, path, _q, body) => {
    const b = body as Record<string, unknown>
    if (path === '/voices/engines') return ENGINES
    if (path === '/voices') return VOICES
    if (path === '/voices/gallery') return GALLERY
    if (path === '/jobs') return []
    if (path.startsWith('/favourites')) return { refs: [] }
    if (path === '/media') return { items: [] }
    if (path === '/podcast/shows') return method === 'GET' ? [] : { id: 'sh1' }
    if (path === '/characters') return [{ id: 'ch1', project_id: null, name: 'Ayesha', description: 'a calm presenter', source: 'user', locked: false, voice_id: 'gallery:ur-dadi' }]
    if (path === '/characters/ch1/looks')
      return {
        looks: [{ id: 'lk1', character_id: 'ch1', name: 'Office', outfit: '', setting: '', pose: '', prompt: '', is_default: true, sort: 0, picture: { id: 'lp1', status: 'ready', thumb_url: '/m/lp1.png' }, created_at: T }],
        packs: [],
      }
    if (path === '/podcast/episodes/e1/ai-video') return new Response('{"detail": "Not found"}', { status: 404, headers: { 'Content-Type': 'application/json' } })
    if (method === 'GET' && path === '/podcast/episodes')
      return [
        { id: 'e1', show_id: null, title: 'Bridge day', format: 'conversation', medium: 'audio', language: 'en', target_minutes: 5, step: 3, minutes: 0.1, has_mix: false, published: false, created_at: T, updated_at: T },
        { id: 'e2', show_id: null, title: 'On camera', format: 'conversation', medium: 'video', language: 'en', target_minutes: 5, step: 8, minutes: 0.1, has_mix: true, published: false, created_at: T, updated_at: T },
      ]
    if (method === 'POST' && path === '/podcast/episodes') return episode({ id: 'e9', title: (b.title as string) ?? 'New episode' })
    if (method === 'GET' && path === '/podcast/episodes/e1') return ep
    if (method === 'PATCH' && path === '/podcast/episodes/e1') {
      ep = { ...ep, ...(b as Partial<Episode>) }
      return ep
    }
    if (method === 'GET' && path === '/scripts/s1') return s
    if (method === 'PATCH' && path === '/scripts/s1') return s
    if (method === 'POST' && path === '/podcast/episodes/e1/edit-lines') return job('w1', { type: 'podcast_write', status: 'queued' })
    if (method === 'POST' && path === '/podcast/episodes/e1/write') return job('w2', { type: 'podcast_write', status: 'queued' })
    if (method === 'POST' && path === '/podcast/episodes/e1/render') return { jobs: [job('r1', { type: 'generate', lane: 'audio' })], queued: (b.line_ids as string[]).length, failed: 0 }
    if (method === 'POST' && path === '/podcast/episodes/e1/insert') return ep
    if (method === 'POST' && path === '/podcast/episodes/e1/mix') return job('m1', { type: 'podcast_mix', status: 'queued' })
    if (method === 'POST' && path === '/podcast/episodes/e1/publish') return job('p1', { type: 'podcast_publish', status: 'queued' })
    if (method === 'POST' && path === '/podcast/episodes/e1/video') return job('v1', { type: 'podcast_video', status: 'queued' })
  })
}

const sent = (calls: ReturnType<typeof api>, method: string, path: string) => calls.filter((c) => c.method === method && c.path === path).map((c) => c.body)

beforeEach(() => {
  stubMedia()
  resetPlayer()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('Podcast', { timeout: 20_000 }, () => {
  it('lists episodes and starts a new one with hosts for its format', async () => {
    const calls = api(episode())
    renderAt('/podcast', routes)
    const user = userEvent.setup()
    expect(await screen.findByRole('link', { name: 'Bridge day' })).toHaveAttribute('href', '/podcast/e1')
    expect(screen.getByText('Step 3: Script')).toBeInTheDocument()
    // a video episode's step counts on into the video steps
    const video = screen.getByRole('link', { name: 'On camera' }).closest('li')!
    expect(within(video).getByText('Video')).toBeInTheDocument()
    expect(within(video).getByText('Step 8: Set')).toBeInTheDocument()
    expect(within(screen.getByRole('link', { name: 'Bridge day' }).closest('li')!).getByText('Audio')).toBeInTheDocument()
    await user.click(screen.getAllByRole('button', { name: 'New episode' })[0])
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('radio', { name: /Audio podcast/ })).toBeChecked()
    await user.click(within(dialog).getByRole('radio', { name: /Video podcast/ }))
    await user.type(within(dialog).getByRole('textbox', { name: 'Topic' }), 'Lahore gates')
    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'Format' }), 'interview')
    await user.click(within(dialog).getByRole('button', { name: 'Create episode' }))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/podcast/e9'))
    const body = sent(calls, 'POST', '/podcast/episodes')[0] as Record<string, unknown>
    expect(body).toMatchObject({ topic: 'Lahore gates', format: 'interview', medium: 'video', title: 'New episode', target_minutes: 5, show_id: null })
    expect((body.hosts as { role: string }[]).map((h) => h.role)).toEqual(['host', 'cohost'])
  })

  it('opens the new-episode dialog from the menu link, already set to video', async () => {
    api(episode())
    renderAt('/podcast?new=video', routes)
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('radio', { name: /Video podcast/ })).toBeChecked()
  })

  it('makes a host one of your Character Studio characters, with their voice and look', async () => {
    const calls = api(episode({ step: 2, medium: 'video' }))
    renderAt('/podcast/e1', routes)
    const user = userEvent.setup()
    await screen.findByRole('heading', { name: 'Hosts' })
    await user.click(screen.getByRole('button', { name: 'Use a character for Ali' }))
    await user.click(await within(screen.getByRole('dialog')).findByRole('button', { name: /Ayesha/ }))
    expect(screen.getByRole('textbox', { name: 'Host 1 name' })).toHaveValue('Ayesha')
    // the default look comes along once it's known to be drawn
    await waitFor(() => {
      const last = sent(calls, 'PATCH', '/podcast/episodes/e1').at(-1) as { hosts?: Record<string, unknown>[] }
      expect(last?.hosts?.[0]).toMatchObject({ name: 'Ayesha', character_id: 'ch1', look_id: 'lk1', voice_id: 'gallery:ur-dadi', persona: 'curious' })
    })
    expect(screen.getByRole('combobox', { name: 'Look on camera for Ayesha' })).toHaveValue('lk1')
    await user.click(screen.getByRole('button', { name: /Remove character from Ayesha/ }))
    await waitFor(() => {
      const last = sent(calls, 'PATCH', '/podcast/episodes/e1').at(-1) as { hosts?: Record<string, unknown>[] }
      expect(last?.hosts?.[0]).toMatchObject({ character_id: null, look_id: null, voice_id: 'gallery:ur-dadi' })
    })
  })

  it('moves through the steps with the keyboard and remembers the step', async () => {
    const calls = api(episode())
    renderAt('/podcast/e1', routes)
    const user = userEvent.setup()
    const source = await screen.findByRole('tab', { name: /Source/ })
    expect(source).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: /Source, done/ })).toBe(source)
    source.focus()
    await user.keyboard('{ArrowRight}')
    const hosts = screen.getByRole('tab', { name: /Hosts/ })
    expect(hosts).toHaveAttribute('aria-selected', 'true')
    expect(hosts).toHaveFocus()
    expect(screen.getByRole('heading', { name: 'Hosts' })).toBeInTheDocument()
    await user.keyboard('{End}')
    expect(screen.getByRole('tab', { name: /Publish/ })).toHaveAttribute('aria-selected', 'true')
    await waitFor(() => expect(sent(calls, 'PATCH', '/podcast/episodes/e1')).toContainEqual({ step: 6 }))
  })

  it('picks host voices from the gallery and a host who records', async () => {
    const calls = api(episode({ step: 2 }))
    renderAt('/podcast/e1', routes)
    const user = userEvent.setup()
    await screen.findByRole('heading', { name: 'Hosts' })
    // Sara records her own lines, so she has no voice picker
    expect(screen.getByRole('checkbox', { name: /Me — I’ll record/, checked: true })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Voice for Sara/ })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Voice for Ali/ }))
    await user.click(await screen.findByRole('button', { name: /Dadi Amma/ }))
    const name = screen.getByRole('textbox', { name: 'Host 1 name' })
    await user.clear(name)
    await user.type(name, 'Bilal')
    await waitFor(() => {
      const last = sent(calls, 'PATCH', '/podcast/episodes/e1').at(-1) as { hosts?: { name: string; voice_id: string | null }[] }
      expect(last?.hosts?.[0]).toMatchObject({ name: 'Bilal', voice_id: 'gallery:ur-dadi' })
    })
  })

  it('shows chapters and sends picked lines to the writer', async () => {
    const calls = api(episode({ step: 3 }))
    renderAt('/podcast/e1', routes)
    const user = userEvent.setup()
    expect(await screen.findByRole('heading', { name: 'Hello' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'The news' })).toBeInTheDocument()
    expect(screen.getByText('Recording')).toBeInTheDocument()
    // numbering runs on across chapters
    expect(screen.getByRole('textbox', { name: 'Line 3 text' })).toHaveValue('The bridge opened.')
    const shorter = screen.getByRole('button', { name: 'Shorter' })
    expect(shorter).toBeDisabled()
    await user.click(screen.getByRole('checkbox', { name: 'Select line 1' }))
    await user.click(screen.getByRole('checkbox', { name: 'Select line 2' }))
    await user.click(shorter)
    await waitFor(() => expect(sent(calls, 'POST', '/podcast/episodes/e1/edit-lines')).toEqual([{ op: 'shorter', line_ids: ['a', 'b'] }]))
    await user.click(screen.getByRole('button', { name: 'Rewrite chapter The news' }))
    await waitFor(() => expect(sent(calls, 'POST', '/podcast/episodes/e1/edit-lines')).toContainEqual({ op: 'rewrite_chapter', chapter_id: 'c2' }))
  })

  it('renders only changed AI lines and offers a recorder for the host who records', async () => {
    const calls = api(episode({ step: 4 }))
    renderAt('/podcast/e1', routes)
    const user = userEvent.setup()
    const btn = await screen.findByRole('button', { name: 'Render 2 changed' })
    await user.click(btn)
    await waitFor(() => expect(sent(calls, 'POST', '/podcast/episodes/e1/render')).toEqual([{ line_ids: ['c', 'd'] }]))
    const mine = screen.getByRole('heading', { name: /Your lines \(0 of 1 recorded\)/ })
    expect(mine).toBeInTheDocument()
    expect(screen.getByText('Glad to be here.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Record' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Or upload a clip' })).toBeInTheDocument()
  })

  it('sets produce options, inserts a pause and waits for every line before mixing', async () => {
    const calls = api(episode({ step: 5 }))
    renderAt('/podcast/e1', routes)
    const user = userEvent.setup()
    await screen.findByRole('heading', { name: 'Produce' })
    expect(screen.getByText('3 lines to voice or record first (step 4)')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Mix the episode' }))
    expect(sent(calls, 'POST', '/podcast/episodes/e1/mix')).toEqual([])
    await user.selectOptions(screen.getByRole('combobox', { name: 'Loudness' }), 'mono')
    await waitFor(() => expect((sent(calls, 'PATCH', '/podcast/episodes/e1').at(-1) as { settings: EpisodeSettings }).settings.loudness).toBe('mono'))
    await user.selectOptions(screen.getByRole('combobox', { name: 'Insert' }), '1')
    await user.click(screen.getByRole('button', { name: 'A 1.5 s pause' }))
    await waitFor(() => expect(sent(calls, 'POST', '/podcast/episodes/e1/insert')).toEqual([{ kind: 'break', after_line_id: 'b', seconds: 1.5 }]))
    expect(screen.getByRole('img', { name: /Episode structure: Hello/ })).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: 'Music page' })[0]).toHaveAttribute('href', expect.stringContaining('from=podcast&episode=e1'))
  })

  it('mixes once every line is ready', async () => {
    const ready = { lines: 4, ready: 4, stale: 0, empty: 0, rendering: 0, failed: 0 }
    const calls = api(episode({ step: 5, counts: ready }))
    renderAt('/podcast/e1', routes)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Mix the episode' }))
    await waitFor(() => expect(sent(calls, 'POST', '/podcast/episodes/e1/mix')).toHaveLength(1))
  })

  it('publishes the audio, the subtitles and the audiogram', async () => {
    const calls = api(episode({ step: 6, outputs: { mix: MIXED, publish: null, video: null } }))
    renderAt('/podcast/e1', routes)
    const user = userEvent.setup()
    await screen.findByRole('heading', { name: 'Publish' })
    expect(screen.getByRole('list', { name: 'Chapters' })).toHaveTextContent('00:30The news')
    await user.click(screen.getByRole('radio', { name: /M4A/ }))
    await user.click(screen.getByRole('button', { name: 'Publish M4A' }))
    await waitFor(() => expect(sent(calls, 'POST', '/podcast/episodes/e1/publish')).toEqual([{ format: 'm4a', notes: true }]))
    expect(screen.getByRole('link', { name: 'Download SRT' })).toHaveAttribute('href', '/api/podcast/episodes/e1/subtitles.srt')
    await user.click(screen.getByRole('radio', { name: /1:1/ }))
    await user.click(screen.getByRole('checkbox', { name: 'Burn in subtitles' }))
    expect(screen.getByRole('heading', { name: 'Audiogram' })).toBeInTheDocument()
    expect(screen.getByText('A quick share video: cover, waveform, speaker names and subtitles')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Make the audiogram' }))
    await waitFor(() => expect(sent(calls, 'POST', '/podcast/episodes/e1/video')).toEqual([{ aspect: '1:1', burn_subtitles: false }]))
    await user.type(screen.getByRole('textbox', { name: 'Description' }), 'Two friends on a bridge.')
    await user.click(screen.getByRole('button', { name: 'Save notes' }))
    await waitFor(() =>
      expect(sent(calls, 'PATCH', '/podcast/episodes/e1')).toContainEqual({
        notes: { description: 'Two friends on a bridge.', key_points: [], chapters: [{ title: 'Hello', start: 0 }, { title: 'The news', start: 30 }] },
      }),
    )
  })

  it("won't publish before there's a mix", async () => {
    const calls = api(episode({ step: 6 }))
    renderAt('/podcast/e1', routes)
    const user = userEvent.setup()
    expect(await screen.findByText(/Mix the episode first \(step 5\)/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Publish MP3' }))
    expect(sent(calls, 'POST', '/podcast/episodes/e1/publish')).toEqual([])
  })
})
