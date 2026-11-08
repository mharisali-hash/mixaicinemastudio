import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { TooltipProvider } from '@/components/ui/tooltip'
import { qk } from '@/hooks/keys'
import { shellRoutes } from '@/router'
import type { Project } from '@/lib/types'
import { mockApi, systemOk, T } from '@/test/media-fixtures'
import { AppShell } from './AppShell'

afterEach(() => vi.unstubAllGlobals())

// the workspace route is lazy and pulls in most of the studio; a cold import of it can take
// several seconds on a busy run, so pay for it here rather than inside a test's findBy wait
beforeAll(
  () => Promise.all([import('@/features/workspace/WorkspacePage'), import('@/features/podcast/PodcastFrame'), import('@/features/podcast/PodcastListPage')]),
  30_000,
)

const project: Project = {
  id: 'p1',
  title: 'The Bleaching Reef',
  logline: 'A diver watches a reef turn white.',
  authoring_mode: 'scene_by_scene',
  aspect_ratio: '16:9',
  target_runtime_s: 120,
  quality: 'draft',
  takes_per_shot: 2,
  overnight: false,
  status: 'draft',
  created_at: T,
  updated_at: T,
  counts: { scenes: 0, shots: 0, characters: 0 },
}

function renderApp(path: string) {
  const calls = mockApi((_m, p) => {
    if (p === '/system/status') return systemOk
    if (p === '/projects') return [project]
    if (p === '/projects/p1') return project
    if (p === '/auth/me') return { id: 'u', email: 'ada@studio.test', display_name: 'Ada', workspace_id: 'w1', role: 'owner' }
    if (p === '/media') return { items: [] }
    if (p === '/quick/recent') return []
    if (p === '/prompt-templates') return []
    if (p === '/podcast/shows' || p === '/podcast/episodes' || p === '/voices/engines') return []
  })
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })
  qc.setQueryData(qk.jobs, [])
  // the real route table, minus the auth gate
  const router = createMemoryRouter([{ element: <AppShell />, children: shellRoutes }], { initialEntries: [path] })
  render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <RouterProvider router={router} />
      </TooltipProvider>
    </QueryClientProvider>,
  )
  return { router, calls }
}

describe('app shell', () => {
  it.each([
    ['/image/templates', '/image/prompt-templates', 'Image prompt templates'],
    ['/video/templates', '/video/prompt-templates', 'Video prompt templates'],
    // Podcast Studio left Audio for a rail entry of its own
    ['/audio/podcast', '/podcast', 'Podcast Studio'],
  ])('the old %s address lands on %s', async (from, to, title) => {
    const { router } = renderApp(from)
    expect(await screen.findByRole('heading', { name: title }, { timeout: 5000 })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe(to)
    expect(screen.getAllByTestId('page-title')[0]).toHaveTextContent(title)
  })

  it.each([
    ['/audio/podcast/e1', '/podcast/e1'],
    ['/audio/podcast/e1/video', '/podcast/e1'],
    ['/podcast/e1/video', '/podcast/e1'],
  ])('the old episode address %s lands on %s', async (from, to) => {
    const { router } = renderApp(from)
    await waitFor(() => expect(router.state.location.pathname).toBe(to), { timeout: 5000 })
  })

  it('outside a project the top bar shows the page title and the page’s own actions', async () => {
    renderApp('/video/projects')
    const banner = (await screen.findAllByRole('banner', {}, { timeout: 5000 }))[0]
    expect(within(banner).getByTestId('page-title')).toHaveTextContent('Studio projects')
    // ProjectsPage portals its primary action into the top bar
    expect(await within(banner).findByRole('button', { name: 'New project' })).toBeInTheDocument()
    expect(within(banner).queryByRole('tablist')).not.toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: 'Main' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Projects' })).toBeInTheDocument()
  })

  it('inside a project it switches to the project switcher, stage tabs and queue drawer button', async () => {
    renderApp('/projects/p1/script')
    const banner = (await screen.findAllByRole('banner'))[0]
    expect(await within(banner).findByRole('button', { name: /Switch project/ })).toHaveTextContent('The Bleaching Reef')
    expect(within(banner).getByRole('tablist', { name: 'Production stages' })).toBeInTheDocument()
    expect(within(banner).getByRole('button', { name: /^Queue:/ })).toBeInTheDocument()
    expect(within(banner).queryByTestId('page-title')).not.toBeInTheDocument()
    // the rail stays, with Video lit for studio work
    expect(within(screen.getByRole('navigation', { name: 'Main' })).getByRole('button', { name: 'Video' })).toHaveAttribute('aria-current', 'true')
  })

  it('keeps the old /projects and /create addresses working', async () => {
    const { router } = renderApp('/projects')
    expect(await screen.findByRole('heading', { name: 'Projects' }, { timeout: 5000 })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/video/projects')

    await router.navigate('/create')
    expect(await screen.findByRole('heading', { name: 'Quick Create' }, { timeout: 5000 })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/video/quick')
  })

  it('opens Home at /', async () => {
    renderApp('/')
    expect(await screen.findByRole('radio', { name: /Video/ }, { timeout: 5000 })).toBeInTheDocument()
    expect(screen.getByTestId('page-title')).toHaveTextContent('Home')
    expect(await screen.findByRole('heading', { name: 'Continue working' })).toBeInTheDocument()
  })
})
