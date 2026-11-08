import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TooltipProvider } from '@/components/ui/tooltip'
import { qk } from '@/hooks/keys'
import { peaksOf, stubCanvas, stubMedia } from '@/test/audio-fixtures'
import { job, mockApi, systemOk } from '@/test/media-fixtures'
import { resetPlayer, togglePlay, usePlayer } from '../sharedAudio'
import { AudioWorkspace } from './AudioWorkspace'

function renderWorkspace(path = '/audio/song') {
  const calls = mockApi((_m, p) => {
    if (p === '/system/status') return systemOk
    if (p === '/audio/m1/peaks') return peaksOf(92)
    if (p === '/jobs') return []
  })
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })
  qc.setQueryData(qk.jobs, [
    job('j1', { lane: 'audio', status: 'running', gpu: 'GPU 1' }),
    job('j2', { lane: 'audio', status: 'queued' }),
    job('j3', { type: 'audio_render', lane: 'general', status: 'queued' }),
    job('j4', { lane: 'video', status: 'running' }),
  ])
  const page = (name: string) => (
    <main>
      <h1>{name}</h1>
      <input aria-label="Prompt" />
    </main>
  )
  const router = createMemoryRouter(
    [
      {
        path: '/audio',
        element: <AudioWorkspace />,
        children: [
          { path: 'song', element: page('Song page') },
          { path: 'music', element: page('Music page') },
          { path: 'sfx', element: page('SFX page') },
          { path: 'speak', element: page('Speak page') },
          { path: 'edit/:id', element: page('Editor page') },
        ],
      },
    ],
    { initialEntries: [path] },
  )
  render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <RouterProvider router={router} />
      </TooltipProvider>
    </QueryClientProvider>,
  )
  return { router, calls }
}

