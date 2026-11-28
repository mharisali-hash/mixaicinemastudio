import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { mockApi, renderAt } from '@/test/media-fixtures'
import { Toaster } from '@/components/shell/Toaster'
import { ComfyServicesCard } from './SystemChecks'

const units = [
  { unit: 'comfyui.service', active: 'active', state: 'running', port: 8188, card: 'image' },
  { unit: 'comfyui-gpu1.service', active: 'failed', state: 'failed', port: 8189, card: 'video' },
]

function open(restart: (unit: string) => unknown) {
  const calls = mockApi((method, path) => {
    if (path === '/system/comfy-services') return { units }
    if (method === 'POST' && path.endsWith('/restart')) return restart(decodeURIComponent(path.split('/')[3]))
  })
  renderAt('/settings', [{ path: '/settings', element: <ComfyServicesCard /> }], undefined, <Toaster />)
  return { calls, user: userEvent.setup() }
}

describe('Settings: ComfyUI restart', () => {
  it('names each card, asks first, then restarts only that one', async () => {
    const { calls, user } = open((unit) => ({ unit, restarted: true }))
    expect(await screen.findByText('Video card (port 8189)')).toBeInTheDocument()
    expect(screen.getByText(/comfyui-gpu1\.service · failed/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Restart Video card (port 8189)' }))
    const ask = within(await screen.findByRole('alertdialog'))
    expect(calls.some((c) => c.method === 'POST')).toBe(false)
    await user.click(ask.getByRole('button', { name: 'Restart' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.path === '/system/comfy-services/comfyui-gpu1.service/restart')).toBe(true))
    expect(calls.some((c) => c.path.includes('comfyui.service/restart'))).toBe(false)
    expect(await screen.findByText('ComfyUI restarted')).toBeInTheDocument()
  })

  it('shows what to run when the server does not allow it yet', async () => {
    const detail = "This server doesn't let the app restart ComfyUI yet. On the server run, once: ./scripts/allow-comfy-restart.sh"
    const { user } = open(
      () => new Response(JSON.stringify({ detail }), { status: 409, headers: { 'Content-Type': 'application/json' } }),
    )
    await user.click(await screen.findByRole('button', { name: 'Restart Image card (port 8188)' }))
    await user.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Restart' }))
    expect(await screen.findByText("Couldn't restart ComfyUI")).toBeInTheDocument()
    expect(screen.getByText(/allow-comfy-restart\.sh/)).toBeInTheDocument()
  })
})
