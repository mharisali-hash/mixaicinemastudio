import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Navigate } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ImageEditPage } from '@/features/image/ImageEditPage'
import { localStart } from '@/lib/templates'
import type { ImageToolInfo, Template, TemplateTool } from '@/lib/types'
import { media, mockApi, renderAt } from '@/test/media-fixtures'
import { TemplatesPage } from './TemplatesPage'

afterEach(() => vi.unstubAllGlobals())

const LAMA: TemplateTool = { id: 'inpaint', label: 'Remove with a mask', engine: 'LaMa', licence: 'Apache-2.0 (LaMa big-lama)', noncommercial: false, fallback: true, needs_mask: true }
const CATVTON: TemplateTool = { id: 'tryon', label: 'Virtual try-on', engine: 'CatVTON', licence: 'CC BY-NC-SA 4.0 (CatVTON weights): non-commercial only', noncommercial: true, fallback: false, needs_mask: false }
const DEPTH: TemplateTool = { id: 'depth', label: 'Depth map', engine: 'Depth Anything V2 Small', licence: 'Apache-2.0 (Depth Anything V2 Small)', noncommercial: false, fallback: false, needs_mask: false }
const WORDS: TemplateTool = { id: 'remove', label: 'Remove by words', engine: 'Remove by words', licence: 'x', noncommercial: false, fallback: false, needs_mask: false }
const ICLIGHT: TemplateTool = { id: 'relight', label: 'Relight', engine: 'IC-Light v1', licence: 'Apache-2.0 (IC-Light v1)', noncommercial: false, fallback: true, needs_mask: false }

const card = (id: string, title: string, tool: TemplateTool, scaffold: string, labels = ['Photo']): Template => ({
  id,
  type: 'image',
  target: 'image-edit',
  title,
  description: `${title}.`,
  category: 'Edit a photo',
  tags: [id],
  refs_required: labels.length,
  ref_labels: labels,
  tool,
  defaults: { instruction_scaffold: scaffold, model: 'qwen_image_edit_2511', count: 2 },
})

const remove = card('remove', 'Remove an object', LAMA, 'Remove [object to remove] from image 1.')
const tryon = card('tryon', 'Virtual try-on (CatVTON)', CATVTON, 'Dress the person in image 1 in the garment from image 2.', ['Person', 'Garment'])
const depth = card('depth', 'Depth map', DEPTH, 'Make a depth map of image 1.')
const relight = card('relight', 'Relight', ICLIGHT, 'Relight image 1 with warm light.')
const byWords = { ...card('words', 'Remove…', WORDS, 'Take out of image 1 exactly what is named under What should go.'), defaults: { instruction_scaffold: 'Take out of image 1 exactly what is named under What should go.', model: 'qwen_image_edit_2511', count: 1 } }
const ALL = [remove, tryon, depth, relight, byWords]

const info = (t: TemplateTool, available = true): ImageToolInfo => ({ ...t, available, reason: available ? null : 'missing nodes: CatVTON' })

const routes = [
  { path: '/image/prompt-templates', element: <TemplatesPage type="image" /> },
  { path: '/image/edit', element: <ImageEditPage /> },
]

function api(tools: ImageToolInfo[]) {
  return mockApi((method, path) => {
    if (path === '/prompt-templates') return ALL
    if (path === '/images/tools') return { tools }
    const m = path.match(/^\/prompt-templates\/(\w+)\/start$/)
    if (method === 'POST' && m) return localStart(ALL.find((t) => t.id === m[1])!)
    const id = path.match(/^\/media\/(\w+)$/)?.[1]
    if (id) return { ...media(id), versions: [] }
    if (path === '/media') return { items: [media('p1'), media('g1')] }
    if (method === 'POST' && path === '/images/edit') return { items: [], jobs: [], tool_resolved: 'inpaint' }
  })
}

async function open(title: string) {
  const user = userEvent.setup()
  await user.click(await screen.findByRole('button', { name: `Use the ${title} prompt template` }))
  await screen.findByRole('textbox', { name: 'What should change?' })
  return user
}

async function addPhoto(user: ReturnType<typeof userEvent.setup>, slot = 'Add photo (required)', pick = /Image p1/) {
  await user.click(screen.getByRole('button', { name: slot }))
  await user.click(await screen.findByRole('button', { name: 'Pick from Library' }))
  await user.click(await screen.findByRole('button', { name: pick }))
  await user.click(screen.getByRole('button', { name: /Use selected/ }))
}

