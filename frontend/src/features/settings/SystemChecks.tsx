import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Check, Cpu, Play, RotateCcw, X } from 'lucide-react'
import { ConfirmDialog } from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { ErrorState } from '@/components/studio/states'
import { StatusDot } from '@/components/shell/ConnectionStatus'
import { useConnections } from '@/hooks/useConnections'
import { useComfyCheck, useLlmCheck } from '@/hooks/useStudio'
import { useUpscaleOptions } from '@/hooks/useUpscale'
import { qk } from '@/hooks/keys'
import { api } from '@/lib/api'
import type { ComfyService, TemplateCheck } from '@/lib/types'
import { cn, timeAgo } from '@/lib/utils'
import { useToasts } from '@/stores/toasts'

export function Card({ title, actions, children }: { title: string; actions?: React.ReactNode; children: React.ReactNode }) {
  const id = `set-${title.toLowerCase().replace(/\W+/g, '-')}`
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3 rounded-[8px] border border-studio-border-strong bg-studio-panel p-4 shadow-card">
      <div className="flex flex-wrap items-center gap-2">
        <h2 id={id} className="font-display text-panel font-semibold">
          {title}
        </h2>
        <div className="ml-auto flex gap-2">{actions}</div>
      </div>
      {children}
    </section>
  )
}

const Ok = ({ ok, children }: { ok: boolean; children: React.ReactNode }) => (
  <span className={cn('inline-flex items-center gap-1 text-small font-medium', ok ? 'text-studio-success' : 'text-studio-danger')}>
    {ok ? <Check aria-hidden className="size-3.5" /> : <X aria-hidden className="size-3.5" />}
    {children}
  </span>
)

const gb = (bytes: number) => `${(bytes / 1024 ** 3).toFixed(1)} GB`

