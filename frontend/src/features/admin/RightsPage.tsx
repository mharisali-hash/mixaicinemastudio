import { useMemo, useState } from 'react'
import { Link } from 'react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Search } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { EmptyState, ErrorState } from '@/components/studio/states'
import { qk } from '@/hooks/keys'
import { useMe } from '@/hooks/useAuth'
import { api } from '@/lib/api'
import type { FeatureKey, ModelType, RightsOverview } from '@/lib/types'
import { Notice } from '@/features/auth/AuthLayout'
import { announce } from '@/stores/ui'
import { AdminPage, linkButton, tableWrap, td, th } from './shared'

type Pkg = RightsOverview['packages'][number]
type Draft = { features: Partial<Record<FeatureKey, boolean>>; models: string[] | null }

const TYPE_LABEL: Record<ModelType, string> = { image: 'Image', edit: 'Edit', video: 'Video', upscale: 'Upscale', audio: 'Audio', model3d: '3D' }

// arrow keys walk the grid's checkboxes like a spreadsheet; Tab still leaves it
function gridKeys(e: React.KeyboardEvent<HTMLTableElement>) {
  const el = e.target as HTMLElement
  const row = Number(el.dataset.row)
  const col = Number(el.dataset.col)
  if (Number.isNaN(row) || Number.isNaN(col)) return
  const step = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] }[e.key]
  if (!step) return
  const next = e.currentTarget.querySelector<HTMLElement>(`[data-row="${row + step[0]}"][data-col="${col + step[1]}"]`)
  if (next) {
    e.preventDefault()
    next.focus()
  }
}

function usePackageSave() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ pkg, draft }: { pkg: Pkg; draft: Draft }) => api.admin.patchPackage(pkg.id, { features: draft.features, models: draft.models }),
    onSuccess: (_, { pkg }) => {
      announce(`${pkg.name} saved`)
      void qc.invalidateQueries({ queryKey: qk.adminRights })
      void qc.invalidateQueries({ queryKey: ['admin-packages'] })
    },
  })
}

function SaveRow({ packages, drafts, onDiscard, colSpanBefore }: { packages: Pkg[]; drafts: Record<string, Draft>; onDiscard: (id: string) => void; colSpanBefore: number }) {
  const save = usePackageSave()
  if (!Object.keys(drafts).length) return null
  return (
    <tr className="border-t border-studio-border bg-studio-panel">
      <td className={td} colSpan={colSpanBefore}>
        <span className="text-small text-studio-muted">Unsaved changes</span>
      </td>
      {packages.map((p) => (
        <td key={p.id} className={td}>
          {drafts[p.id] && (
            <div className="flex gap-1">
              <Button size="sm" loading={save.isPending && save.variables?.pkg.id === p.id} onClick={() => save.mutate({ pkg: p, draft: drafts[p.id] }, { onSuccess: () => onDiscard(p.id) })}>
                Save
              </Button>
              <Button size="sm" variant="ghost" onClick={() => onDiscard(p.id)}>
                Discard
              </Button>
            </div>
          )}
        </td>
      ))}
    </tr>
  )
}

function ReleaseButton({ kind, keyName, label }: { kind: 'feature' | 'model'; keyName: string; label: string }) {
  const qc = useQueryClient()
  const release = useMutation({
    mutationFn: () => api.admin.release(kind, keyName),
    onSuccess: () => {
      announce(`${label} released to packages`)
      void qc.invalidateQueries({ queryKey: qk.adminRights })
    },
  })
  return (
    <Button size="sm" variant="secondary" loading={release.isPending} onClick={() => release.mutate()} aria-label={`Release ${label}`}>
      Release
    </Button>
  )
}

