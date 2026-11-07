import { useState } from 'react'
import { useNavigate } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Check, Circle, Cpu, Eye, Loader2, ScanSearch, Wand2, Zap } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { ErrorState } from '@/components/studio/states'
import { photoKeys, useDescribe, useRestoreTools, useSmartPlan } from '@/hooks/usePhoto'
import { api } from '@/lib/api'
import type { PhotoHistory, PlanStep, RestoreTool, ToolGroup } from '@/lib/photo/types'
import type { Job } from '@/lib/types'
import { analysisLine } from '@/lib/photo/restore'
import { cn } from '@/lib/utils'
import { trackJobs } from '@/stores/toasts'
import { announce } from '@/stores/ui'
import { DevelopSlider } from './DevelopSlider'
import { runningChains } from './session'

const GROUPS: { id: ToolGroup; label: string }[] = [
  { id: 'repair', label: 'Repair' },
  { id: 'clean', label: 'Clean up' },
  { id: 'faces', label: 'Faces' },
  { id: 'colour', label: 'Colour' },
  { id: 'style', label: 'Style' },
  { id: 'finish', label: 'Finish' },
]
const STRENGTH = { min: 5, max: 100, step: 1, default: 100, label: 'Strength' }

const secs = (s: number) => (s < 60 ? `~${Math.max(1, Math.round(s))} s` : `~${Math.round(s / 60)} min`)

type StepState = 'pending' | 'running' | 'done' | 'failed'

function chainStates(history: PhotoHistory, chainId: string | null, count: number): StepState[] {
  const out: StepState[] = Array(count).fill('pending')
  if (!chainId) return out
  for (const v of history.versions) {
    if (v.chain?.id !== chainId) continue
    const s = v.generation.status
    out[v.chain.index] = s === 'ready' || s === 'approved' ? 'done' : s === 'failed' ? 'failed' : 'running'
  }
  return out
}

interface Props {
  photoId: string
  routeId: string
  title: string
  history: PhotoHistory
}

/**
 * Restore: Smart Restore (inspect -> editable plan -> one version per step), Describe, the single tools
 * by group, and the instant quick effects. Every result lands as a new version in the filmstrip.
 */