beforeEach(() => {
  stubCanvas()
  stubMedia()
  resetPlayer()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('Audio workspace', () => {
  it('groups the sidebar, every page live', async () => {
    renderWorkspace()
    const nav = await screen.findByRole('navigation', { name: 'Audio Studio' })
    expect(within(nav).getAllByRole('list').map((l) => l.getAttribute('aria-label'))).toEqual(['Create', 'Voice', 'Story', 'Edit', 'Tools', 'Library'])
    expect(within(nav).getByRole('link', { name: 'Song' })).toHaveAttribute('aria-current', 'page')
    expect(within(nav).queryAllByRole('link').filter((l) => l.getAttribute('aria-disabled') === 'true')).toEqual([])
    // contract v21
    expect(within(nav).getByRole('link', { name: /^Stems/ })).toHaveAttribute('href', '/audio/stems')
    expect(within(nav).getByRole('link', { name: 'Score a Clip' })).toHaveAttribute('href', '/audio/score')
    // contract v43: Remix sits in the sidebar too, not only the Audio Tools menu
    expect(within(within(nav).getByRole('list', { name: 'Edit' })).getByRole('link', { name: /^Remix/ })).toHaveAttribute('href', '/audio/remix')
    // B2: the Voice group is live
    expect(within(nav).getByRole('link', { name: 'Speak' })).toHaveAttribute('href', '/audio/speak')
    expect(within(nav).getByRole('link', { name: 'Voices' })).toHaveAttribute('href', '/audio/voices')
    expect(within(nav).getByRole('link', { name: 'Design' })).toHaveAttribute('href', '/audio/design')
    // B3
    expect(within(nav).getByRole('link', { name: /^Dialogue/ })).toHaveAttribute('href', '/audio/dialogue')
    // B4
    expect(within(nav).getByRole('link', { name: /^Transcribe/ })).toHaveAttribute('href', '/audio/transcribe')
    expect(within(nav).getByRole('link', { name: /^Dub/ })).toHaveAttribute('href', '/audio/dub')
  })

  it('folds a group, and collapses to an icon rail that it remembers', async () => {
    const user = userEvent.setup()
    renderWorkspace()
    const nav = await screen.findByRole('navigation', { name: 'Audio Studio' })
    await user.click(within(nav).getByRole('button', { name: 'Voice' }))
    expect(within(nav).queryByRole('list', { name: 'Voice' })).not.toBeInTheDocument()

    await user.click(within(nav).getByRole('button', { name: 'Collapse the audio sidebar to icons' }))
    expect(within(nav).getByRole('button', { name: 'Expand the audio sidebar' })).toHaveAttribute('aria-expanded', 'false')
    expect(JSON.parse(localStorage.getItem('mixai.audio.sidebar')!)).toMatchObject({ rail: true, groups: { voice: false } })

    cleanup()
    renderWorkspace()
    expect(await screen.findByRole('button', { name: 'Expand the audio sidebar' })).toBeInTheDocument()
  })

  it('Ctrl+2 switches page, but not while typing; ? opens the shortcut sheet', async () => {
    const user = userEvent.setup()
    const { router } = renderWorkspace()
    await screen.findByRole('heading', { name: 'Song page' })

    await user.click(screen.getByRole('textbox', { name: 'Prompt' }))
    await user.keyboard('{Control>}2{/Control}')
    expect(router.state.location.pathname).toBe('/audio/song')

    await user.click(document.body)
    await user.keyboard('{Control>}2{/Control}')
    expect(router.state.location.pathname).toBe('/audio/music')
    expect(await screen.findByRole('heading', { name: 'Music page' })).toBeInTheDocument()

    await user.keyboard('?')
    const sheet = await screen.findByRole('dialog', { name: 'Audio shortcuts' })
    // the first nine pages get Ctrl+1…9; Gallery (Voice) is among them
    expect(within(sheet).getByText('Gallery')).toBeInTheDocument()
    expect(within(sheet).getByText('Cut the region')).toBeInTheDocument()
    expect(within(sheet).getByText('Speak the script (Design: preview)')).toBeInTheDocument()
  })

  it('numbers the pages in sidebar order, Voice included', async () => {
    const user = userEvent.setup()
    const { router } = renderWorkspace()
    await screen.findByRole('heading', { name: 'Song page' })
    await user.keyboard('{Control>}4{/Control}')
    expect(router.state.location.pathname).toBe('/audio/speak')
    expect(await screen.findByRole('heading', { name: 'Speak page' })).toBeInTheDocument()
  })

  it('the transport and status strip follow the shared player and the audio lane', async () => {
    const user = userEvent.setup()
    renderWorkspace()
    const transport = await screen.findByRole('region', { name: 'Transport' })
    expect(within(transport).getByRole('button', { name: 'Play (nothing loaded)' })).toBeDisabled()
    // the audio lane plus the editor's renders; video work isn't counted
    expect(screen.getByRole('link', { name: /Audio queue: 1 running, 2 waiting on GPU 1/ })).toBeInTheDocument()
    expect(await screen.findByText('LLM offline')).toBeInTheDocument()

    act(() => togglePlay('m1', '/media/m1.flac', { title: 'Rain take', mediaId: 'm1' }))
    expect(within(transport).getByTestId('transport-title')).toHaveTextContent('Rain take')
    const pause = within(transport).getByRole('button', { name: 'Pause Rain take' })
    expect(within(transport).getByRole('link', { name: 'Open in Editor' })).toHaveAttribute('href', '/audio/edit/m1')

    act(() => usePlayer.setState({ duration: 92, time: 30 }))
    expect(within(transport).getByRole('slider', { name: 'Position' })).toHaveAttribute('aria-valuetext', '0:30 of 1:32')

    await user.click(within(transport).getByRole('button', { name: 'Loop' }))
    expect(usePlayer.getState().loop).toBe(true)
    await user.click(within(transport).getByRole('button', { name: 'Mute' }))
    expect(usePlayer.getState().volume).toBe(0)
    await user.click(pause)
    expect(usePlayer.getState().playing).toBe(false)

    await user.click(within(transport).getByRole('link', { name: 'Open in Editor' }))
    expect(await screen.findByRole('heading', { name: 'Editor page' })).toBeInTheDocument()
  })
})
