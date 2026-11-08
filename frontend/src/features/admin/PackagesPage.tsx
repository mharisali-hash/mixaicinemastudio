import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Archive, ArchiveRestore, Copy, PackagePlus, Pencil, Boxes } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input, fieldClass } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { EmptyState, ErrorState } from '@/components/studio/states'
import { useCatalog } from '@/hooks/useModels'
import { api } from '@/lib/api'
import type { FeatureKey, ModelType, Package, PackageInput, PackageKind, PackagePeriod } from '@/lib/types'
import { cn } from '@/lib/utils'
import { Notice } from '@/features/auth/AuthLayout'
import { announce } from '@/stores/ui'
import { AdminPage, PERIOD_LABEL, minutes, pkr, tableWrap, td, th, usePackages } from './shared'

const FEATURES: { key: FeatureKey; label: string }[] = [
  { key: 'image', label: 'Image studio' },
  { key: 'video', label: 'Video studio' },
  { key: 'audio', label: 'Audio studio' },
  { key: 'character', label: 'Character Studio' },
  { key: 'ads', label: 'Ad Studio' },
  { key: 'poetry', label: 'Poetry Shorts' },
  { key: 'podcast', label: 'Podcasts' },
  { key: 'dub', label: 'Dubbing' },
  { key: 'stories', label: 'Stories' },
  { key: '3d', label: '3D Studio' },
  { key: 'api', label: 'API and MCP' },
  // contract v42; a studio that isn't released yet stays off whatever this says (Admin › Rights)
  { key: 'song_voices', label: 'Song voices' },
  { key: 'presenter', label: 'Presenter Video' },
  { key: 'print', label: 'Print Studio' },
  { key: 'bulk_edit', label: 'Bulk edit' },
]

const MODEL_TYPES: { type: ModelType; label: string }[] = [
  { type: 'image', label: 'Image' },
  { type: 'edit', label: 'Edit' },
  { type: 'video', label: 'Video' },
  { type: 'upscale', label: 'Upscale' },
  { type: 'audio', label: 'Audio' },
  { type: 'model3d', label: '3D' },
]

const BLANK: PackageInput = {
  name: '',
  description: '',
  price_pkr: 0,
  period: 'month',
  days: null,
  gpu_min_day: 60,
  gpu_min_month: null,
  max_queued: 3,
  private_items: false,
  models: null,
  features: {},
  kind: 'standard',
  public: false,
  sort: 10,
}

const asText = (n: number | null) => (n == null ? '' : String(n))
const asNum = (v: string) => (v.trim() === '' ? null : Number(v))