/** Admin › Rights (contract v42): what's waiting for release, and which package has which studio and model. */
export function RightsPage() {
  const { data: me } = useMe()
  const rights = useQuery({ queryKey: qk.adminRights, queryFn: api.admin.rights, enabled: !!me?.admin })
  const [q, setQ] = useState('')
  const [drafts, setDrafts] = useState<Record<string, Draft>>({})
  const data = rights.data
  const packages = useMemo(() => [...(data?.packages ?? [])].sort((a, b) => Number(a.custom) - Number(b.custom)), [data])
  const needle = q.trim().toLowerCase()
  const match = (label: string) => !needle || label.toLowerCase().includes(needle)

  if (rights.isError) return <ErrorState title="Couldn't load rights" error={rights.error} onRetry={() => rights.refetch()} />
  if (!data) return <p role="status" className="p-8 text-body text-studio-muted">Loading rights…</p>

  const allModels = data.models.map((m) => m.key)
  const draftOf = (p: Pkg): Draft => drafts[p.id] ?? { features: p.features, models: p.models }
  const change = (p: Pkg, next: Draft) => setDrafts((d) => ({ ...d, [p.id]: next }))
  const discard = (id: string) =>
    setDrafts((d) => {
      const rest = { ...d }
      delete rest[id]
      return rest
    })
  const featureOn = (p: Pkg, key: FeatureKey) => draftOf(p).features[key] !== false
  const modelOn = (p: Pkg, key: string) => {
    const list = draftOf(p).models
    return list === null || list.includes(key)
  }
  const setModel = (p: Pkg, key: string, on: boolean) => {
    const d = draftOf(p)
    const list = d.models ?? allModels
    const next = on ? [...new Set([...list, key])] : list.filter((k) => k !== key)
    change(p, { ...d, models: next.length === allModels.length ? null : next })
  }

  const studios = data.features.filter((f) => !f.own_switch && match(f.label))
  const waiting = [
    ...data.features.filter((f) => !f.released && !f.own_switch).map((f) => ({ kind: 'feature' as const, key: f.key, label: f.label, what: 'Studio' })),
    ...data.models.filter((m) => !m.released).map((m) => ({ kind: 'model' as const, key: m.key, label: m.label, what: `${TYPE_LABEL[m.type]} model` })),
  ].filter((w) => match(w.label))
  const types = [...new Set(data.models.map((m) => m.type))]

  return (
    <AdminPage
      id="rights-title"
      title="Rights"
      intro="Which studios and models each package has. Something new stays with admins until you release it; one person's exceptions live on their page under Users."
      actions={
        <div className="relative">
          <Search aria-hidden className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-studio-muted" />
          <Input aria-label="Search studios and models" placeholder="Search studios and models" value={q} onChange={(e) => setQ(e.target.value)} className="w-64 pl-8" />
        </div>
      }
    >
      <section aria-labelledby="waiting-title" className="flex flex-col gap-2">
        <h2 id="waiting-title" className="section-label">
          Waiting for release ({waiting.length})
        </h2>
        {waiting.length ? (
          <ul className={`${tableWrap} divide-y divide-studio-border`}>
            {waiting.map((w) => (
              <li key={`${w.kind}-${w.key}`} className="flex flex-wrap items-center gap-3 px-3 py-2 text-body">
                <span className="min-w-40 font-medium">{w.label}</span>
                <span className="text-studio-muted">{w.what}</span>
                <span className="flex-1 text-small text-studio-muted">Only admins and people you've switched it on for</span>
                <ReleaseButton kind={w.kind} keyName={w.key} label={w.label} />
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-body text-studio-muted">Nothing new is waiting.</p>
        )}
      </section>

      {!packages.length ? (
        <EmptyState title="No packages yet">Make one under Packages first.</EmptyState>
      ) : (
        <>
          <section aria-labelledby="studios-title" className="flex flex-col gap-2">
            <h2 id="studios-title" className="section-label">
              Studios
            </h2>
            <div className={tableWrap}>
              <table className="w-full text-body" onKeyDown={gridKeys}>
                <caption className="sr-only">Studios each package includes</caption>
                <thead className="bg-studio-panel text-left text-small text-studio-muted">
                  <tr>
                    <th scope="col" className={th}>
                      Studio
                    </th>
                    {packages.map((p) => (
                      <th key={p.id} scope="col" className={th}>
                        {p.name}
                        {p.custom && <span className="sr-only"> (custom)</span>}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {studios.map((f, row) => (
                    <tr key={f.key} className="border-t border-studio-border">
                      <th scope="row" className={`${td} text-left font-normal`}>
                        {f.label} {!f.released && <Badge tone="warning">Not released</Badge>}
                      </th>
                      {packages.map((p, col) => (
                        <td key={p.id} className={td}>
                          <input
                            type="checkbox"
                            className="size-4 accent-studio-accent"
                            data-row={row}
                            data-col={col}
                            aria-label={`${p.name}: ${f.label}`}
                            checked={featureOn(p, f.key)}
                            onChange={(e) => change(p, { ...draftOf(p), features: { ...draftOf(p).features, [f.key]: e.target.checked } })}
                          />
                        </td>
                      ))}
                    </tr>
                  ))}
                  <SaveRow packages={packages} drafts={drafts} onDiscard={discard} colSpanBefore={1} />
                </tbody>
              </table>
            </div>
          </section>

          <section aria-labelledby="models-title" className="flex flex-col gap-2">
            <h2 id="models-title" className="section-label">
              Models
            </h2>
            {types.map((type) => {
              const rows = data.models.filter((m) => m.type === type && match(m.label))
              if (!rows.length) return null
              return (
                <details key={type} className={tableWrap} open={!!needle}>
                  <summary className="cursor-pointer px-3 py-2 text-body font-medium focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-studio-gold/50">
                    {TYPE_LABEL[type]} ({rows.length})
                  </summary>
                  <table className="w-full text-body" onKeyDown={gridKeys}>
                    <caption className="sr-only">{TYPE_LABEL[type]} models each package includes</caption>
                    <thead className="bg-studio-panel text-left text-small text-studio-muted">
                      <tr>
                        <th scope="col" className={th}>
                          Model
                        </th>
                        {packages.map((p) => (
                          <th key={p.id} scope="col" className={th}>
                            {p.name}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((m, row) => (
                        <tr key={m.key} className="border-t border-studio-border">
                          <th scope="row" className={`${td} text-left font-normal`}>
                            {m.label} {!m.released && <Badge tone="warning">Not released</Badge>}
                          </th>
                          {packages.map((p, col) => (
                            <td key={p.id} className={td}>
                              <input
                                type="checkbox"
                                className="size-4 accent-studio-accent"
                                data-row={row}
                                data-col={col}
                                aria-label={`${p.name}: ${m.label}`}
                                checked={modelOn(p, m.key)}
                                onChange={(e) => setModel(p, m.key, e.target.checked)}
                              />
                            </td>
                          ))}
                        </tr>
                      ))}
                      <SaveRow packages={packages} drafts={drafts} onDiscard={discard} colSpanBefore={1} />
                    </tbody>
                  </table>
                </details>
              )
            })}
          </section>
        </>
      )}
      {Object.keys(drafts).length > 0 && <Notice tone="ok">Unsaved changes in {Object.keys(drafts).length} package(s). Save each column when you're done.</Notice>}
      <p className="text-small text-studio-muted">
        Changes apply from the next thing each person makes. <Link className={linkButton} to="/admin/packages">Packages</Link> has prices and limits.
      </p>
    </AdminPage>
  )
}
