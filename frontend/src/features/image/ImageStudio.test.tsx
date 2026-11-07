import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { addSources, editPayload, imagePayload, DEFAULT_IMAGE_FORM, placeholdersIn, splitPlaceholders } from '@/lib/images'
import { job, media, mockApi, renderAt } from '@/test/media-fixtures'
import { closeChip, openChip } from '@/test/dock'
import { STYLE_OPTIONS } from '@/test/style-fixtures'
import { ImageEditPage } from './ImageEditPage'
import { ImageGeneratePage } from './ImageGeneratePage'
import { ImageReferencesPage } from './ImageReferencesPage'

afterEach(() => vi.unstubAllGlobals())

describe('image payloads', () => {
  it('builds the generate body: trims, clamps count, drops empty extras', () => {
    expect(imagePayload({ ...DEFAULT_IMAGE_FORM, prompt: '  a fox  ', count: 9, seed: '', steps: '' })).toEqual({ prompt: 'a fox', aspect: '1:1', count: 4 })
    expect(imagePayload({ ...DEFAULT_IMAGE_FORM, prompt: 'x', count: 0, seed: '7', steps: '80', negative: ' blur ', style: 'anime' })).toEqual({
      prompt: 'x',
      aspect: '1:1',
      count: 1,
      seed: 7,
      steps: 50,
      negative: 'blur',
      style: 'anime',
    })
  })

  it('never sends more than three edit sources, and no duplicates', () => {
    expect(addSources(['a', 'b'], ['b', 'c', 'd', 'e'])).toEqual({ ids: ['a', 'b', 'c'], dropped: 2 })
    expect(editPayload(['a', 'a', 'b', 'c', 'd'], ' night ', 2, null)).toEqual({ source_ids: ['a', 'b', 'c'], instruction: 'night', count: 2 })
  })

  it('finds [placeholders] for templates', () => {
    expect(placeholdersIn('A [product] on [surface], [product] again')).toEqual(['[product]', '[surface]'])
    expect(splitPlaceholders('Hi [name]!')).toEqual([
      { text: 'Hi ', slot: false },
      { text: '[name]', slot: true },
      { text: '!', slot: false },
    ])
  })
})

describe('Create Image page', () => {
  it('sends prompt, aspect, count, style and advanced fields with Ctrl+Enter, and the batch fills the grid', async () => {
    const created = [media('n1', { media_url: null, title: 'Lighthouse 1' }), media('n2', { media_url: null, title: 'Lighthouse 2' })]
    const calls = mockApi((method, path) => {
      if (path === '/media') return { items: [media('old', { title: 'Older image' })] }
      if (path === '/prompt-templates') return []
      if (path === '/images/options') return STYLE_OPTIONS
      if (method === 'POST' && path === '/images/generate') return { items: created, jobs: [job('j1', { generation_id: 'g-n1' }), job('j2', { generation_id: 'g-n2' })] }
    })
    renderAt('/image/generate?model=z-image-turbo', [{ path: '/image/generate', element: <ImageGeneratePage /> }])
    const user = userEvent.setup()

    expect(await screen.findByRole('img', { name: 'Older image' })).toBeInTheDocument()
    // paste rather than type: the form re-renders its pickers per keystroke, which is slow under jsdom
    await user.click(screen.getByRole('textbox', { name: 'Describe the image' }))
    await user.paste('A lighthouse keeper at dusk')
    await user.click(within(await openChip(user, 'Aspect')).getByRole('radio', { name: /16:9 Wide/ }))
    await closeChip(user)
    await user.click(within(await openChip(user, 'Count')).getByRole('radio', { name: '4 images' }))
    await closeChip(user)
    await user.click(within(await openChip(user, 'Style')).getByRole('radio', { name: 'Cinematic, Film still' }))
    await closeChip(user)
    expect(screen.getByRole('button', { name: 'Style: Cinematic' })).toBeInTheDocument()
    await user.click(screen.getByRole('radio', { name: 'Advanced' }))
    await user.type(screen.getByRole('textbox', { name: 'Seed' }), '42')
    await user.type(screen.getByRole('textbox', { name: /Avoid/ }), 'text')
    await user.keyboard('{Control>}{Enter}{/Control}')

    const post = calls.find((c) => c.method === 'POST')
    expect(post).toMatchObject({
      path: '/images/generate',
      body: { prompt: 'A lighthouse keeper at dusk', aspect: '16:9', count: 4, style: 'cinematic', seed: 42, negative: 'text' },
    })
    // the request becomes the top row; its tiles wait in the queue (with their place) until SSE delivers them
    const results = screen.getByRole('region', { name: 'Results' })
    const rows = await within(results).findAllByRole('listitem', { name: /^Request:/ })
    expect(rows.map((r) => r.getAttribute('aria-label'))).toEqual(['Request: A lighthouse keeper at dusk', 'Request: Older image'])
    expect(within(rows[0]).getByText(/Z-Image Turbo · 16:9 · 4 images · Cinematic/)).toBeInTheDocument()
    expect(within(rows[0]).getByText('Queued, next')).toBeInTheDocument()
    expect(within(rows[0]).getByText('Queued, 2nd in line')).toBeInTheDocument()
    expect(within(rows[1]).getByRole('group', { name: 'Actions for Older image' })).toBeInTheDocument()
  })

  it('offers review actions on a finished image', async () => {
    const calls = mockApi((method, path) => {
      if (path === '/media') return { items: [media('m1', { title: 'Fox' })] }
      if (path === '/prompt-templates') return []
      if (method === 'POST' && path === '/media/m1/regenerate') return job('r1')
    })
    renderAt('/image/generate', [{ path: '/image/generate', element: <ImageGeneratePage /> }])
    const user = userEvent.setup()
    const bar = await screen.findByRole('group', { name: 'Actions for Fox' })
    expect(within(bar).getByRole('link', { name: 'Download Fox' })).toHaveAttribute('href', '/api/generations/g-m1/download')
    await user.click(within(bar).getByRole('button', { name: 'More for Fox' }))
    await user.click(await screen.findByRole('menuitem', { name: /Regenerate: same prompt/ }))
    expect(calls).toContainEqual(expect.objectContaining({ method: 'POST', path: '/media/m1/regenerate', body: { mode: 'same' } }))

    await user.click(within(bar).getByRole('button', { name: 'Edit Fox' }))
    expect(screen.getByTestId('location')).toHaveTextContent('/image/edit?sources=m1')
  })
})