export function ConnectionsCard() {
  const c = useConnections()
  const d = c.data
  const rows = [
    { name: 'ComfyUI', state: c.comfy, detail: c.comfyDetail },
    { name: 'Ollama', state: c.llm, detail: c.llmDetail },
  ]
  return (
    <Card title="Connections">
      <dl className="flex flex-col gap-2">
        {rows.map((r) => (
          <div key={r.name} className="flex flex-wrap items-center gap-2 text-body">
            <StatusDot state={r.state} />
            <dt className="w-20 font-medium">{r.name}</dt>
            <dd className={cn('min-w-0 flex-1 break-words', r.state === 'down' ? 'text-studio-danger' : 'text-studio-muted')}>
              {r.state === 'ok' ? 'Connected' : r.state === 'down' ? 'Offline' : 'Checking'} · {r.detail}
            </dd>
          </div>
        ))}
        {d && (
          <div className="flex flex-wrap items-center gap-2 text-body">
            <StatusDot state={d.worker.alive ? 'ok' : 'down'} />
            <dt className="w-20 font-medium">Worker</dt>
            <dd className="text-studio-muted">
              {d.worker.alive ? 'Running' : 'Not running'}
              {d.worker.last_heartbeat && ` · last seen ${timeAgo(d.worker.last_heartbeat)}`}
            </dd>
          </div>
        )}
      </dl>
      {d?.driver === 'mock' && (
        <p className="flex items-center gap-1.5 text-small text-studio-warning">
          <AlertTriangle aria-hidden className="size-3.5" /> The mock renderer is in use: generations are placeholders, not ComfyUI.
        </p>
      )}
      {!!d?.comfy.devices?.length && (
        <ul className="flex flex-col gap-1" aria-label="GPUs">
          {d.comfy.devices.map((g) => (
            <li key={g.name} className="flex items-center gap-2 font-mono text-small text-studio-muted">
              <Cpu aria-hidden className="size-3.5" />
              <span className="text-studio-text">{g.name}</span>
              {gb(g.vram_free)} free of {gb(g.vram_total)}
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function Problems({ t }: { t: TemplateCheck }) {
  const list = [
    ...t.missing_nodes.map((n) => `Missing node: ${n}`),
    ...t.missing_models.map((m) => `Missing model: ${m}`),
    ...t.invalid,
  ]
  return (
    <ul className="mt-1 flex flex-col gap-0.5 text-small">
      {list.map((p) => (
        <li key={p} className="break-words text-studio-danger">
          {p}
        </li>
      ))}
      {t.warnings.map((w) => (
        <li key={w} className="break-words text-studio-warning">
          {w}
        </li>
      ))}
    </ul>
  )
}

export function TemplatesCard() {
  const [run, setRun] = useState(false)
  const check = useComfyCheck(run)
  const entries = Object.entries(check.data?.templates ?? {}).sort(([a], [b]) => a.localeCompare(b))
  return (
    <Card
      title="Workflow templates"
      actions={
        <Button size="sm" variant="secondary" loading={check.isFetching} onClick={() => (run ? check.refetch() : setRun(true))}>
          <Play aria-hidden />
          {run ? 'Check again' : 'Check templates'}
        </Button>
      }
    >
      <p className="text-small text-studio-muted">Compares every workflow with the nodes and models installed on ComfyUI. Nothing is generated.</p>
      {check.isFetching && !check.data && <Skeleton className="h-24" />}
      {check.isError && <ErrorState compact title="Couldn't run the check" error={check.error} />}
      {check.data && !check.data.ok && check.data.error && <ErrorState compact title="ComfyUI didn't answer" error={check.data.error} />}
      {entries.length > 0 && (
        <ul className="flex flex-col divide-y divide-studio-border" aria-label="Template results">
          {entries.map(([name, t]) => (
            <li key={name} className="py-1.5">
              <div className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate font-mono text-small">{name}</span>
                <Ok ok={t.ok}>{t.ok ? 'Ready' : 'Problems'}</Ok>
              </div>
              {(!t.ok || t.warnings.length > 0) && <Problems t={t} />}
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

export function LlmCard() {
  const [ping, setPing] = useState(false)
  const check = useLlmCheck(ping)
  const roles = Object.entries(check.data?.roles ?? {})
  return (
    <Card
      title="Language models"
      actions={
        <Button size="sm" variant="secondary" loading={ping && check.isFetching} onClick={() => (ping ? check.refetch() : setPing(true))}>
          <Play aria-hidden />
          Ping models
        </Button>
      }
    >
      <p className="text-small text-studio-muted">
        Which Ollama model plays each role. Pinging loads each one in turn, which can take a few minutes on a cold GPU.
      </p>
      {check.isPending && <Skeleton className="h-16" />}
      {check.isError && <ErrorState compact title="Couldn't check the models" error={check.error} />}
      {check.data?.error && <ErrorState compact title="Ollama didn't answer" error={check.data.error} />}
      {roles.length > 0 && (
        <dl className="flex flex-col gap-1.5">
          {roles.map(([role, r]) => (
            <div key={role} className="flex flex-wrap items-center gap-2 text-body">
              <dt className="w-24 capitalize text-studio-muted">{role}</dt>
              <dd className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
                <span className="font-mono text-small">{r?.model ?? 'not set'}</span>
                <Ok ok={!!r?.present && !r?.error}>{r?.error ? r.error : r?.present ? 'Installed' : 'Not installed'}</Ok>
                {r?.latency_ms != null && <span className="font-mono text-small text-studio-muted">{r.latency_ms} ms</span>}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </Card>
  )
}

const CARD_NAME = { image: 'Image card', video: 'Video card', '': 'ComfyUI' } as const

/** Admins: restart a ComfyUI service when it stops answering. Needs scripts/allow-comfy-restart.sh on the server. */
export function ComfyServicesCard() {
  const qc = useQueryClient()
  const push = useToasts((s) => s.push)
  const services = useQuery({ queryKey: ['comfy-services'], queryFn: api.system.comfyServices, refetchInterval: 15_000 })
  const [asking, setAsking] = useState<ComfyService | null>(null)
  const restart = useMutation({
    mutationFn: (unit: string) => api.system.comfyRestart(unit),
    onSuccess: (_, unit) => {
      push({ title: 'ComfyUI restarted', detail: `${unit} is starting; it answers again in about a minute.`, tone: 'done' })
      void qc.invalidateQueries({ queryKey: ['comfy-services'] })
      void qc.invalidateQueries({ queryKey: qk.system })
    },
    onError: (e) => push({ title: "Couldn't restart ComfyUI", detail: e.message, tone: 'failed' }),
    onSettled: () => setAsking(null),
  })
  const units = services.data?.units ?? []
  const name = (u: ComfyService) => `${CARD_NAME[u.card]}${u.port ? ` (port ${u.port})` : ''}`
  return (
    <Card title="ComfyUI">
      <p className="text-small text-studio-muted">
        Restart a card's ComfyUI when pictures or videos fail with "no healthy ComfyUI". Anything rendering on it at that moment stops; a bulk batch pauses and resumes from where it was.
      </p>
      {services.isPending && <Skeleton className="h-12" />}
      {services.isError && <ErrorState compact title="Couldn't list the services" error={services.error} />}
      {services.data && !units.length && <p className="text-body text-studio-muted">No ComfyUI services found on this machine.</p>}
      {units.length > 0 && (
        <ul className="flex flex-col divide-y divide-studio-border" aria-label="ComfyUI services">
          {units.map((u) => (
            <li key={u.unit} className="flex flex-wrap items-center gap-2 py-2">
              <StatusDot state={u.active === 'active' ? 'ok' : 'down'} />
              <span className="min-w-0 flex-1">
                <span className="block text-body font-medium">{name(u)}</span>
                <span className="block font-mono text-small text-studio-muted">
                  {u.unit} · {u.active === 'active' ? 'running' : u.active}
                  {u.state && u.state !== 'running' && ` (${u.state})`}
                </span>
              </span>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => setAsking(u)}
                loading={restart.isPending && restart.variables === u.unit}
                disabled={restart.isPending}
                aria-label={`Restart ${name(u)}`}
              >
                <RotateCcw aria-hidden />
                Restart
              </Button>
            </li>
          ))}
        </ul>
      )}
      <ConfirmDialog
        open={!!asking}
        onOpenChange={(o) => !o && !restart.isPending && setAsking(null)}
        title={asking ? `Restart ${name(asking)}?` : 'Restart ComfyUI?'}
        description="Anything rendering on this card right now stops and shows as failed; you can retry it. It takes about a minute to answer again."
        confirmLabel="Restart"
        onConfirm={() => asking && restart.mutate(asking.unit)}
      />
    </Card>
  )
}

export function UpscaleCard() {
  const opts = useUpscaleOptions()
  return (
    <Card title="Upscale engines">
      {opts.isPending && <Skeleton className="h-16" />}
      {opts.isError && <ErrorState compact title="Couldn't check the engines" error={opts.error} />}
      {opts.data && (
        <ul className="flex flex-col gap-1.5">
          {(opts.data.engines ?? []).map((e) => (
            <li key={e.id} className="flex flex-wrap items-center gap-2 text-body">
              <span className="w-28 font-medium">{e.label}</span>
              <Ok ok={e.available}>{e.available ? 'Available' : `Unavailable${e.reason ? `: ${e.reason}` : ''}`}</Ok>
              <span className="font-mono text-small text-studio-muted">×{(e.scales ?? []).join(' / ×')}</span>
              {e.id === opts.data.default_engine && <span className="text-small text-studio-muted">· default</span>}
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}
