import { fireEvent, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { clampStrength, DEFAULT_I2I_FORM, i2iModels, img2imgPayload, strengthLabel } from '@/lib/img2img'
import { job, media, mockApi, renderAt } from '@/test/media-fixtures'
import { CATALOG, seedCatalog } from '@/test/model-fixtures'
import { closeChip, openChip } from '@/test/dock'
import type { ModelInfo, ModelType } from '@/lib/types'
import { Img2ImgPage } from './Img2ImgPage'

afterEach(() => vi.unstubAllGlobals())

// the image models gain "i2i" with milestone 8; Qwen-Image stays text-only here
const WITH_I2I: Record<ModelType, ModelInfo[]> = {
  ...CATALOG,
  image: CATALOG.image.map((m) => (m.id === 'qwen_image_2512' ? m : { ...m, capabilities: [...m.capabilities, 'i2i'] })),
}

describe('img2img rules', () => {
  it('labels the slider and keeps strength inside 0.1–0.9', () => {
    expect([0.1, 0.3, 0.45, 0.5, 0.6, 0.7, 0.9].map(strengthLabel)).toEqual(['Subtle', 'Subtle', 'Balanced', 'Balanced', 'Strong', 'Strong', 'Strong'])
    expect(clampStrength(0.02)).toBe(0.1)
    expect(clampStrength(1.4)).toBe(0.9)
    expect(clampStrength(0.456)).toBe(0.46)
  })

  it('offers only models that can start from a picture, or all of them on an older catalog', () => {
    expect(i2iModels(WITH_I2I.image).map((m) => m.id)).toEqual(['zimage_turbo', 'flux2_klein'])
    expect(i2iModels(CATALOG.image)).toHaveLength(3)
  })

  it('builds the body: source aspect by default, seed only when set', () => {
    expect(img2imgPayload({ ...DEFAULT_I2I_FORM, sourceId: 's1', prompt: ' oil painting ' })).toEqual({
      source_id: 's1',
      prompt: 'oil painting',
      strength: 0.45,
      count: 2,
      aspect: 'source',
    })
  })
})

describe('Image to Image page', () => {
  it('posts source, prompt, strength, model, aspect and count', async () => {
    const calls = mockApi((method, path) => {
      if (path === '/media') return { items: [] }
      const id = path.match(/^\/media\/(\w+)$/)?.[1]
      if (id) return { ...media(id, { title: 'Street at noon' }), versions: [] }
      if (method === 'POST' && path === '/images/img2img') return { items: [media('r1', { media_url: null, title: 'Street, painted' })], jobs: [job('j1', { generation_id: 'g-r1' })] }
    })
    renderAt('/image/img2img?source=s1', [{ path: '/image/img2img', element: <Img2ImgPage /> }], (qc) => seedCatalog(qc, WITH_I2I))
    const user = userEvent.setup()

    expect(await screen.findByRole('img', { name: 'Street at noon' })).toBeInTheDocument()
    const models = await openChip(user, 'Model')
    expect(within(models).getByRole('radio', { name: 'Auto' })).toBeChecked()
    expect(within(models).queryByRole('radio', { name: /Qwen-Image 2512/ })).not.toBeInTheDocument()
    await closeChip(user)
    expect(within(await openChip(user, 'Aspect')).getByRole('radio', { name: 'Same as the source' })).toBeChecked()
    await closeChip(user)

    await openChip(user, 'Style strength')
    const slider = screen.getByRole('slider', { name: 'Style strength' })
    expect(slider).toHaveAttribute('aria-valuetext', '0.45, Balanced')
    fireEvent.change(slider, { target: { value: '0.8' } })
    expect(slider).toHaveAttribute('aria-valuetext', '0.80, Strong')
    expect(screen.getByText('Keeps a loose layout, redraws almost everything.')).toBeInTheDocument()
    await closeChip(user)

    await user.click(screen.getByRole('textbox', { name: 'Describe the result' }))
    await user.paste('Watercolour, soft evening light')
    await user.click(within(await openChip(user, 'Count')).getByRole('radio', { name: '1 image' }))
    await closeChip(user)
    await user.click(screen.getByRole('textbox', { name: 'Describe the result' }))
    await user.keyboard('{Control>}{Enter}{/Control}')

    expect(calls.find((c) => c.method === 'POST')).toMatchObject({
      path: '/images/img2img',
      body: { source_id: 's1', prompt: 'Watercolour, soft evening light', strength: 0.8, model: 'zimage_turbo', aspect: 'source', count: 1 },
    })
    expect(await within(screen.getByRole('region', { name: 'Results' })).findByRole('listitem', { name: 'Request: Watercolour, soft evening light' })).toBeInTheDocument()
    // queued: a fresh start, with no words and no picture
    expect(screen.getByRole('textbox', { name: 'Describe the result' })).toHaveValue('')
    expect(await screen.findByRole('button', { name: 'Add your picture (required)' })).toBeInTheDocument()
  })

  it('needs a picture before it can run', async () => {
    mockApi((_m, path) => (path === '/media' ? { items: [] } : undefined))
    renderAt('/image/img2img', [{ path: '/image/img2img', element: <Img2ImgPage /> }], (qc) => seedCatalog(qc, WITH_I2I))
    const user = userEvent.setup()
    await user.click(screen.getByRole('textbox', { name: 'Describe the result' }))
    await user.paste('Make it a poster')
    expect(screen.getByRole('button', { name: /^Generate/ })).toBeDisabled()
    expect(screen.getByText('Add your picture first: drop, paste or pick one.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add your picture (required)' })).toBeInTheDocument()
  })
})
