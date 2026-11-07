import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ImageGeneratePage } from '@/features/image/ImageGeneratePage'
import { Img2ImgPage } from '@/features/image/Img2ImgPage'
import { CATALOG, seedCatalog } from '@/test/model-fixtures'
import { closeChip, openChip } from '@/test/dock'
import { job, media, mockApi, renderAt } from '@/test/media-fixtures'
import type { ModelInfo } from '@/lib/types'

afterEach(() => vi.unstubAllGlobals())

const notFound = () => new Response('{"detail":"Not Found"}', { status: 404 })

function api(over: (method: string, path: string, body: unknown) => unknown = () => undefined) {
  return mockApi((method, path, _q, body) => {
    const hit = over(method, path, body)
    if (hit !== undefined) return hit
    if (path === '/media') return { items: [] }
    if (path === '/prompt-templates') return []
    if (path === '/estimate' || path === '/prompts/enhance') return notFound()
    const id = path.match(/^\/media\/(\w+)$/)?.[1]
    if (id) return { ...media(id), versions: [] }
    if (method === 'POST' && path === '/images/generate') return { items: [media('n1', { media_url: null })], jobs: [job('j1', { generation_id: 'g-n1' })] }
  })
}

const create = (seed = seedCatalog) => renderAt('/image/generate', [{ path: '/image/generate', element: <ImageGeneratePage /> }], seed)
const posted = (calls: ReturnType<typeof api>, path = '/images/generate') => calls.find((c) => c.method === 'POST' && c.path === path)?.body as Record<string, unknown>

describe('prompt dock', () => {
  it('shows the settings as chips and remembers Simple / Advanced for next time', async () => {
    api()
    const first = create()
    const user = userEvent.setup()
    const chips = screen.getByRole('group', { name: 'Settings' })
    expect(within(chips).getAllByRole('button').map((b) => b.getAttribute('aria-label'))).toEqual([
      'Model: Auto',
      'Aspect: 1:1',
      'Count: ×2',
      'Style: None',
      'References: Refs',
      'Brand: none',
    ])
    expect(screen.getByRole('textbox', { name: 'Describe the image' })).toHaveAttribute('dir', 'auto')
    expect(screen.queryByRole('textbox', { name: 'Seed' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('radio', { name: 'Advanced' }))
    expect(screen.getByRole('group', { name: 'Advanced settings' })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Seed' })).toBeInTheDocument()
    first.unmount()

    // another generator, same person: still Advanced
    renderAt('/image/img2img', [{ path: '/image/img2img', element: <Img2ImgPage /> }], seedCatalog)
    expect(screen.getByRole('radio', { name: 'Advanced' })).toBeChecked()
    expect(screen.getByRole('textbox', { name: 'Seed' })).toBeInTheDocument()
  })

  it('a broken localStorage only means Simple', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied')
    })
    api()
    create()
    expect(screen.getByRole('radio', { name: 'Simple' })).toBeChecked()
    vi.restoreAllMocks()
  })
})

describe('generate button', () => {
  it("states the server's measured range, and the disabled reason inline", async () => {
    const calls = api((_m, path) => (path === '/estimate' ? { low_s: 20, high_s: 35, basis: 'measured', samples: 14 } : undefined))
    create()
    const user = userEvent.setup()
    const button = await screen.findByRole('button', { name: /^Generate · 2 images · ~20–35 s/ })
    expect(button).toBeDisabled()
    expect(button).toHaveAccessibleDescription('Describe the image first.')
    expect(screen.getByRole('button', { name: /About the estimate: Measured from the last 14 runs/ })).toHaveTextContent('measured')
    expect(calls.find((c) => c.path === '/estimate')?.query).toMatchObject({ kind: 'image', model: 'zimage_turbo', count: '2', width: '1024', height: '1024' })

    await user.click(screen.getByRole('textbox', { name: 'Describe the image' }))
    await user.paste('A fox in the snow')
    expect(button).toBeEnabled()
  })

  it("falls back to a rough range from the catalog when the server can't estimate", async () => {
    api()
    create()
    // 7 s × 2 images, rough (×0.7–1.6)
    expect(await screen.findByRole('button', { name: /^Generate · 2 images · ~10–20 s/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /About the estimate: A rough guess/ })).toHaveTextContent('rough')
  })
})

