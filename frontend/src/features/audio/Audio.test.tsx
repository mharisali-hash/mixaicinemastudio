import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { LibraryPage } from '@/features/library/LibraryPage'
import { TemplatesPage } from '@/features/templates/TemplatesPage'
import { activeRail, menusFrom, pageTitle, RAIL_ITEMS } from '@/lib/nav'
import type { MediaItem, Template } from '@/lib/types'
import { closeChip, openChip } from '@/test/dock'
import { job, media, mockApi, renderAt } from '@/test/media-fixtures'
import { CATALOG, MINIMAX, seedCatalog } from '@/test/model-fixtures'
import { MusicPage } from './MusicPage'
import { resetPlayer } from './sharedAudio'
import { SfxPage } from './SfxPage'
import { SongPage } from './SongPage'

const routes = [
  { path: '/audio/song', element: <SongPage /> },
  { path: '/audio/music', element: <MusicPage /> },
  { path: '/audio/sfx', element: <SfxPage /> },
]

const track = (id: string, extra: Partial<MediaItem> = {}) =>
  media(id, {
    kind: 'audio',
    title: 'Rain take',
    width: null,
    height: null,
    duration_s: 92,
    media_url: `/media/${id}.flac`,
    thumb_url: `/media/${id}.png`,
    params: { kind: 'sfx', model: 'sa3_small_sfx' },
    ...extra,
  })

let play: ReturnType<typeof vi.fn<() => Promise<void>>>
let pause: ReturnType<typeof vi.fn<() => void>>

beforeEach(() => {
  // jsdom has no media playback
  play = vi.fn(() => Promise.resolve())
  pause = vi.fn(() => {})
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(play)
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(pause)
  resetPlayer()
})