function PackageForm({ initial, onDone }: { initial: Package | null; onDone: () => void }) {
  const qc = useQueryClient()
  const catalog = useCatalog()
  const [f, setF] = useState<PackageInput>(() => (initial ? { ...BLANK, ...initial, features: { ...initial.features } } : BLANK))
  const [day, setDay] = useState(asText(f.gpu_min_day))
  const [month, setMonth] = useState(asText(f.gpu_min_month))
  const [queued, setQueued] = useState(asText(f.max_queued))
  const [days, setDays] = useState(asText(f.days))
  const set = <K extends keyof PackageInput>(k: K, v: PackageInput[K]) => setF((s) => ({ ...s, [k]: v }))
  const body = (): PackageInput => {
    // only the editable fields; the row's id, counts and dates stay out of the request
    const picked = Object.fromEntries(Object.keys(BLANK).map((k) => [k, f[k as keyof PackageInput]])) as unknown as PackageInput
    return { ...picked, name: f.name.trim(), gpu_min_day: asNum(day), gpu_min_month: asNum(month), max_queued: asNum(queued), days: asNum(days) }
  }
  const save = useMutation({
    mutationFn: () => (initial ? api.admin.patchPackage(initial.id, body()) : api.admin.createPackage(body())),
    onSuccess: (p) => {
      announce(initial ? `${p.name} saved` : `${p.name} created`)
      void qc.invalidateQueries({ queryKey: ['admin-packages'] })
      onDone()
    },
  })
  const allModels = f.models == null
  const toggleModel = (id: string, on: boolean) => set('models', on ? [...(f.models ?? []), id] : (f.models ?? []).filter((m) => m !== id))
  const demo = f.kind === 'demo'
  return (
    <form
      className="flex flex-col gap-3"
      noValidate
      onSubmit={(e) => {
        e.preventDefault()
        if (f.name.trim()) save.mutate()
      }}
    >
      {save.isError && <Notice>{save.error.message}</Notice>}
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="pkg-name">Name</Label>
          <Input id="pkg-name" required value={f.name} onChange={(e) => set('name', e.target.value)} />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="pkg-kind">Kind</Label>
          <select id="pkg-kind" className={cn(fieldClass, 'h-8')} value={f.kind} onChange={(e) => set('kind', e.target.value as PackageKind)} disabled={initial?.kind === 'custom'}>
            <option value="standard">Standard</option>
            <option value="demo">Demo (once each)</option>
            {initial?.kind === 'custom' && <option value="custom">Custom</option>}
          </select>
        </div>
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="pkg-desc">Description</Label>
        <Textarea id="pkg-desc" rows={2} value={f.description} onChange={(e) => set('description', e.target.value)} />
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="pkg-price">Price (PKR)</Label>
          <Input id="pkg-price" type="number" min={0} step={1} value={f.price_pkr} onChange={(e) => set('price_pkr', Math.max(0, Math.round(Number(e.target.value) || 0)))} />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="pkg-period">Billed</Label>
          <select id="pkg-period" className={cn(fieldClass, 'h-8')} value={f.period} onChange={(e) => set('period', e.target.value as PackagePeriod)}>
            {(['day', 'week', 'month', 'year', 'none'] as PackagePeriod[]).map((p) => (
              <option key={p} value={p}>
                {p === 'none' ? 'One-off' : `Every ${p}`}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="pkg-days">{demo ? 'Demo length (days)' : 'Lasts (days)'}</Label>
          <Input id="pkg-days" type="number" min={1} value={days} placeholder={f.period === 'none' ? 'Until changed' : 'The period'} onChange={(e) => setDays(e.target.value)} />
        </div>
      </div>
      <fieldset className="grid gap-3 sm:grid-cols-3">
        <legend className="mb-1 text-small font-medium text-studio-muted">Limits (empty means no limit)</legend>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="pkg-day">GPU min a day</Label>
          <Input id="pkg-day" type="number" min={0} value={day} onChange={(e) => setDay(e.target.value)} />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="pkg-month">GPU min a month</Label>
          <Input id="pkg-month" type="number" min={0} value={month} onChange={(e) => setMonth(e.target.value)} />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="pkg-queued">Jobs at once</Label>
          <Input id="pkg-queued" type="number" min={0} value={queued} onChange={(e) => setQueued(e.target.value)} />
        </div>
      </fieldset>
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-small font-medium text-studio-muted">Studios</legend>
        <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 sm:grid-cols-3">
          {FEATURES.map((ft) => (
            <label key={ft.key} className="flex items-center gap-2 text-body">
              <input
                type="checkbox"
                className="size-4 accent-studio-accent"
                checked={f.features[ft.key] !== false}
                onChange={(e) => set('features', { ...f.features, [ft.key]: e.target.checked })}
              />
              {ft.label}
            </label>
          ))}
        </div>
      </fieldset>
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-small font-medium text-studio-muted">Models</legend>
        <label className="flex items-center gap-3 text-body">
          <Switch checked={allModels} onCheckedChange={(v) => set('models', v ? null : [])} aria-label="Every model" />
          Every model
        </label>
        {!allModels && (
          <div className="flex max-h-56 flex-col gap-2 overflow-y-auto rounded-[6px] border border-studio-border p-2">
            {MODEL_TYPES.map(({ type, label }) => (
              <div key={type}>
                <p className="text-small font-medium text-studio-muted">{label}</p>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1">
                  {catalog[type]
                    .filter((m) => m.id !== 'auto')
                    .map((m) => (
                      <label key={m.id} className="flex items-center gap-2 text-body">
                        <input
                          type="checkbox"
                          className="size-4 accent-studio-accent"
                          checked={(f.models ?? []).includes(m.id)}
                          onChange={(e) => toggleModel(m.id, e.target.checked)}
                        />
                        {m.label}
                      </label>
                    ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </fieldset>
      <div className="flex flex-wrap gap-x-6 gap-y-2">
        <label className="flex items-center gap-3 text-body">
          <Switch checked={f.public} onCheckedChange={(v) => set('public', v)} disabled={initial?.kind === 'custom'} />
          Show on the website
        </label>
        <label className="flex items-center gap-3 text-body">
          <Switch checked={f.private_items} onCheckedChange={(v) => set('private_items', v)} />
          Private items
        </label>
        <div className="flex items-center gap-2">
          <Label htmlFor="pkg-sort">Order</Label>
          <Input id="pkg-sort" type="number" className="w-20" value={f.sort} onChange={(e) => set('sort', Math.round(Number(e.target.value) || 0))} />
        </div>
      </div>
      <DialogFooter>
        <Button type="button" variant="secondary" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={save.isPending} disabled={!f.name.trim()}>
          {initial ? 'Save package' : 'Create package'}
        </Button>
      </DialogFooter>
    </form>
  )
}

export function PackagesPage() {
  const qc = useQueryClient()
  const [archived, setArchived] = useState(false)
  const [editing, setEditing] = useState<Package | 'new' | null>(null)
  const packages = usePackages(archived)
  const act = useMutation({
    mutationFn: ({ id, what }: { id: string; what: 'duplicate' | 'archive' | 'restore' }) =>
      what === 'duplicate' ? api.admin.duplicatePackage(id) : what === 'archive' ? api.admin.archivePackage(id) : api.admin.restorePackage(id),
    onSuccess: (p, { what }) => {
      announce(what === 'duplicate' ? `${p.name} made` : what === 'archive' ? `${p.name} archived` : `${p.name} restored`)
      return qc.invalidateQueries({ queryKey: ['admin-packages'] })
    },
  })
  const rows = packages.data ?? []
  return (
    <AdminPage
      id="packages-title"
      title="Packages"
      intro="What each person may do: GPU minutes, jobs at once, models and studios. Prices are in rupees."
      actions={
        <Button variant="primary" onClick={() => setEditing('new')}>
          <PackagePlus aria-hidden />
          New package
        </Button>
      }
    >
      <label className="flex items-center gap-3 self-start text-body">
        <Switch checked={archived} onCheckedChange={setArchived} />
        Show archived packages
      </label>
      {act.isError && <Notice>{act.error.message}</Notice>}
      {packages.isError ? (
        <ErrorState title="Couldn't load the packages" error={packages.error} onRetry={() => packages.refetch()} />
      ) : packages.isPending ? (
        <p role="status" className="text-body text-studio-muted">
          Loading packages…
        </p>
      ) : rows.length === 0 ? (
        <EmptyState icon={<Boxes />} title="No packages">
          Make the first one.
        </EmptyState>
      ) : (
        <div className={tableWrap}>
          <table className="w-full min-w-[900px] border-collapse text-left text-body">
            <caption className="sr-only">Packages, {rows.length} shown</caption>
            <thead className="bg-studio-panel text-small text-studio-muted">
              <tr>
                <th scope="col" className={th}>Name</th>
                <th scope="col" className={th}>Price</th>
                <th scope="col" className={cn(th, 'text-right')}>GPU a day</th>
                <th scope="col" className={cn(th, 'text-right')}>Jobs at once</th>
                <th scope="col" className={cn(th, 'text-right')}>People on it</th>
                <th scope="col" className={th}>Shown</th>
                <th scope="col" className={th}>
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => (
                <tr key={p.id} className={cn('border-t border-studio-border', !p.active && 'text-studio-muted')}>
                  <th scope="row" className={cn(td, 'font-medium')}>
                    {p.name}
                    <span className="ml-2 inline-flex gap-1 align-middle">
                      {p.kind !== 'standard' && <Badge tone="accent">{p.kind === 'demo' ? `Demo · ${p.days ?? 7} days` : 'Custom'}</Badge>}
                      {p.slug === 'free' && <Badge>Everyone’s fallback</Badge>}
                      {!p.active && <Badge tone="warning">Archived</Badge>}
                    </span>
                  </th>
                  <td className={cn(td, 'whitespace-nowrap')}>
                    {pkr(p.price_pkr)}
                    {p.price_pkr > 0 && <span className="text-studio-muted"> {PERIOD_LABEL[p.period]}</span>}
                  </td>
                  <td className={cn(td, 'text-right tabular-nums')}>{minutes(p.gpu_min_day)}</td>
                  <td className={cn(td, 'text-right tabular-nums')}>{p.max_queued ?? 'No limit'}</td>
                  <td className={cn(td, 'text-right tabular-nums')}>{p.subscribers}</td>
                  <td className={td}>{p.public ? 'On the website' : 'Clients only'}</td>
                  <td className={cn(td, 'whitespace-nowrap text-right')}>
                    <Button size="sm" variant="ghost" onClick={() => setEditing(p)} aria-label={`Edit ${p.name}`}>
                      <Pencil aria-hidden />
                      Edit
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => act.mutate({ id: p.id, what: 'duplicate' })} aria-label={`Duplicate ${p.name}`}>
                      <Copy aria-hidden />
                      Duplicate
                    </Button>
                    {p.slug !== 'free' &&
                      (p.active ? (
                        <Button size="sm" variant="ghost" onClick={() => act.mutate({ id: p.id, what: 'archive' })} aria-label={`Archive ${p.name}`}>
                          <Archive aria-hidden />
                          Archive
                        </Button>
                      ) : (
                        <Button size="sm" variant="ghost" onClick={() => act.mutate({ id: p.id, what: 'restore' })} aria-label={`Restore ${p.name}`}>
                          <ArchiveRestore aria-hidden />
                          Restore
                        </Button>
                      ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Dialog open={editing !== null} onOpenChange={(o) => !o && setEditing(null)}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{editing === 'new' ? 'New package' : `Edit ${editing?.name ?? ''}`}</DialogTitle>
            <DialogDescription>People already on a package see a change straight away. Archiving only stops it being given out.</DialogDescription>
          </DialogHeader>
          {editing !== null && <PackageForm key={editing === 'new' ? 'new' : editing.id} initial={editing === 'new' ? null : editing} onDone={() => setEditing(null)} />}
        </DialogContent>
      </Dialog>
    </AdminPage>
  )
}
