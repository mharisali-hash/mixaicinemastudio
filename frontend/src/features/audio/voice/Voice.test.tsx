import { act, cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CONSENT_STATEMENT } from '@/lib/voice'
import type { Job, Voice } from '@/lib/types'
import { audioItem, stubMedia } from '@/test/audio-fixtures'
import { fakeXhr } from '@/test/brand-fixtures'
import { closeChip, openChip } from '@/test/dock'
import { job, media, mockApi, renderAt } from '@/test/media-fixtures'
import { ENGINES, VOICES, voice } from '@/test/voice-fixtures'
import { resetPlayer } from '../sharedAudio'
import { DesignPage } from './DesignPage'
import { SpeakPage } from './SpeakPage'
import { VoicesPage } from './VoicesPage'

const routes = [
  { path: '/audio/speak', element: <SpeakPage /> },
  { path: '/audio/voices', element: <VoicesPage /> },
  { path: '/audio/design', element: <DesignPage /> },
]

interface Opts {
  voices?: Voice[]
  created?: (body: Record<string, unknown>) => Voice
  jobs?: () => Job[]
}

function api(opts: Opts = {}) {
  let n = 0
  return mockApi((method, path, _q, body) => {
    const b = body as Record<string, unknown>
    if (path === '/voices/engines') return ENGINES
    if (method === 'GET' && path === '/voices') return opts.voices ?? VOICES
    if (method === 'POST' && path === '/voices') return opts.created?.(b) ?? voice('v9', { name: b.name as string })
    if (method === 'PATCH' && path.startsWith('/voices/')) {
      const id = path.split('/')[2]
      return { ...((opts.voices ?? VOICES).find((v) => v.id === id) ?? voice(id)), ...b }
    }
    if (method === 'POST' && /^\/voices\/\w+\/transcribe$/.test(path)) return job('t1', { type: 'voice_transcribe', status: 'queued' })
    if (method === 'POST' && (path === '/audio/speak' || path === '/voices/design/preview')) {
      n += 1
      const item = audioItem(`s${n}`, { title: `Take ${n}`, media_url: null, params: { kind: 'speech', engine: b.engine, voice_id: b.voice_id ?? null, text: b.text } })
      return { items: [item], jobs: [job(`j${n}`, { generation_id: item.generation_id })], engine_resolved: 'omnivoice', seed: 777 }
    }
    if (method === 'POST' && path === '/text/aerab') {
      const text = (b.text as string).replace('سلام', 'سَلام')
      return { text, marked: 1, kept: 0, low_coverage: 0, language: (b.language as string) ?? 'ur' }
    }
    if (path === '/media') return { items: [] }
    if (path === '/jobs') return opts.jobs?.() ?? []
    if (path.startsWith('/favourites')) return { refs: [] }
  })
}

const posts = (calls: ReturnType<typeof api>, path: string) => calls.filter((c) => c.method === 'POST' && c.path === path).map((c) => c.body)

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

const speakButton = () => screen.getByRole('button', { name: /^Speak\b.*Enter/ })