export function RestoreTab({ photoId, routeId, title, history }: Props) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const tools = useRestoreTools()
  const [inspect, setInspect] = useState(false)
  const plan = useSmartPlan(photoId, inspect)
  const [edits, setEdits] = useState<Record<string, Partial<PlanStep>>>({})
  const [chain, setChainState] = useState(() => runningChains.get(routeId) ?? null)
  const setChain = (c: { id: string; labels: string[] } | null) => {
    if (c) runningChains.set(routeId, c)
    else runningChains.delete(routeId)
    setChainState(c)
  }
  const [running, setRunning] = useState<string | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  const [failure, setFailure] = useState<unknown>(null)
  const describe = useDescribe()

  const link = `/image/studio/${routeId}`
  const refresh = () => {
    qc.invalidateQueries({ queryKey: photoKeys.history(routeId) })
    qc.invalidateQueries({ queryKey: ['jobs'] })
  }
  // true once the job is queued, so a prompted tool can empty its box
  const run = async (tag: string, fn: () => Promise<Job>, label: string): Promise<boolean> => {
    setRunning(tag)
    setFailure(null)
    try {
      const job = await fn()
      trackJobs([job], `${label} · ${title}`, link)
      announce(`${label} started. The result appears in the filmstrip as a new version.`)
      refresh()
      return true
    } catch (e) {
      setFailure(e)
      return false
    } finally {
      setRunning(null)
    }
  }

  const steps = (plan.data?.steps ?? []).map((s) => ({ ...s, ...edits[s.id] }))
  const picked = steps.filter((s) => s.on && s.available)
  const states = chain ? chainStates(history, chain.id, chain.labels.length) : []
  const chainBusy = states.some((s) => s === 'pending' || s === 'running')
  // the newest finished step is the restored picture
  const result = chain
    ? history.versions.find((v) => v.chain?.id === chain.id && (v.generation.status === 'ready' || v.generation.status === 'approved'))
    : undefined

  const startSmart = async () => {
    setRunning('smart')
    setFailure(null)
    try {
      const out = await api.photo.smartRestore(photoId, picked.map(({ tool, on, variant, strength }) => ({ tool, on, variant, strength })))
      setChain({ id: out.chain_id, labels: out.steps.map((s) => s.label) })
      trackJobs([{ id: out.first_job_id }], `Smart Restore · ${title}`, link)
      announce(`Smart Restore started: ${out.steps.length} steps. Each step becomes its own version.`)
      refresh()
    } catch (e) {
      setFailure(e)
    } finally {
      setRunning(null)
    }
  }

  const byGroup = (g: ToolGroup) => (tools.data?.tools ?? []).filter((t) => t.group === g)
  const label = (id: string) => tools.data?.tools.find((t) => t.id === id)?.label ?? id

  return (
    <div className="flex flex-col gap-4">
      {failure != null && <ErrorState compact title="That didn't start" error={failure} />}

      <section aria-labelledby="smart-h" className="rounded-[8px] border border-studio-border bg-studio-raised p-3">
        <div className="flex items-center justify-between gap-2">
          <h3 id="smart-h" className="font-display text-body font-semibold">Smart Restore</h3>
          <span className="text-[12px] text-studio-muted">old &amp; damaged photos</span>
        </div>
        <p className="mt-1 text-small text-studio-muted">Inspects the photo, proposes the fixes it needs, and runs them in order. Untick anything you don&apos;t want.</p>

        {!inspect && !chain && (
          <Button className="mt-2.5 w-full" variant="primary" onClick={() => setInspect(true)}>
            <ScanSearch aria-hidden /> Inspect photo
          </Button>
        )}
        {inspect && plan.isPending && (
          <p className="mt-2.5 flex items-center gap-2 text-small text-studio-muted" role="status">
            <Loader2 aria-hidden className="size-4 animate-spin" /> Inspecting photo…
          </p>
        )}
        {plan.isError && <ErrorState compact className="mt-2" error={plan.error} onRetry={() => plan.refetch()} />}
        {plan.data && (
          <>
            <p className="mt-2 text-[12px] text-studio-muted">
              <span className="font-medium text-studio-text">Analysis:</span> {analysisLine(plan.data.analysis)}
            </p>
            {plan.data.message && <p className="mt-2 text-small" role="status">{plan.data.message}</p>}
            {steps.length > 0 && !chain && (
              <ol className="mt-2 flex flex-col gap-1.5" aria-label="Smart Restore plan">
                {steps.map((s, i) => (
                  <li key={s.id} className={cn('flex items-start gap-2 rounded-[6px] px-1.5 py-1', !s.available && 'opacity-60')}>
                    <Switch
                      checked={s.on && s.available}
                      disabled={!s.available}
                      aria-label={`Step ${i + 1}: ${s.label}`}
                      onCheckedChange={(on) => setEdits((e) => ({ ...e, [s.id]: { ...e[s.id], on } }))}
                      className="mt-0.5"
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-baseline justify-between gap-2 text-small">
                        <span className="font-medium">{i + 1}. {s.label}</span>
                        <span className="shrink-0 text-[11px] text-studio-muted">{s.est_gpu_s ? secs(s.est_gpu_s) : 'instant'}</span>
                      </div>
                      <p className="text-[12px] text-studio-muted">{s.available ? s.reason : s.unavailable_reason}</p>
                    </div>
                  </li>
                ))}
              </ol>
            )}
            {steps.length > 0 && !chain && (
              <Button className="mt-2.5 w-full" variant="primary" loading={running === 'smart'} disabled={!picked.length} onClick={startSmart}>
                <Wand2 aria-hidden /> Restore · {picked.length} step{picked.length === 1 ? '' : 's'}
              </Button>
            )}
          </>
        )}
        {chain && (
          <>
            <ol className="mt-2 flex flex-col gap-1" aria-label="Smart Restore progress" aria-live="polite">
              {chain.labels.map((l, i) => (
                <li key={i} className="flex items-center gap-2 text-small">
                  {states[i] === 'done' && <Check aria-hidden className="size-4 text-studio-success" />}
                  {states[i] === 'running' && <Loader2 aria-hidden className="size-4 animate-spin text-studio-accent" />}
                  {states[i] === 'failed' && <AlertTriangle aria-hidden className="size-4 text-studio-danger" />}
                  {states[i] === 'pending' && <Circle aria-hidden className="size-4 text-studio-muted" />}
                  <span className={cn(states[i] === 'pending' && 'text-studio-muted')}>{i + 1}. {l}</span>
                  <span className="sr-only">{states[i]}</span>
                </li>
              ))}
            </ol>
            <div className="mt-2.5 flex gap-2">
              {result && result.generation.id !== photoId && !chainBusy && (
                <Button className="flex-1" variant="primary" onClick={() => navigate(`/image/studio/${result.generation.id}`)}>Open the result</Button>
              )}
              <Button className="flex-1" disabled={chainBusy} onClick={() => { setChain(null); setInspect(false); setEdits({}) }}>
                {chainBusy ? 'Restoring…' : 'Done'}
              </Button>
            </div>
          </>
        )}
      </section>

      <section aria-labelledby="describe-h" className="flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <h3 id="describe-h" className="section-label">Describe</h3>
          <Button size="sm" variant="ghost" loading={describe.isPending} onClick={() => describe.mutate(photoId)}>
            <Eye aria-hidden /> {describe.data ? 'Again' : 'Describe photo'}
          </Button>
        </div>
        {describe.isError && <ErrorState compact error={describe.error} />}
        {describe.data && (
          <div className="rounded-[6px] bg-studio-panel-hover p-2.5 text-small" role="status">
            <p className="font-medium">{describe.data.caption}</p>
            {describe.data.details && <p className="mt-1 text-studio-muted">{describe.data.details}</p>}
            {describe.data.defects.length > 0 && <p className="mt-1.5 text-[12px]"><span className="font-medium">Defects:</span> {describe.data.defects.join(', ')}</p>}
            {describe.data.suggested_tools.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1.5" aria-label="Suggested tools">
                {describe.data.suggested_tools.map((t) => (
                  <Button key={t} size="sm" variant="outline" onClick={() => setOpen(t)}>{label(t)}</Button>
                ))}
              </div>
            )}
          </div>
        )}
      </section>

      {tools.isPending && <Skeleton className="h-40" />}
      {tools.isError && <ErrorState compact error={tools.error} onRetry={() => tools.refetch()} />}
      {GROUPS.map((g) => {
        const list = byGroup(g.id)
        if (!list.length) return null
        return (
          <section key={g.id} aria-labelledby={`grp-${g.id}`} className="flex flex-col gap-1">
            <h3 id={`grp-${g.id}`} className="section-label">{g.label}</h3>
            {list.map((t) => (
              <ToolRow
                key={t.id}
                tool={t}
                open={open === t.id}
                onToggle={() => setOpen((o) => (o === t.id ? null : t.id))}
                busy={running === t.id}
                onApply={(opts) => run(t.id, () => api.photo.restore(photoId, { tool: t.id, ...opts }), t.label)}
              />
            ))}
          </section>
        )
      })}

      {!!tools.data?.effects.length && (
        <section aria-labelledby="fx-h" className="flex flex-col gap-1.5">
          <h3 id="fx-h" className="section-label">Quick effects · instant</h3>
          <div className="flex flex-wrap gap-1.5">
            {tools.data.effects.map((e) => (
              <Button key={e.id} size="sm" variant="outline" title={e.hint} loading={running === `fx:${e.id}`}
                onClick={() => run(`fx:${e.id}`, () => api.photo.effect(photoId, e.id), e.label)}>
                {e.label}
              </Button>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}

interface RowProps {
  tool: RestoreTool
  open: boolean
  onToggle: () => void
  busy: boolean
  onApply: (opts: { variant?: string | null; strength?: number; prompt?: string }) => Promise<boolean> | void
}

function ToolRow({ tool, open, onToggle, busy, onApply }: RowProps) {
  const [variant, setVariant] = useState(tool.default_variant)
  const [strength, setStrength] = useState(100)
  const [prompt, setPrompt] = useState('')
  const panelId = `tool-${tool.id}`
  const needsText = tool.prompt === 'required' && !prompt.trim()

  return (
    <div className={cn('rounded-[6px] border', open ? 'border-studio-border-strong bg-studio-raised' : 'border-transparent')}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={onToggle}
        className="flex w-full items-start gap-2 rounded-[6px] px-2 py-1.5 text-left hover:bg-studio-panel-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-studio-accent"
      >
        {tool.gpu ? <Cpu aria-hidden className="mt-0.5 size-4 shrink-0 text-studio-muted" /> : <Zap aria-hidden className="mt-0.5 size-4 shrink-0 text-studio-gold" />}
        <span className="min-w-0 flex-1">
          <span className={cn('block text-small font-medium', !tool.available && 'text-studio-muted')}>
            {tool.label}
          </span>
          <span className="block text-[12px] text-studio-muted">{tool.hint}</span>
        </span>
      </button>
      {open && (
        <div id={panelId} className="flex flex-col gap-2 px-2 pb-2.5 pt-1">
          {!tool.available && <p className="text-[12px] text-studio-danger" role="note">Not available: {tool.reason}</p>}
          {tool.variants.length > 1 && (
            <div role="radiogroup" aria-label={`${tool.label} option`} className="flex flex-wrap gap-1.5">
              {tool.variants.map((v) => (
                <Button key={v.id} size="sm" role="radio" aria-checked={variant === v.id} variant={variant === v.id ? 'primary' : 'outline'} onClick={() => setVariant(v.id)}>
                  {v.label}
                </Button>
              ))}
            </div>
          )}
          {tool.strength && <DevelopSlider name={`${tool.id}-strength`} label="Strength" value={strength} range={STRENGTH} onChange={(v) => setStrength(v)} />}
          {tool.prompt && (
            <Textarea
              aria-label={tool.prompt === 'required' ? `${tool.label}: what to change` : `${tool.label}: anything else (optional)`}
              placeholder={tool.prompt === 'required' ? 'e.g. remove the stain on his collar' : 'Anything else to fix (optional)'}
              rows={2}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
            />
          )}
          <Button
            variant="primary"
            size="sm"
            loading={busy}
            disabled={!tool.available || needsText}
            onClick={async () => {
              const ok = await onApply({ variant, strength: tool.strength ? strength / 100 : undefined, prompt: prompt.trim() || undefined })
              if (ok) setPrompt('')
            }}
          >
            Apply {tool.label.toLowerCase()}
          </Button>
        </div>
      )}
    </div>
  )
}