afterEach(() => {
  // unmount while pause() is still stubbed: a playing tile pauses on its way out
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function api(opts: { items?: MediaItem[]; lyrics?: () => unknown; aerab?: (lyrics: string) => unknown } = {}) {
  return mockApi((method, path, query, body) => {
    if (path === '/media') return { items: query.kind === 'audio' ? (opts.items ?? []) : [] }
    const one = path.match(/^\/media\/(\w+)$/)
    if (one) return { ...(opts.items ?? []).find((m) => m.id === one[1]), versions: [] }
    if (method === 'POST' && path === '/audio/generate') {
      const { kind, model } = body as { kind: string; model: string }
      return { items: [track('n1', { title: 'New take', media_url: null, params: { kind, model } })], jobs: [job('j1', { generation_id: 'g-n1' })], model_resolved: model }
    }
    if (method === 'POST' && path === '/audio/lyrics') return opts.lyrics?.()
    if (method === 'POST' && path === '/text/aerab') return opts.aerab?.((body as { text: string }).text)
    if (path === '/jobs') return []
  })
}

const posted = (calls: ReturnType<typeof api>, path = '/audio/generate') => calls.find((c) => c.method === 'POST' && c.path === path)?.body

describe('Audio Studio', () => {
  it('Song: posts style tags, sectioned lyrics, vocal, language, length and takes', async () => {
    const calls = api()
    renderAt('/audio/song', routes, seedCatalog)
    const user = userEvent.setup()

    await user.click(screen.getByRole('textbox', { name: 'Style tags' }))
    await user.paste('soulful qawwali, harmonium, tabla')
    const lyrics = screen.getByRole('textbox', { name: 'Lyrics' })
    await user.click(screen.getByRole('button', { name: 'Insert [verse]' }))
    expect(lyrics).toHaveValue('[verse]\n')
    await user.type(lyrics, 'Raat ka safar')
    await user.click(screen.getByRole('button', { name: 'Insert [chorus]' }))
    expect(lyrics).toHaveValue('[verse]\nRaat ka safar\n[chorus]\n')
    expect(lyrics).toHaveAccessibleDescription('31 / 6,000 characters')

    await user.click(within(await openChip(user, 'Vocal')).getByRole('button', { name: 'Female' }))
    await closeChip(user)
    await user.click(within(await openChip(user, 'Language')).getByRole('button', { name: 'Urdu' }))
    await closeChip(user)
    await user.click(within(await openChip(user, 'Length')).getByRole('button', { name: '3 min' }))
    await closeChip(user)
    await user.click(within(await openChip(user, 'Count')).getByRole('radio', { name: '2 songs' }))
    await closeChip(user)
    await user.click(screen.getByRole('button', { name: /^Compose · 2 × 3 min songs/ }))

    expect(posted(calls)).toEqual({
      kind: 'song',
      prompt: 'soulful qawwali, harmonium, tabla',
      model: 'ace15_turbo',
      duration_s: 180,
      count: 2,
      language: 'ur',
      vocal: 'female',
      lyrics: '[verse]\nRaat ka safar\n[chorus]',
      aerab: true,
    })
    expect(await screen.findByRole('listitem', { name: 'Request: soulful qawwali, harmonium, tabla' })).toBeInTheDocument()
  })

  it('Song: a child or one of my voices sings it when the server can change voices', async () => {
    const calls = mockApi((method, path) => {
      if (path === '/audio/song-voices') return { available: true, vocals: ['boy', 'girl', 'choir', 'voice'] }
      if (path === '/voices') return [{ id: 'v1', name: 'Ammi', kind: 'clone', language: 'ur' }]
      if (path === '/gallery') return { items: [], total: 0 }
      if (path === '/media') return { items: [] }
      if (method === 'POST' && path === '/audio/generate') return { items: [track('n1', { media_url: null })], jobs: [job('j1')], model_resolved: 'ace15_turbo' }
      if (path === '/jobs') return []
    })
    renderAt('/audio/song', routes, seedCatalog)
    const user = userEvent.setup()
    await user.click(screen.getByRole('textbox', { name: 'Style tags' }))
    await user.paste('lullaby, soft piano')
    await user.type(screen.getByRole('textbox', { name: 'Lyrics' }), 'Taaron ki chhaon mein')

    const chip = await openChip(user, 'Vocal')
    await user.click(await within(chip).findByRole('button', { name: 'Girl (child)' }))
    await closeChip(user)
    await user.click(screen.getByRole('button', { name: /^Compose/ }))
    expect(posted(calls)).toMatchObject({ vocal: 'girl' })

    // one of my voices has to be picked before it can be sent
    await user.type(screen.getByRole('textbox', { name: 'Style tags' }), 'lullaby')
    await user.type(screen.getByRole('textbox', { name: 'Lyrics' }), 'So ja')
    const again = await openChip(user, 'Vocal')
    await user.click(within(again).getByRole('button', { name: 'A voice of mine' }))
    await user.selectOptions(await within(again).findByRole('combobox', { name: 'Voice' }), 'Ammi')
    await closeChip(user)
    await user.click(screen.getByRole('button', { name: /^Compose/ }))
    expect(calls.filter((c) => c.method === 'POST' && c.path === '/audio/generate')[1].body).toMatchObject({ vocal: 'voice', sing_voice_id: 'v1' })
  })

  it('Song: a lead child and a group, with the who-sings markers', async () => {
    const calls = mockApi((method, path) => {
      if (path === '/audio/song-voices') return { available: true, vocals: ['boy', 'girl', 'choir', 'voice', 'lead_group'] }
      if (path === '/voices') return []
      if (path === '/gallery') return { items: [], total: 0 }
      if (path === '/media') return { items: [] }
      if (method === 'POST' && path === '/audio/generate') return { items: [track('n1', { media_url: null })], jobs: [job('j1')], model_resolved: 'ace15_turbo' }
      if (path === '/jobs') return []
    })
    renderAt('/audio/song', routes, seedCatalog)
    const user = userEvent.setup()
    await user.click(screen.getByRole('textbox', { name: 'Style tags' }))
    await user.paste("children's nasheed, soft drum")
    const chip = await openChip(user, 'Vocal')
    await user.click(await within(chip).findByRole('button', { name: "Lead child + children's group" }))
    await user.click(within(chip).getByRole('button', { name: 'Boy' }))
    await user.click(within(chip).getByRole('button', { name: '5 children' }))
    await closeChip(user)
    // the lyrics toolbar now offers the who-sings markers
    await user.click(screen.getByRole('button', { name: 'Insert [verse: lead]' }))
    await user.type(screen.getByRole('textbox', { name: 'Lyrics' }), 'Up with the sun (good morning!)')
    await user.click(screen.getByRole('button', { name: /^Compose/ }))
    expect(posted(calls)).toMatchObject({ vocal: 'lead_group', lead_voice: 'boy', group_size: 5, lyrics: '[verse: lead]\nUp with the sun (good morning!)' })
  })

  it('Song: no child voices are offered when the server has no voice changer', async () => {
    api()
    renderAt('/audio/song', routes, seedCatalog)
    const user = userEvent.setup()
    const chip = await openChip(user, 'Vocal')
    expect(within(chip).getByRole('button', { name: 'Duet' })).toBeInTheDocument()
    expect(within(chip).queryByRole('button', { name: 'Boy (child)' })).not.toBeInTheDocument()
  })

  it('Song: Instrumental sets the lyrics aside and sends vocal none', async () => {
    const calls = api()
    renderAt('/audio/song', routes, seedCatalog)
    const user = userEvent.setup()
    await user.click(screen.getByRole('textbox', { name: 'Style tags' }))
    await user.paste('lofi piano')
    await user.type(screen.getByRole('textbox', { name: 'Lyrics' }), 'la la')
    await user.click(screen.getByRole('switch', { name: /Instrumental/ }))
    expect(screen.getByRole('textbox', { name: 'Lyrics' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Insert [verse]' })).toBeDisabled()
    await user.keyboard('{Control>}{Enter}{/Control}')
    const body = posted(calls) as Record<string, unknown>
    expect(body.vocal).toBe('none')
    expect(body).not.toHaveProperty('lyrics')
  })

  it('Write lyrics fills the editor, sets the sung language, and asks before replacing', async () => {
    const calls = api({ lyrics: () => ({ lyrics: '[verse]\nDil ki baat', tags: 'ghazal' }) })
    renderAt('/audio/song', routes, seedCatalog)
    const user = userEvent.setup()

    await user.click(screen.getByRole('button', { name: 'Write lyrics' }))
    const pop = await screen.findByRole('dialog', { name: 'Write lyrics' })
    await user.type(within(pop).getByRole('textbox', { name: 'What is the song about?' }), 'Monsoon in Lahore')
    await user.selectOptions(within(pop).getByRole('combobox', { name: 'Language' }), 'ur-latn')
    await user.type(within(pop).getByRole('textbox', { name: 'Mood' }), 'wistful')
    await user.click(within(pop).getByRole('button', { name: 'Write' }))

    expect(await screen.findByRole('textbox', { name: 'Lyrics' })).toHaveValue('[verse]\nDil ki baat')
    expect(posted(calls, '/audio/lyrics')).toEqual({ topic: 'Monsoon in Lahore', language: 'ur-latn', mood: 'wistful' })
    expect(screen.getByRole('button', { name: 'Language: Urdu' })).toBeInTheDocument()

    // a second write would overwrite: confirm first
    await user.click(screen.getByRole('button', { name: 'Write lyrics' }))
    await user.click(within(await screen.findByRole('dialog', { name: 'Write lyrics' })).getByRole('button', { name: 'Write' }))
    expect(await screen.findByRole('alertdialog', { name: 'Replace your lyrics?' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.getByRole('textbox', { name: 'Lyrics' })).toHaveValue('[verse]\nDil ki baat')
  })

  it('Urdu lyrics: Add aerab marks them, can be undone, and the song asks for aerab', async () => {
    const calls = api({ aerab: (l) => ({ text: l.replace('دل', 'دِل'), marked: 1, kept: 1, low_coverage: 0, language: 'ur' }) })
    renderAt('/audio/song', routes, seedCatalog)
    const user = userEvent.setup()
    const lyrics = screen.getByRole('textbox', { name: 'Lyrics' })
    expect(screen.queryByRole('button', { name: /Add aerab/ })).not.toBeInTheDocument()
    await user.click(lyrics)
    await user.paste('[verse]\nدل کی بات\nآج کی رات')

    await user.click(screen.getByRole('button', { name: /Add aerab/ }))
    await waitFor(() => expect(lyrics).toHaveValue('[verse]\nدِل کی بات\nآج کی رات'))
    // the song is still in English here, so the server reads the language from the letters
    expect(posted(calls, '/text/aerab')).toEqual({ text: '[verse]\nدل کی بات\nآج کی رات' })
    await user.click(screen.getByRole('button', { name: 'Undo aerab' }))
    expect(lyrics).toHaveValue('[verse]\nدل کی بات\nآج کی رات')

    const auto = screen.getByRole('switch', { name: /Aerab before singing/ })
    expect(auto).toBeChecked()
    await user.click(auto)
    await user.click(screen.getByRole('textbox', { name: 'Style tags' }))
    await user.paste('ghazal, harmonium')
    await user.click(within(await openChip(user, 'Language')).getByRole('button', { name: 'Urdu' }))
    await closeChip(user)
    await user.keyboard('{Control>}{Enter}{/Control}')
    expect(posted(calls)).toMatchObject({ language: 'ur', aerab: false })
  })

  it('Write lyrics in Urdu asks for aerab by default', async () => {
    const calls = api({ lyrics: () => ({ lyrics: '[verse]\nدِل کی بات', tags: 'ghazal', marked: 1, kept: 0 }) })
    renderAt('/audio/song', routes, seedCatalog)
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Write lyrics' }))
    const pop = await screen.findByRole('dialog', { name: 'Write lyrics' })
    await user.type(within(pop).getByRole('textbox', { name: 'What is the song about?' }), 'Rain')
    await user.selectOptions(within(pop).getByRole('combobox', { name: 'Language' }), 'ur')
    expect(within(pop).getByRole('checkbox', { name: /With aerab/ })).toBeChecked()
    await user.click(within(pop).getByRole('button', { name: 'Write' }))
    expect(await screen.findByRole('textbox', { name: 'Lyrics' })).toHaveValue('[verse]\nدِل کی بات')
    expect(posted(calls, '/audio/lyrics')).toEqual({ topic: 'Rain', language: 'ur', aerab: true })
  })

  it('Write lyrics says so plainly when the writer is offline', async () => {
    api({ lyrics: () => new Response(JSON.stringify({ detail: 'The lyrics writer is offline' }), { status: 503, headers: { 'Content-Type': 'application/json' } }) })
    renderAt('/audio/song', routes, seedCatalog)
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Write lyrics' }))
    const pop = await screen.findByRole('dialog', { name: 'Write lyrics' })
    await user.type(within(pop).getByRole('textbox', { name: 'What is the song about?' }), 'Rain{Enter}')
    expect(await within(pop).findByRole('alert')).toHaveTextContent(/lyrics writer is offline.*Write the lyrics yourself/)
    expect(screen.getByRole('textbox', { name: 'Lyrics' })).toHaveValue('')
  })

  it('MiniMax is only offered when the catalog lists it, under its full name, with no licence text', async () => {
    api()
    const first = renderAt('/audio/song', routes, seedCatalog)
    const user = userEvent.setup()
    let models = await openChip(user, 'Model')
    expect(within(models).queryByRole('radio', { name: /MiniMax/ })).not.toBeInTheDocument()
    expect(within(models).getByRole('radio', { name: 'ACE-Step 1.5' })).not.toHaveAccessibleDescription(/Apache/)
    // music models don't belong on the Song page
    expect(within(models).queryByRole('radio', { name: /Stable Audio/ })).not.toBeInTheDocument()
    first.unmount()

    renderAt('/audio/song', routes, (qc) => seedCatalog(qc, { ...CATALOG, audio: [...CATALOG.audio, MINIMAX] }))
    models = await openChip(user, 'Model')
    const mm = within(models).getByRole('radio', { name: 'MiniMax-Music3' })
    expect(mm).not.toHaveAccessibleDescription(/commercial|licen/i)
  })

  it('Music: a long Medium loop with BPM', async () => {
    const calls = api()
    renderAt('/audio/music', routes, seedCatalog)
    const user = userEvent.setup()
    await user.click(screen.getByRole('textbox', { name: 'Describe the music' }))
    await user.paste('tense score, low strings')
    // Small tops out at 2 min, so 6 min isn't offered yet
    expect(within(await openChip(user, 'Length')).queryByRole('button', { name: '6 min' })).not.toBeInTheDocument()
    await closeChip(user)
    await user.click(within(await openChip(user, 'Model')).getByRole('radio', { name: 'Stable Audio 3 Medium' }))
    await closeChip(user)
    await user.click(within(await openChip(user, 'Length')).getByRole('button', { name: '6 min' }))
    await closeChip(user)
    await user.click(within(await openChip(user, 'Category')).getByRole('button', { name: 'Loop' }))
    await closeChip(user)
    await user.click(screen.getByRole('radio', { name: 'Advanced' }))
    await user.type(screen.getByRole('textbox', { name: 'BPM' }), '70')
    await user.click(screen.getByRole('button', { name: /^Compose/ }))
    expect(posted(calls)).toEqual({ kind: 'music', prompt: 'tense score, low strings', model: 'sa3_medium', duration_s: 360, count: 1, category: 'loop', bpm: 70 })
    // queued: the box is ready for the next idea, and it takes as much as anyone writes
    const box = screen.getByRole('textbox', { name: 'Describe the music' })
    await waitFor(() => expect(box).toHaveValue(''))
    expect(box).not.toHaveAttribute('maxlength')
  })

  it('SFX: a preset fills the prompt; length and takes go with it', async () => {
    const calls = api()
    renderAt('/audio/sfx', routes, seedCatalog)
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Thunder' }))
    expect(screen.getByRole('textbox', { name: 'Describe the sound' })).toHaveValue('Thunder')
    await user.click(within(await openChip(user, 'Length')).getByRole('button', { name: '20 s' }))
    await closeChip(user)
    await user.click(within(await openChip(user, 'Count')).getByRole('radio', { name: '3 sounds' }))
    await closeChip(user)
    await user.click(screen.getByRole('button', { name: /^Generate/ }))
    expect(posted(calls)).toEqual({ kind: 'sfx', prompt: 'Thunder', model: 'sa3_small_sfx', duration_s: 20, count: 3 })
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'Describe the sound' })).toHaveValue(''))
  })

  it('a tile plays and pauses from the keyboard, one shared player at a time', async () => {
    api({ items: [track('a1'), track('a2', { title: 'Wind take' })] })
    renderAt('/audio/sfx', routes, seedCatalog)
    const user = userEvent.setup()

    const rain = await screen.findByRole('button', { name: 'Play Rain take' })
    expect(rain).toHaveAccessibleDescription('Waveform, 1:32')
    rain.focus()
    await user.keyboard('{Enter}')
    expect(play).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Pause Rain take' })).toBeInTheDocument()

    // starting another track takes over the player
    await user.click(screen.getByRole('button', { name: 'Play Wind take' }))
    expect(screen.getByRole('button', { name: 'Play Rain take' })).toBeInTheDocument()
    const wind = screen.getByRole('button', { name: 'Pause Wind take' })
    wind.focus()
    await user.keyboard(' ')
    expect(pause).toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Play Wind take' })).toBeInTheDocument()
  })

  it('the lightbox player seeks with the keyboard and never autoplays', async () => {
    api({ items: [track('a1')] })
    renderAt('/audio/sfx', routes, seedCatalog)
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: 'Open Rain take' }))
    const box = await screen.findByRole('dialog', { name: 'Rain take' })
    expect(within(box).getByRole('img', { name: 'Waveform, 1:32' })).toBeInTheDocument()
    expect(play).not.toHaveBeenCalled()
    const seek = within(box).getByRole('slider', { name: 'Seek' })
    expect(seek).toHaveAttribute('aria-valuetext', '0:00 of 1:32')
    seek.focus()
    await user.keyboard('{ArrowRight}{ArrowRight}')
    expect(seek).toHaveAttribute('aria-valuetext', '0:10 of 1:32')
    await user.keyboard('{ArrowLeft}')
    expect(seek).toHaveAttribute('aria-valuetext', '0:05 of 1:32')
    await user.keyboard('{End}')
    expect(seek).toHaveAttribute('aria-valuetext', '1:32 of 1:32')
    await user.click(within(box).getByRole('button', { name: 'Play Rain take' }))
    expect(play).toHaveBeenCalledTimes(1)
  })

  it('the audio library lists tracks and takes audio uploads', async () => {
    api({ items: [track('a1')] })
    renderAt('/audio/library', [{ path: '/audio/library', element: <LibraryPage kind="audio" /> }], seedCatalog)
    expect(await screen.findByRole('heading', { name: 'Audio library' })).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Play Rain take' })).toBeInTheDocument()
    const accept = screen.getByTestId('upload-input').getAttribute('accept')!
    expect(accept).toContain('audio/flac')
    expect(accept).toContain('.mp3')
    expect(screen.getByText('Drop audio files here')).toBeInTheDocument()
  })

  it('a measured track shows its loudness and warnings, and its menu opens the editor and Score a Clip', async () => {
    api({ items: [track('a1', { analysis: { lufs_i: -8.24, true_peak_db: 0.1, flags: ['too_loud', 'clipping'] } })] })
    renderAt('/audio/library', [{ path: '/audio/library', element: <LibraryPage kind="audio" /> }], seedCatalog)
    const user = userEvent.setup()
    expect(await screen.findByText('Quality: −8.2 LUFS integrated, too loud, clipping.')).toBeInTheDocument()
    expect(screen.getByText('−8.2 LUFS')).toHaveAttribute('aria-hidden', 'true')
    await user.click(screen.getByRole('button', { name: 'More for Rain take' }))
    expect(await screen.findByRole('menuitem', { name: 'Open in Editor' })).toHaveAttribute('href', '/audio/edit/a1')
    expect(screen.getByRole('menuitem', { name: 'Use in Score a Clip' })).toHaveAttribute('href', '/audio/score?audio=a1')
  })

  it('Score a Clip sends a generator the clip length and a way back', async () => {
    api()
    renderAt('/audio/music?duration=12&from=score', routes, seedCatalog)
    expect(await screen.findByRole('link', { name: /Back to Score a Clip/ })).toHaveAttribute('href', '/audio/score')
    expect(screen.getByRole('button', { name: 'Length: 12 s' })).toBeInTheDocument()
  })

  it('an audio prompt template opens the page its category belongs to', async () => {
    const rain: Template = { id: 'rain', type: 'audio', title: 'Monsoon rain', description: 'Heavy rain', category: 'Ambience', defaults: { prompt_scaffold: 'Monsoon rain on [surface]', duration_s: 20 } }
    const calls = mockApi((method, path) => {
      if (path === '/prompt-templates') return [rain]
      if (method === 'POST') return new Response('{}', { status: 404 })
      if (path === '/media') return { items: [] }
    })
    renderAt('/audio/prompt-templates', [...routes, { path: '/audio/prompt-templates', element: <TemplatesPage type="audio" /> }], seedCatalog)
    const user = userEvent.setup()
    expect(await screen.findByRole('heading', { name: 'Audio prompt templates' })).toBeInTheDocument()
    expect(calls.find((c) => c.path === '/prompt-templates')?.query).toEqual({ type: 'audio' })
    await user.click(await screen.findByRole('button', { name: 'Use the Monsoon rain prompt template' }))
    expect(await screen.findByRole('textbox', { name: 'Describe the sound' })).toHaveValue('Monsoon rain on [surface]')
    expect(screen.getByTestId('location')).toHaveTextContent('/audio/sfx')
    expect(screen.getByRole('button', { name: 'Length: 20 s' })).toBeInTheDocument()
  })
})