describe('magic prompt', () => {
  it('previews, lets the user edit and accept, then sends the enhanced prompt marked as such', async () => {
    const calls = api((method, path) =>
      method === 'POST' && path === '/prompts/enhance' ? { enhanced: 'A red fox in fresh snow, low winter sun, 85mm', changed: true } : undefined,
    )
    create()
    const user = userEvent.setup()
    await user.click(screen.getByRole('textbox', { name: 'Describe the image' }))
    await user.paste('a fox')
    await user.click(screen.getByRole('button', { name: 'Preview enhancement' }))

    expect(posted(calls, '/prompts/enhance')).toEqual({ prompt: 'a fox', kind: 'image', mode: 'auto', model: 'zimage_turbo' })
    const panel = await screen.findByRole('region', { name: 'Enhanced prompt' })
    const text = within(panel).getByRole('textbox', { name: 'Enhanced prompt' })
    expect(text).toHaveValue('A red fox in fresh snow, low winter sun, 85mm')
    await user.type(text, ', film grain')
    await user.click(within(panel).getByRole('button', { name: 'Use enhanced prompt' }))
    expect(within(panel).getByText('Runs with the enhanced prompt')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /^Generate/ }))
    const body = posted(calls)
    expect(body).toMatchObject({ prompt: 'A red fox in fresh snow, low winter sun, 85mm, film grain', prompt_enhanced: true })
    expect(body).not.toHaveProperty('magic_prompt')
  })

  it('Off sends the prompt as written; editing after a preview drops the enhancement', async () => {
    const calls = api((method, path) => (method === 'POST' && path === '/prompts/enhance' ? { enhanced: 'Better fox', changed: true } : undefined))
    create()
    const user = userEvent.setup()
    const prompt = screen.getByRole('textbox', { name: 'Describe the image' })
    await user.click(prompt)
    await user.paste('a fox')
    // the dropdown starts on Auto prompt correction
    const correction = screen.getByRole('combobox', { name: 'Prompt correction' })
    expect(correction).toHaveValue('auto')
    await user.selectOptions(correction, 'Always correct')
    await user.click(screen.getByRole('button', { name: 'Preview enhancement' }))
    await user.click(await screen.findByRole('button', { name: 'Use enhanced prompt' }))
    await user.type(prompt, ' at dawn')
    expect(screen.getByText(/You changed the prompt after this preview/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /^Generate/ }))
    expect(posted(calls)).toMatchObject({ prompt: 'a fox at dawn', magic_prompt: 'on' })
    expect(posted(calls)).not.toHaveProperty('prompt_enhanced')

    calls.length = 0
    const next = screen.getByRole('textbox', { name: 'Describe the image' })
    expect(next).toHaveValue('')
    // clearing the prompt keeps the person's choice
    expect(screen.getByRole('combobox', { name: 'Prompt correction' })).toHaveValue('on')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Prompt correction' }), 'Off: use my prompt exactly')
    expect(screen.queryByRole('button', { name: 'Preview enhancement' })).not.toBeInTheDocument()
    await user.click(next)
    await user.paste('a fox at dawn')
    await user.click(screen.getByRole('button', { name: /^Generate/ }))
    expect(posted(calls)).toMatchObject({ prompt: 'a fox at dawn', magic_prompt: 'off' })
  })

  it('says so when the server has no enhancer yet', async () => {
    api()
    create()
    const user = userEvent.setup()
    await user.click(screen.getByRole('textbox', { name: 'Describe the image' }))
    await user.paste('a fox')
    await user.click(screen.getByRole('button', { name: 'Preview enhancement' }))
    expect(await screen.findByText("Prompt enhancement isn't available on this server yet.")).toBeInTheDocument()
  })
})

describe('Auto model', () => {
  it('is the default; an older server gets the model Auto would pick', async () => {
    const calls = api()
    create()
    const user = userEvent.setup()
    expect(screen.getByRole('button', { name: 'Model: Auto' })).toBeInTheDocument()
    const prompt = screen.getByRole('textbox', { name: 'Describe the image' })
    await user.click(prompt)
    await user.paste('A shop sign that says "Open"')
    await user.click(screen.getByRole('button', { name: /^Generate/ }))
    expect(posted(calls)).toMatchObject({ model: 'qwen_image_2512' })
    expect(posted(calls)).not.toHaveProperty('speed')
  })

  it('sends "auto" when the catalog has it', async () => {
    const auto = { id: 'auto', type: 'image', label: 'Auto', badge: null, description: 'Picks the best model for your prompt', capabilities: [], available: true, default: false } as ModelInfo
    const calls = api()
    create((qc) => seedCatalog(qc, { ...CATALOG, image: [auto, ...CATALOG.image] }))
    const user = userEvent.setup()
    const chip = await openChip(user, 'Model')
    expect(within(chip).getAllByRole('radio')[0]).toHaveAccessibleName('Auto')
    expect(within(chip).getByRole('radio', { name: 'Auto' })).toBeChecked()
    await closeChip(user)
    await user.click(screen.getByRole('textbox', { name: 'Describe the image' }))
    await user.paste('a fox')
    await user.click(screen.getByRole('button', { name: /^Generate/ }))
    expect(posted(calls)).toMatchObject({ model: 'auto' })
  })
})