describe('image tool cards', () => {
  it('hides a tool-only card the server cannot run and says which engine each tool uses', async () => {
    api([info(LAMA), info(CATVTON, false), info(DEPTH), info(ICLIGHT)])
    renderAt('/image/prompt-templates', routes)
    expect(await screen.findByRole('heading', { name: 'Depth map' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Virtual try-on (CatVTON)' })).not.toBeInTheDocument()
    expect(screen.getByText('Runs on LaMa when installed')).toBeInTheDocument()
    expect(screen.getByText('Runs on Depth Anything V2 Small')).toBeInTheDocument()
    expect(screen.queryByText(/Apache|licen/i)).not.toBeInTheDocument()
  })

  it('offers the try-on card, with no licence label, when CatVTON is installed', async () => {
    api([info(LAMA), info(CATVTON), info(DEPTH), info(ICLIGHT)])
    renderAt('/image/prompt-templates', routes)
    expect(await screen.findByRole('heading', { name: 'Virtual try-on (CatVTON)' })).toBeInTheDocument()
    expect(screen.getByText('Runs on CatVTON')).toBeInTheDocument()
    expect(screen.queryByText(/commercial|licen/i)).not.toBeInTheDocument()
  })
})

describe('Edit Image with a tool', () => {
  it('Remove an object: LaMa with a mask, the edit model from words, or the edit model on request', async () => {
    const calls = api([info(LAMA)])
    renderAt('/image/prompt-templates', routes)
    const user = await open('Remove an object')
    expect(await screen.findByText(/Runs on LaMa where you paint a mask/)).toBeInTheDocument()
    expect(screen.getByText('Runs on LaMa')).toBeInTheDocument()
    expect(screen.queryByText(/licen|Apache/i)).not.toBeInTheDocument()

    await addPhoto(user)
    expect(await screen.findByLabelText(/Mask: drag over the object/)).toBeInTheDocument()
    // no mask painted: the words have to say what goes
    expect(screen.getByText('Replace [object to remove] first.')).toBeInTheDocument()
    const box = screen.getByRole('textbox', { name: 'What should change?' })
    await user.clear(box)
    await user.type(box, 'Remove the bin from image 1.')
    await user.click(screen.getByRole('button', { name: /^Edit · 2 results/ }))
    const sent = calls.filter((c) => c.method === 'POST' && c.path === '/images/edit')
    expect(sent[0].body).toMatchObject({ template_id: 'remove', engine: 'auto', source_ids: ['p1'] })
    expect(sent[0].body).not.toHaveProperty('mask')

    await user.click(screen.getByRole('radio', { name: 'Edit model (Qwen-Image-Edit)' }))
    expect(screen.getByText('Runs on the edit model, from your instruction.')).toBeInTheDocument()
    expect(screen.queryByLabelText(/Mask:/)).not.toBeInTheDocument()
    // the first edit was a fresh start once queued: no words and no picture
    expect(box).toHaveValue('')
    await addPhoto(user)
    await user.type(screen.getByRole('textbox', { name: 'What should change?' }), 'Remove the bin from image 1.')
    await user.click(screen.getByRole('button', { name: /^Edit · 2 results/ }))
    expect(calls.filter((c) => c.method === 'POST' && c.path === '/images/edit')[1].body).toMatchObject({ engine: 'edit' })
    // adding the picture again after the reset makes this the longest run in the file
  }, 30_000)

  it('falls back to the edit model when the tool is switched off', async () => {
    api([])
    renderAt('/image/prompt-templates', routes)
    await open('Remove an object')
    expect(await screen.findByText("LaMa isn't available (switched off on this server), so the edit model runs this instead.")).toBeInTheDocument()
    expect(screen.queryByRole('radio', { name: 'Edit model (Qwen-Image-Edit)' })).not.toBeInTheDocument()
  })

  it('a tool-only card opened from an old link blocks the run when its tool is missing', async () => {
    api([info(DEPTH, false)])
    // the gallery hides this card, so arrive the way a bookmarked or shared start would
    const template = { templateId: 'depth', templateTitle: 'Depth map', prefill: localStart(depth).prefill }
    renderAt('/go', [...routes, { path: '/go', element: <Navigate to="/image/edit" state={{ template }} /> }])
    const user = userEvent.setup()
    await screen.findByRole('textbox', { name: 'What should change?' })
    await addPhoto(user)
    expect(await screen.findByText("Depth Anything V2 Small isn't available on this server.")).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Edit · 2 results/ })).toBeDisabled()
  })

  it('Relight sends the light direction to IC-Light', async () => {
    const calls = api([info(ICLIGHT)])
    renderAt('/image/prompt-templates', routes)
    const user = await open('Relight')
    await addPhoto(user)
    await user.selectOptions(await screen.findByRole('combobox', { name: 'Light from' }), 'Left')
    await user.click(screen.getByRole('button', { name: /^Edit · 2 results/ }))
    expect(calls.find((c) => c.method === 'POST' && c.path === '/images/edit')?.body).toMatchObject({
      template_id: 'relight',
      engine: 'auto',
      tool_options: { light: 'left' },
    })
  })

  it('Remove… asks what should go, offers quick picks, and sends the words without a brush', async () => {
    const calls = api([info(WORDS)])
    renderAt('/image/prompt-templates', routes)
    expect(await screen.findByRole('heading', { name: 'Remove…' })).toBeInTheDocument()
    expect(screen.queryByText('Runs on Remove by words')).not.toBeInTheDocument()
    const user = await open('Remove…')
    await addPhoto(user)
    const words = await screen.findByRole('textbox', { name: 'What should go?' })
    expect(screen.queryByLabelText(/Mask:/)).not.toBeInTheDocument()
    expect(screen.getByText('Say what should go.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Edit · 1 result/ })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: 'children' }))
    expect(words).toHaveValue('children')
    expect(screen.getByRole('button', { name: 'children' })).toHaveAttribute('aria-pressed', 'true')
    await user.clear(words)
    await user.type(words, 'men')
    await user.click(screen.getByRole('button', { name: /^Edit · 1 result/ }))
    expect(calls.find((c) => c.method === 'POST' && c.path === '/images/edit')?.body).toMatchObject({
      template_id: 'words',
      engine: 'auto',
      source_ids: ['p1'],
      tool_options: { words: 'men' },
    })
    expect(screen.queryByText(/SAM|LaMa/)).not.toBeInTheDocument()
  })
})