describe('Edit Image page', () => {
  it('keeps at most three sources from the URL and posts the edit', async () => {
    const calls = mockApi((method, path) => {
      const id = path.match(/^\/media\/(\w+)$/)?.[1]
      if (id) return { ...media(id), versions: [] }
      if (path === '/media') return { items: [] }
      if (method === 'POST' && path === '/images/edit') return { items: [media('e1', { media_url: null })], jobs: [] }
    })
    renderAt('/image/edit?sources=a,b,c,d', [{ path: '/image/edit', element: <ImageEditPage /> }])
    const user = userEvent.setup()

    const chosen = screen.getByRole('list', { name: 'Chosen sources' })
    expect(within(chosen).getAllByRole('listitem')).toHaveLength(3)
    expect(screen.getByText('Source images (3/3)')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Add/ })).not.toBeInTheDocument()

    await user.click(await within(chosen).findByRole('button', { name: 'Remove Image c' }))
    expect(within(chosen).getAllByRole('listitem')).toHaveLength(2)
    expect(screen.getByRole('button', { name: 'Add another source' })).toBeEnabled()

    await user.type(screen.getByRole('textbox', { name: 'What should change?' }), 'Put them on a boat')
    await user.click(within(await openChip(user, 'Count')).getByRole('radio', { name: '2 images' }))
    await closeChip(user)
    await user.click(screen.getByRole('button', { name: /^Edit/ }))
    expect(calls.find((c) => c.method === 'POST')).toMatchObject({
      path: '/images/edit',
      // prompt correction is on Auto unless changed, and an edit always says which
      body: { source_ids: ['a', 'b'], instruction: 'Put them on a boat', count: 2, magic_prompt: 'auto' },
    })
    // queued: a fresh start, with no words and no pictures, and the URL no longer brings them back
    expect(screen.getByRole('textbox', { name: 'What should change?' })).toHaveValue('')
    expect(screen.queryByRole('list', { name: 'Chosen sources' })).not.toBeInTheDocument()
    await waitFor(() => expect(screen.getByTestId('location')).not.toHaveTextContent('sources='))
  })

  it('sends prompt correction off when it is turned off', async () => {
    const calls = mockApi((method, path) => {
      const id = path.match(/^\/media\/(\w+)$/)?.[1]
      if (id) return { ...media(id), versions: [] }
      if (path === '/media') return { items: [] }
      if (method === 'POST' && path === '/images/edit') return { items: [media('e1', { media_url: null })], jobs: [] }
    })
    renderAt('/image/edit?sources=a', [{ path: '/image/edit', element: <ImageEditPage /> }])
    const user = userEvent.setup()
    await user.selectOptions(screen.getByRole('combobox', { name: 'Prompt correction' }), 'Off: use my prompt exactly')
    await user.type(screen.getByRole('textbox', { name: 'What should change?' }), 'Make it night')
    await user.click(screen.getByRole('button', { name: /^Edit/ }))
    expect(calls.find((c) => c.method === 'POST' && c.path === '/images/edit')?.body).toMatchObject({ instruction: 'Make it night', magic_prompt: 'off' })
  })

  it('needs a source before it can edit', async () => {
    mockApi((_m, path) => (path === '/media' ? { items: [] } : undefined))
    renderAt('/image/edit', [{ path: '/image/edit', element: <ImageEditPage /> }])
    const user = userEvent.setup()
    await user.type(screen.getByRole('textbox', { name: 'What should change?' }), 'Make it night')
    expect(screen.getByRole('button', { name: /^Edit/ })).toBeDisabled()
    expect(screen.getByText('Add at least one source image.')).toBeInTheDocument()
  })
})

describe('References page', () => {
  it('picks up to three images and opens Edit with them preloaded', async () => {
    mockApi((_m, path) => (path === '/media' ? { items: ['a', 'b', 'c', 'd'].map((id) => media(id)) } : undefined))
    renderAt('/image/references', [{ path: '/image/references', element: <ImageReferencesPage /> }])
    const user = userEvent.setup()
    for (const id of ['a', 'b', 'c']) await user.click(await screen.findByRole('button', { name: `Image ${id}` }))
    expect(screen.getByRole('button', { name: 'Image d' })).toBeDisabled()
    expect(screen.getByText(/That's the limit/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Open in Edit/ }))
    expect(screen.getByTestId('location')).toHaveTextContent('/image/edit?sources=a,b,c')
  })
})