describe('Speak', () => {
  it('posts voice, engine, language, speed, tags and aerab for an Urdu line', async () => {
    const calls = api()
    renderAt('/audio/speak?voice=v1', routes)
    const user = userEvent.setup()

    // the linked voice brings its language
    expect(await screen.findByRole('button', { name: 'Language: Urdu' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Voice: Ayesha' })).toBeInTheDocument()

    const script = screen.getByRole('textbox', { name: 'Script' })
    // nothing to mark in Latin letters
    await user.click(script)
    await user.paste('Hello')
    expect(screen.queryByRole('button', { name: /Add aerab/ })).not.toBeInTheDocument()
    await user.clear(script)
    await user.paste('سلام دنیا')
    await user.click(screen.getByRole('button', { name: 'Insert tag' }))
    await user.click(await screen.findByRole('menuitem', { name: /pause 0\.5s/ }))
    expect(script).toHaveValue('سلام دنیا [pause 0.5s] ')
    expect(script).toHaveAccessibleDescription('23 / 5,000 characters')

    await user.click(screen.getByRole('button', { name: /Add aerab/ }))
    await waitFor(() => expect(script).toHaveValue('سَلام دنیا [pause 0.5s] '))
    expect(posts(calls, '/text/aerab')[0]).toEqual({ text: 'سلام دنیا [pause 0.5s] ', language: 'ur' })
    expect(screen.getByText('Aerab added to 1 line.')).toBeInTheDocument()
    // one step back, and forward again
    await user.click(screen.getByRole('button', { name: 'Undo aerab' }))
    expect(script).toHaveValue('سلام دنیا [pause 0.5s] ')
    await user.click(screen.getByRole('button', { name: /Add aerab/ }))
    await waitFor(() => expect(script).toHaveValue('سَلام دنیا [pause 0.5s] '))
    expect(screen.getByRole('switch', { name: /Aerab before speaking/ })).toBeChecked()

    await user.click(within(await openChip(user, 'Speed')).getByRole('button', { name: '1.25×' }))
    await closeChip(user)
    await user.click(within(await openChip(user, 'Count')).getByRole('radio', { name: '2 takes' }))
    await closeChip(user)
    await user.click(speakButton())

    await waitFor(() => expect(posts(calls, '/audio/speak')).toHaveLength(1))
    expect(posts(calls, '/audio/speak')[0]).toEqual({
      text: 'سَلام دنیا [pause 0.5s]',
      engine: 'auto',
      language: 'ur',
      voice_id: 'v1',
      aerab: true,
      speed: 1.25,
      count: 2,
      denoise: true,
    })
    expect(await screen.findByRole('listitem', { name: /^Request: سَلام/ })).toBeInTheDocument()
  })

  it('turning aerab off sends aerab: false', async () => {
    const calls = api()
    renderAt('/audio/speak?voice=v1', routes)
    const user = userEvent.setup()
    await screen.findByRole('button', { name: 'Language: Urdu' })
    await user.click(screen.getByRole('textbox', { name: 'Script' }))
    await user.paste('آج بارش ہے')
    await user.click(screen.getByRole('switch', { name: /Aerab before speaking/ }))
    await user.keyboard('{Control>}{Enter}{/Control}')
    await waitFor(() => expect(posts(calls, '/audio/speak')[0]).toMatchObject({ aerab: false, language: 'ur' }))
    // queued: the script box empties for the next line
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'Script' })).toHaveValue(''))
  })

  it('disables an engine that does not speak the language, and says why', async () => {
    api()
    renderAt('/audio/speak', routes)
    const user = userEvent.setup()
    await screen.findByRole('button', { name: /^Engine: Auto/ })
    await user.click(within(await openChip(user, 'Language')).getByRole('button', { name: /^Urdu/ }))
    await closeChip(user)

    const pop = await openChip(user, 'Engine')
    const chatterbox = within(pop).getByRole('radio', { name: 'Chatterbox' })
    expect(chatterbox).toBeDisabled()
    expect(chatterbox).toHaveAccessibleDescription(/Chatterbox doesn't speak Urdu\. Use OmniVoice\./)
    expect(within(pop).getByRole('radio', { name: 'OmniVoice' })).toBeEnabled()
    expect(within(pop).getByRole('radio', { name: 'OmniVoice' })).not.toHaveAccessibleDescription(/Apache|licen/i)
  })

  it('warns that tags are stripped on an engine without them', async () => {
    api()
    renderAt('/audio/speak', routes)
    const user = userEvent.setup()
    await screen.findByRole('button', { name: /^Engine: Auto/ })
    await user.click(within(await openChip(user, 'Engine')).getByRole('radio', { name: 'Chatterbox' }))
    await closeChip(user)
    expect(screen.getByRole('button', { name: 'Engine: Chatterbox' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Insert tag' }))
    expect(await screen.findByText(/Chatterbox: tags are removed; pauses become silence/)).toBeInTheDocument()
    await user.click(screen.getByRole('menuitem', { name: /\[laughter\]/ }))
    expect(screen.getByText(/Chatterbox can't perform expression tags/)).toBeInTheDocument()
  })

  it('A/B posts the same line twice and keeps the chosen side', async () => {
    const calls = api()
    renderAt('/audio/speak', routes)
    const user = userEvent.setup()
    await screen.findByRole('button', { name: /^Engine: Auto/ })
    await user.click(screen.getByRole('textbox', { name: 'Script' }))
    await user.paste('Which one sounds right?')
    await user.click(screen.getByRole('button', { name: 'Compare' }))
    expect(screen.getByRole('button', { name: 'Compare' })).toHaveAttribute('aria-pressed', 'true')
    // B starts identical to A, so it can't run yet
    expect(speakButton()).toBeDisabled()

    await user.click(within(await openChip(user, 'Voice B')).getByRole('button', { name: /^Old Narrator/ }))
    await closeChip(user)
    await user.click(speakButton())

    await waitFor(() => expect(posts(calls, '/audio/speak')).toHaveLength(2))
    const [a, b] = posts(calls, '/audio/speak') as Record<string, unknown>[]
    expect(a).not.toHaveProperty('voice_id')
    expect(b).toMatchObject({ voice_id: 'v2', text: 'Which one sounds right?', engine: 'auto' })

    const board = await screen.findByRole('region', { name: 'A/B compare' })
    expect(within(board).getByText('Old Narrator · OmniVoice')).toBeInTheDocument()
    await user.click(within(board).getByRole('button', { name: 'Keep B' }))
    expect(within(board).getByRole('button', { name: 'Keep B' })).toHaveAttribute('aria-pressed', 'true')
    expect(within(board).getByRole('button', { name: 'Keep A' })).toHaveAttribute('aria-pressed', 'false')
  })
})

describe('Voices', () => {
  it('lists voices with kind, language and uses, and filters by starred', async () => {
    const calls = api()
    renderAt('/audio/voices', routes)
    const user = userEvent.setup()
    const list = await screen.findByRole('list', { name: 'Voices' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(3)
    expect(within(list).getByText('12 uses')).toBeInTheDocument()
    expect(within(list).getByRole('button', { name: "Play Ayesha's reference" })).toBeInTheDocument()
    // designed and preset voices have no clip to play
    expect(within(list).queryByRole('button', { name: "Play Old Narrator's reference" })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Starred' }))
    await waitFor(() => expect(calls.some((c) => c.path === '/voices' && c.query.starred === 'true')).toBe(true))
    const starred = await screen.findByRole('list', { name: 'Voices' })
    await user.click(within(starred).getByRole('button', { name: 'Star Old Narrator' }))
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')).toMatchObject({ path: '/voices/v2', body: { starred: true } }))
  })

  it('clone: upload, length warning, and Create only once consent is given, with the exact statement', async () => {
    const calls = api({ created: (b) => voice('v9', { name: b.name as string, warnings: ['short'] }) })
    fakeXhr(() => ({ status: 200, body: media('a9', { kind: 'audio', title: 'ref.wav', duration_s: 4.2 }) }))
    renderAt('/audio/voices?add=1', routes)
    const user = userEvent.setup()
    const dialog = await screen.findByRole('dialog', { name: 'Add a voice' })
    const create = within(dialog).getByRole('button', { name: 'Create voice' })
    expect(create).toBeDisabled()

    await user.type(within(dialog).getByRole('textbox', { name: 'Voice name' }), 'Bilal')
    await user.upload(within(dialog).getByLabelText('Audio file'), new File(['x'], 'ref.wav', { type: 'audio/wav' }))
    expect(await within(dialog).findByText(/Under 5 seconds/)).toBeInTheDocument()
    expect(within(dialog).getByText('0:04')).toBeInTheDocument()
    expect(create).toBeDisabled()

    await user.type(within(dialog).getByRole('textbox', { name: 'Speaker’s name' }), 'Bilal Khan')
    expect(create).toBeDisabled()
    expect(within(dialog).getByText('Tick the consent statement to clone this voice.')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('checkbox', { name: CONSENT_STATEMENT }))
    expect(create).toBeEnabled()
    await user.click(create)

    await waitFor(() => expect(posts(calls, '/voices')).toHaveLength(1))
    expect(posts(calls, '/voices')[0]).toEqual({
      name: 'Bilal',
      language: 'en',
      kind: 'clone',
      ref_media_id: 'a9',
      consent: { name: 'Bilal Khan', statement: 'I have the right to use this voice, and the person it belongs to agreed to it being cloned.' },
    })
    const done = await screen.findByRole('dialog', { name: 'Bilal is ready' })
    // the server's own warning, after the voice exists
    expect(within(done).getByText(/Under 5 seconds/)).toBeInTheDocument()
  })

  it('refuses a clip outside 3–30 s before it is ever sent', async () => {
    api()
    fakeXhr(() => ({ status: 200, body: media('a8', { kind: 'audio', duration_s: 42 }) }))
    renderAt('/audio/voices?add=1', routes)
    const user = userEvent.setup()
    const dialog = await screen.findByRole('dialog', { name: 'Add a voice' })
    await user.type(within(dialog).getByRole('textbox', { name: 'Voice name' }), 'Long one')
    await user.upload(within(dialog).getByLabelText('Audio file'), new File(['x'], 'long.mp3', { type: 'audio/mpeg' }))
    expect(await within(dialog).findAllByText(/a reference can be up to 30 s/)).not.toHaveLength(0)
    expect(within(dialog).getByRole('button', { name: 'Create voice' })).toBeDisabled()
  })

  it('Transcribe fills the transcript once the job finishes, and Done saves it', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })  // so the 1 s job poll can be skipped below
    let polls = 0
    const calls = api({
      jobs: () => {
        polls += 1
        return [job('t1', { type: 'voice_transcribe', status: polls > 1 ? 'done' : 'running', progress: 0.5, result: polls > 1 ? { ref_text: 'Salaam, aap kaise hain?' } : null })]
      },
    })
    fakeXhr(() => ({ status: 200, body: media('a9', { kind: 'audio', duration_s: 8 }) }))
    renderAt('/audio/voices?add=1', routes)
    const user = userEvent.setup()
    const dialog = await screen.findByRole('dialog', { name: 'Add a voice' })
    await user.type(within(dialog).getByRole('textbox', { name: 'Voice name' }), 'Ammi')
    await user.upload(within(dialog).getByLabelText('Audio file'), new File(['x'], 'ref.wav', { type: 'audio/wav' }))
    await within(dialog).findByText('0:08')
    // can't transcribe a voice that doesn't exist yet
    expect(within(dialog).getByRole('button', { name: 'Transcribe' })).toBeDisabled()
    await user.type(within(dialog).getByRole('textbox', { name: 'Speaker’s name' }), 'Ammi')
    await user.click(within(dialog).getByRole('checkbox', { name: CONSENT_STATEMENT }))
    await user.click(within(dialog).getByRole('button', { name: 'Create voice' }))

    const done = await screen.findByRole('dialog', { name: 'Ammi is ready' })
    await user.click(within(done).getByRole('button', { name: 'Transcribe' }))
    expect(posts(calls, '/voices/v9/transcribe')).toHaveLength(1)
    await act(() => vi.advanceTimersByTimeAsync(1000))
    await waitFor(() => expect(within(done).getByRole('textbox', { name: 'Reference transcript' })).toHaveValue('Salaam, aap kaise hain?'))

    await user.click(within(done).getByRole('button', { name: 'Done' }))
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')).toMatchObject({ path: '/voices/v9', body: { ref_text: 'Salaam, aap kaise hain?' } }))
  })

  it('the detail sheet stars, locks, links to Speak and deletes after a warning', async () => {
    const calls = api()
    renderAt('/audio/voices', routes)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Open Ayesha' }))
    const sheet = await screen.findByRole('dialog', { name: 'Ayesha' })
    expect(within(sheet).getByText(/Consent from Ayesha/)).toBeInTheDocument()
    await user.click(within(sheet).getByRole('switch', { name: /Locked/ }))
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')).toMatchObject({ body: { locked: true } }))

    await user.click(within(sheet).getByRole('button', { name: 'Delete voice' }))
    const confirm = await screen.findByRole('alertdialog', { name: 'Delete Ayesha?' })
    expect(confirm).toHaveTextContent('Its reference clip stays in the Library')
    await user.click(within(confirm).getByRole('button', { name: 'Delete voice' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE' && c.path === '/voices/v1')).toBe(true))
  })

  it('"Speak with this voice" opens Speak with the voice picked', async () => {
    api()
    renderAt('/audio/voices', routes)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Open Ayesha' }))
    await user.click(await screen.findByRole('link', { name: 'Speak with this voice' }))
    expect(screen.getByTestId('location')).toHaveTextContent('/audio/speak?voice=v1')
    expect(await screen.findByRole('button', { name: 'Voice: Ayesha' })).toBeInTheDocument()
  })
})

describe('Recorder', () => {
  const openRecorder = async (user: ReturnType<typeof userEvent.setup>) => {
    const dialog = await screen.findByRole('dialog', { name: 'Add a voice' })
    await user.click(within(dialog).getByRole('radio', { name: 'Record' }))
    return dialog
  }

  it('explains a blocked microphone instead of failing silently', async () => {
    api()
    vi.stubGlobal('MediaRecorder', class {})
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: vi.fn(() => Promise.reject(new DOMException('denied', 'NotAllowedError'))) },
    })
    renderAt('/audio/voices?add=1', routes)
    const user = userEvent.setup()
    const dialog = await openRecorder(user)
    await user.click(within(dialog).getByRole('button', { name: 'Record' }))
    expect(await within(dialog).findByRole('status')).toHaveTextContent(/Microphone access is blocked/)
  })

  it('records, stops and announces each step; a take under 3 s cannot be used', async () => {
    api()
    const stopTrack = vi.fn()
    class FakeRecorder {
      state = 'inactive'
      mimeType = 'audio/webm;codecs=opus'
      ondataavailable: ((e: { data: Blob }) => void) | null = null
      onstop: (() => void) | null = null
      start() {
        this.state = 'recording'
      }
      stop() {
        this.state = 'inactive'
        this.ondataavailable?.({ data: new Blob(['abc']) })
        this.onstop?.()
      }
    }
    vi.stubGlobal('MediaRecorder', FakeRecorder)
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: vi.fn(() => Promise.resolve({ getTracks: () => [{ stop: stopTrack }] })) },
    })
    Object.defineProperty(URL, 'createObjectURL', { value: vi.fn(() => 'blob:take'), configurable: true, writable: true })
    Object.defineProperty(URL, 'revokeObjectURL', { value: vi.fn(), configurable: true, writable: true })
    renderAt('/audio/voices?add=1', routes)
    const user = userEvent.setup()
    const dialog = await openRecorder(user)
    await user.click(within(dialog).getByRole('button', { name: 'Record' }))
    expect(await within(dialog).findByRole('status')).toHaveTextContent('Recording. Speak now')
    await user.click(within(dialog).getByRole('button', { name: 'Stop' }))
    expect(within(dialog).getByRole('status')).toHaveTextContent(/Recording stopped/)
    expect(stopTrack).toHaveBeenCalled()
    expect(within(dialog).getByRole('button', { name: 'Use this recording' })).toBeDisabled()
    expect(within(dialog).getByRole('button', { name: 'Record again' })).toBeInTheDocument()
  })
})

describe('Design', () => {
  it('preview from a preset, keep the seed, then save as a designed voice', async () => {
    const calls = api()
    renderAt('/audio/design', routes)
    const user = userEvent.setup()
    await screen.findByRole('button', { name: 'Engine: OmniVoice' })
    await user.click(screen.getByRole('button', { name: 'Elder' }))
    const description = screen.getByRole('textbox', { name: 'Describe the voice' })
    expect(description).toHaveValue('male, elderly, low pitch, slow')

    await user.click(screen.getByRole('button', { name: /^Preview\b.*Enter/ }))
    await waitFor(() => expect(posts(calls, '/voices/design/preview')).toHaveLength(1))
    expect(posts(calls, '/voices/design/preview')[0]).toEqual({
      description: 'male, elderly, low pitch, slow',
      language: 'en',
      engine: 'omnivoice',
      text: 'The quick brown fox jumps over the lazy dog.',
    })
    const row = await screen.findByRole('listitem', { name: 'Preview 1, seed 777' })
    expect(screen.getByRole('textbox', { name: 'Seed' })).toHaveValue('777')

    // keep the identity while the description changes
    await user.click(screen.getByRole('button', { name: 'Keep this voice' }))
    await user.type(description, ', Lahori')
    await user.click(screen.getByRole('button', { name: /^Preview\b.*Enter/ }))
    await waitFor(() => expect(posts(calls, '/voices/design/preview')).toHaveLength(2))
    expect(posts(calls, '/voices/design/preview')[1]).toMatchObject({ seed: 777, description: expect.stringMatching(/Lahori$/) })

    await user.click(within(row).getByRole('button', { name: 'Save as voice' }))
    const dialog = await screen.findByRole('dialog', { name: 'Save as voice' })
    await user.type(within(dialog).getByRole('textbox', { name: 'Voice name' }), 'Dada jaan')
    await user.click(within(dialog).getByRole('button', { name: 'Save voice' }))
    await waitFor(() => expect(posts(calls, '/voices')).toHaveLength(1))
    expect(posts(calls, '/voices')[0]).toEqual({
      name: 'Dada jaan',
      language: 'en',
      kind: 'design',
      design: { description: 'male, elderly, low pitch, slow', seed: 777, engine: 'omnivoice' },
    })
    expect(await screen.findByRole('dialog', { name: 'Dada jaan is saved' })).toBeInTheDocument()
  })

  it('only offers the engines that can design', async () => {
    api()
    renderAt('/audio/design', routes)
    const user = userEvent.setup()
    await screen.findByRole('button', { name: 'Engine: OmniVoice' })
    const pop = await openChip(user, 'Engine')
    expect(within(pop).getAllByRole('radio').map((r) => r.getAttribute('aria-label'))).toEqual(['OmniVoice', 'VoxCPM2'])
  })
})