describe('Audio navigation', () => {
  it('has an Audio rail entry after Video, with its menu and catalog models', () => {
    const ids = RAIL_ITEMS.map((r) => r.id)
    expect(ids.indexOf('audio')).toBe(ids.indexOf('video') + 1)
    expect(RAIL_ITEMS.find((r) => r.id === 'audio')?.to).toBe('/audio/song')
    // Podcast Studio has its own rail entry right after Audio, so Audio no longer lists it
    expect(ids.indexOf('podcast')).toBe(ids.indexOf('audio') + 1)
    expect(menusFrom(CATALOG).podcast.features.map((f) => f.to)).toEqual(['/podcast', '/podcast?new=audio', '/podcast?new=video'])
    expect(activeRail('/podcast/e1')).toBe('podcast')
    expect(pageTitle('/podcast')).toBe('Podcast Studio')

    const menu = menusFrom(CATALOG).audio
    const live = menu.features.filter((f) => !f.disabled).map((f) => [f.title, f.to])
    expect(live).toEqual([
      ['Song', '/audio/song'],
      ['Music & Score', '/audio/music'],
      ['Sound Effects', '/audio/sfx'],
      ['Speak', '/audio/speak'],
      ['Voices', '/audio/voices'],
      ['Voice gallery', '/audio/gallery'],
      ['Design a voice', '/audio/design'],
      ['Dialogue', '/audio/dialogue'],
      ['Editor', '/audio/edit'],
      ['Score a Clip', '/audio/score'],
      ['Split a song', '/audio/stems'],
      ['Remix a song', '/audio/remix'],
      ['Transcribe', '/audio/transcribe'],
      ['Dub a video', '/audio/dub'],
      ['Prompt templates', '/audio/prompt-templates'],
      ['Library', '/audio/library'],
    ])
    expect(menu.models.map((m) => m.to)).toEqual([
      '/audio/song?model=ace15_turbo',
      '/audio/music?model=sa3_small_music',
      '/audio/music?model=sa3_medium',
      '/audio/sfx?model=sa3_small_sfx',
    ])
    expect(activeRail('/audio/sfx')).toBe('audio')
    expect(pageTitle('/audio/library')).toBe('Audio library')
    expect(pageTitle('/audio/song')).toBe('Song')
    expect(pageTitle('/audio/voices')).toBe('Voices')
    expect(pageTitle('/audio/stems')).toBe('Stems')
  })
})
