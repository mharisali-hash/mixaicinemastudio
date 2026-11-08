import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/alert-dialog'
import { Input, fieldClass } from '@/components/ui/input'
import { Progress } from '@/components/ui/progress'
import { ErrorState } from '@/components/studio/states'
import { useMe } from '@/hooks/useAuth'
import { announce } from '@/stores/ui'
import { cn } from '@/lib/utils'
import { AdminPage, linkButton } from '../shared'
import { ACTION_LABEL, STATUS, bulkApi, bulkKey, uploadBulkZip, uploadPrompts, type BulkBatch, type BulkItem, type BulkOptions, type PromptsResult } from './api'
import { BulkViewer } from './BulkViewer'
import { useBulkUpload } from './useBulkUpload'

const card = 'flex flex-col gap-3 rounded-[8px] border border-studio-border bg-studio-raised p-4'
const n = (v: number) => v.toLocaleString('en-GB')
const EDGES = [
  { value: 0, label: 'Sharp edge' },
  { value: 0.6, label: 'Soft edge' },
  { value: 3, label: 'Very soft edge (hair, fur)' },
]

function Step({ n: num, title, children, id }: { n: number; title: string; children: React.ReactNode; id: string }) {
  return (
    <section className={card} aria-labelledby={id}>
      <h2 id={id} className="flex items-center gap-2 text-panel font-display font-semibold">
        <span aria-hidden className="grid size-6 place-items-center rounded-full bg-studio-accent-soft text-small text-studio-accent-hover">
          {num}
        </span>
        {title}
      </h2>
      {children}
    </section>
  )
}

/** Admin: one bulk batch, top to bottom: pictures, settings, try on three, run, export. */
export function BulkBatchPage() {
  const { id = '' } = useParams()
  const { data: me } = useMe()
  const qc = useQueryClient()
  const nav = useNavigate()
  const batchKey = [...bulkKey, id]
  const [busySamples, setBusySamples] = useState(false)
  const batch = useQuery({
    queryKey: batchKey,
    queryFn: () => bulkApi.get(id),
    enabled: !!me?.admin && !!id,
    refetchInterval: (q) => (q.state.data?.status === 'running' || busySamples ? 3000 : 15_000),
  })
  const refresh = () => {
    qc.invalidateQueries({ queryKey: batchKey })
    qc.invalidateQueries({ queryKey: [...batchKey, 'items'] })
  }
  const upload = useBulkUpload(id, refresh)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const remove = useMutation({ mutationFn: () => bulkApi.remove(id), onSuccess: () => nav('/admin/bulk') })

  if (batch.error) return <AdminPage id="bulk-batch" title="Bulk" intro=""><ErrorState error={batch.error} onRetry={() => batch.refetch()} /></AdminPage>
  const b = batch.data
  if (!b) return <AdminPage id="bulk-batch" title="Bulk" intro="Loading…"><Progress value={null} label="Loading the batch" /></AdminPage>
  const locked = b.status === 'running'

  return (
    <AdminPage
      id="bulk-batch"
      title={b.name}
      intro={`${ACTION_LABEL[b.action]} · ${n(b.counts.total)} pictures`}
      actions={
        <div className="flex items-center gap-2">
          <Badge tone={STATUS[b.status].tone}>{STATUS[b.status].label}</Badge>
          <Link to="/admin/bulk" className={cn(linkButton, 'text-body')}>
            All batches
          </Link>
          <Button variant="ghost" size="sm" onClick={() => setConfirmDelete(true)}>
            Delete
          </Button>
        </div>
      }
    >
      <PicturesStep b={b} upload={upload} onZip={refresh} />
      {/* remounted when the saved settings change, so the form starts from them */}
      <SettingsStep key={JSON.stringify(b.options)} b={b} locked={locked} onSaved={refresh} />
      <SamplesStep b={b} locked={locked} onBusy={setBusySamples} />
      <RunStep b={b} />
      <ExportStep b={b} />
      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title={`Delete “${b.name}”?`}
        description={`Its ${n(b.counts.total)} pictures, every result and every export ZIP are deleted from the server. This can't be undone.`}
        confirmLabel="Delete batch"
        tone="danger"
        onConfirm={() => remove.mutate()}
      />
    </AdminPage>
  )
}

// ---------------------------------------------------------------- 1. pictures

function PicturesStep({ b, upload, onZip }: { b: BulkBatch; upload: ReturnType<typeof useBulkUpload>; onZip: () => void }) {
  const folder = useRef<HTMLInputElement>(null)
  const files = useRef<HTMLInputElement>(null)
  const zip = useRef<HTMLInputElement>(null)
  const [zipState, setZipState] = useState<{ progress: number; error?: string; done?: boolean } | null>(null)
  const { state } = upload
  const handled = state.sent + state.skipped + state.failed.length
  const pick = (input: HTMLInputElement | null) => {
    const list = input?.files ? Array.from(input.files) : []
    if (input) input.value = ''
    if (list.length) void upload.start(list)
  }
  const sendZip = () => {
    const f = zip.current?.files?.[0]
    if (zip.current) zip.current.value = ''
    if (!f) return
    setZipState({ progress: 0 })
    uploadBulkZip(b.id, f, (p) => setZipState({ progress: p }))
      .promise.then(() => {
        setZipState({ progress: 1, done: true })
        onZip()
      })
      .catch((e: Error) => setZipState({ progress: 0, error: e.message }))
  }
  const off = b.status === 'cancelled' || state.running

  return (
    <Step n={1} title="Pictures" id="bulk-step-pictures">
      <p className="text-body text-studio-muted">
        Pick the folder: pictures go up four at a time and nothing is kept in the browser. If the connection drops, pick the same folder
        again and it carries on where it stopped. PNG, JPEG, WebP, HEIC, TIFF and camera RAW.
      </p>
      <div className="flex flex-wrap gap-2">
        <Button variant="primary" onClick={() => folder.current?.click()} disabled={off}>
          Choose a folder
        </Button>
        <Button onClick={() => files.current?.click()} disabled={off}>
          Choose pictures
        </Button>
        <Button variant="outline" onClick={() => zip.current?.click()} disabled={off || (zipState != null && !zipState.done && !zipState.error)}>
          Upload a ZIP
        </Button>
        {/* webkitdirectory isn't in React's types; set it as a plain attribute */}
        <input ref={folder} type="file" className="sr-only" tabIndex={-1} aria-hidden {...{ webkitdirectory: '', directory: '' }} multiple onChange={(e) => pick(e.currentTarget)} />
        <input ref={files} type="file" className="sr-only" tabIndex={-1} aria-hidden multiple accept="image/*,.heic,.heif,.tif,.tiff,.dng,.cr2,.cr3,.nef,.arw,.raf" onChange={(e) => pick(e.currentTarget)} />
        <input ref={zip} type="file" className="sr-only" tabIndex={-1} aria-hidden accept=".zip,application/zip" onChange={sendZip} />
      </div>
      <p className="text-body tabular-nums" aria-live="polite">
        {n(b.counts.total)} pictures in this batch
      </p>

      {(state.total > 0 || state.checking) && (
        <div className="flex flex-col gap-2" role="status" aria-live="polite">
          <Progress value={state.checking ? null : state.total ? handled / state.total : 0} label="Upload progress" />
          <p className="text-small text-studio-muted tabular-nums">
            {state.checking
              ? `Checking which of ${n(state.total)} pictures are already here…`
              : `${n(state.sent)} sent · ${n(state.skipped)} already here · ${n(state.failed.length)} failed · ${n(state.total)} pictures picked`}
            {state.ignored > 0 && ` · ${n(state.ignored)} other files left out`}
          </p>
          <div className="flex gap-2">
            {state.running && (
              <Button size="sm" onClick={upload.stop}>
                Stop
              </Button>
            )}
            {!state.running && state.failed.length > 0 && (
              <Button size="sm" onClick={upload.retryFailed}>
                Retry {n(state.failed.length)} failed
              </Button>
            )}
          </div>
          {state.failed.length > 0 && (
            <details className="text-small">
              <summary className="cursor-pointer text-studio-danger">What failed</summary>
              <ul className="mt-1 max-h-40 overflow-y-auto">
                {state.failed.slice(0, 200).map((f) => (
                  <li key={f.name}>
                    <span className="font-mono">{f.name}</span> — {f.error}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
      {zipState && (
        <div className="flex flex-col gap-1" role="status">
          {zipState.error ? (
            <p className="text-small text-studio-danger">{zipState.error}</p>
          ) : zipState.done ? (
            <p className="text-small text-studio-muted">ZIP received; the server is unpacking it. The count above goes up as it does.</p>
          ) : (
            <Progress value={zipState.progress} label="ZIP upload progress" />
          )}
        </div>
      )}
    </Step>
  )
}

// ---------------------------------------------------------------- 2. settings

function SettingsStep({ b, locked, onSaved }: { b: BulkBatch; locked: boolean; onSaved: () => void }) {
  const [opts, setOpts] = useState<BulkOptions>(b.options)
  const [csv, setCsv] = useState<PromptsResult | { error: string } | null>(null)
  const csvInput = useRef<HTMLInputElement>(null)
  const save = useMutation({ mutationFn: () => bulkApi.patch(b.id, { options: opts }), onSuccess: onSaved })
  const set = <K extends keyof BulkOptions>(k: K, v: BulkOptions[K]) => setOpts((o) => ({ ...o, [k]: v }))
  const dirty = JSON.stringify(opts) !== JSON.stringify(b.options)
  const sendCsv = () => {
    const f = csvInput.current?.files?.[0]
    if (csvInput.current) csvInput.current.value = ''
    if (!f) return
    uploadPrompts(b.id, f)
      .promise.then((r) => {
        setCsv(r)
        onSaved()
      })
      .catch((e: Error) => setCsv({ error: e.message }))
  }

  return (
    <Step n={2} title="Settings" id="bulk-step-settings">
      {locked && <p className="text-small text-studio-warning">Pause the batch to change its settings.</p>}
      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate()
        }}
      >
        <fieldset disabled={locked} className="flex flex-col gap-3">
          {b.action === 'edit' ? (
            <>
              <div className="flex flex-col gap-1.5">
                <label htmlFor="bulk-instruction" className="section-label">
                  Instruction for every picture
                </label>
                <textarea
                  id="bulk-instruction"
                  rows={3}
                  className={cn(fieldClass, 'py-1.5')}
                  placeholder="e.g. Put the product on a plain white background with a soft shadow"
                  value={opts.instruction ?? ''}
                  maxLength={1200}
                  onChange={(e) => set('instruction', e.target.value)}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <label htmlFor="bulk-magic" className="section-label">
                  Magic prompt
                </label>
                <select id="bulk-magic" className={cn(fieldClass, 'h-8 max-w-xs pr-7')} value={opts.magic_prompt ?? 'off'} onChange={(e) => set('magic_prompt', e.target.value as BulkOptions['magic_prompt'])}>
                  <option value="off">Off: the instruction as written</option>
                  <option value="auto">Auto</option>
                  <option value="on">On: the writer improves each one</option>
                </select>
              </div>
            </>
          ) : (
            <div className="flex flex-col gap-1.5">
              <label htmlFor="bulk-edge" className="section-label">
                Edge
              </label>
              <select id="bulk-edge" className={cn(fieldClass, 'h-8 max-w-xs pr-7')} value={String(opts.feather ?? 0.6)} onChange={(e) => set('feather', Number(e.target.value))}>
                {EDGES.map((x) => (
                  <option key={x.value} value={x.value}>
                    {x.label}
                  </option>
                ))}
              </select>
            </div>
          )}
          <div className="flex flex-col gap-1.5">
            <label htmlFor="bulk-keywords" className="section-label">
              Keywords for every caption
            </label>
            <Input id="bulk-keywords" placeholder="e.g. product photo, studio light" value={opts.keywords ?? ''} maxLength={500} onChange={(e) => set('keywords', e.target.value)} />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" variant="primary" disabled={!dirty} loading={save.isPending}>
              Save settings
            </Button>
            <Button type="button" onClick={() => csvInput.current?.click()}>
              Upload prompts CSV
            </Button>
            <input ref={csvInput} type="file" className="sr-only" tabIndex={-1} aria-hidden accept=".csv,text/csv" onChange={sendCsv} />
          </div>
        </fieldset>
        <p className="text-small text-studio-muted">
          A CSV with the columns <span className="font-mono">filename, instruction, keywords</span> gives pictures their own prompt; the rest use
          the settings above.
        </p>
        {save.error && <p className="text-small text-studio-danger" role="alert">{save.error.message}</p>}
        {csv && (
          <p className={cn('text-small', 'error' in csv ? 'text-studio-danger' : 'text-studio-muted')} role="status">
            {'error' in csv
              ? csv.error
              : `${n(csv.matched)} of ${n(csv.rows)} rows matched a picture.${csv.unmatched_count ? ` Not found: ${csv.unmatched.slice(0, 5).join(', ')}${csv.unmatched_count > 5 ? '…' : ''}` : ''}`}
          </p>
        )}
      </form>
    </Step>
  )
}

// ---------------------------------------------------------------- 3. try on three

function Pair({ item, action, onOpen }: { item: BulkItem; action: BulkBatch['action']; onOpen: () => void }) {
  const busy = item.status === 'queued' || item.status === 'running'
  return (
    <figure className="flex flex-col gap-1.5">
      <button
        type="button"
        onClick={onOpen}
        aria-label={`View ${item.filename} full size`}
        className="grid cursor-zoom-in grid-cols-2 gap-1 rounded-[6px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-studio-gold/50"
      >
        {item.source_url && <img src={`${item.source_url}?w=256`} alt={`${item.filename}, before`} className="aspect-square w-full rounded-[6px] bg-studio-panel object-contain" loading="lazy" />}
        {item.result_url ? (
          <img
            src={`${item.result_url}?w=512`}
            alt={`${item.filename}, after`}
            className={cn('aspect-square w-full rounded-[6px] object-contain', action === 'cutout' ? 'checker' : 'bg-studio-panel')}
            loading="lazy"
          />
        ) : (
          <div className="grid aspect-square place-items-center rounded-[6px] bg-studio-panel text-small text-studio-muted">
            {busy ? 'Working…' : item.status === 'failed' ? 'Failed' : 'Not run'}
          </div>
        )}
      </button>
      <figcaption className="truncate text-small text-studio-muted" title={item.filename}>
        {item.filename}
        {item.error && <span className="block text-studio-danger">{item.error}</span>}
      </figcaption>
    </figure>
  )
}

function SamplesStep({ b, locked, onBusy }: { b: BulkBatch; locked: boolean; onBusy: (v: boolean) => void }) {
  const qc = useQueryClient()
  const key = [...bulkKey, b.id, 'items', 'samples']
  const samples = useQuery({
    queryKey: key,
    queryFn: () => bulkApi.items(b.id, { status: 'samples' }),
    refetchInterval: (q) => (q.state.data?.items.some((i) => i.status === 'queued' || i.status === 'running') ? 3000 : false),
  })
  const busy = !!samples.data?.items.some((i) => i.status === 'queued' || i.status === 'running')
  useEffect(() => onBusy(busy), [busy, onBusy])
  const run = useMutation({ mutationFn: () => bulkApi.preview(b.id), onSuccess: () => qc.invalidateQueries({ queryKey: key }) })
  const [viewing, setViewing] = useState<number | null>(null)
  return (
    <Step n={3} title="Try on three pictures" id="bulk-step-samples">
      <p className="text-body text-studio-muted">Check the result on three pictures before spending GPU hours on all of them.</p>
      <div>
        <Button onClick={() => run.mutate()} disabled={locked || b.counts.total === 0 || busy} loading={run.isPending}>
          {samples.data?.items.length ? 'Try again with these settings' : 'Try on three'}
        </Button>
      </div>
      {run.error && <p className="text-small text-studio-danger" role="alert">{run.error.message}</p>}
      {!!samples.data?.items.length && (
        <div className="grid gap-3 sm:grid-cols-3">
          {samples.data.items.map((i, k) => (
            <Pair key={i.id} item={i} action={b.action} onOpen={() => setViewing(k)} />
          ))}
        </div>
      )}
      <BulkViewer items={samples.data?.items ?? []} index={viewing} onIndex={setViewing} action={b.action} />
    </Step>
  )
}

// ---------------------------------------------------------------- 4. run

const FILTERS = [
  { value: '', label: 'All' },
  { value: 'done', label: 'Done' },
  { value: 'failed,cancelled', label: 'Failed' },
  { value: 'queued,running', label: 'Working' },
  { value: 'uploaded', label: 'Waiting' },
]

function etaText(b: BulkBatch) {
  if (b.status !== 'running' || b.eta_s == null) return null
  const mins = b.eta_s / 60
  return mins < 60 ? `about ${Math.max(1, Math.ceil(mins))} min left` : `about ${(mins / 60).toFixed(1)} h left`
}

function RunStep({ b }: { b: BulkBatch }) {
  const qc = useQueryClient()
  const [filter, setFilter] = useState('')
  const act = useMutation({
    mutationFn: (what: 'run' | 'pause' | 'cancel' | 'retryFailed') => bulkApi[what](b.id),
    onSuccess: (out) => qc.setQueryData([...bulkKey, b.id], out),
  })
  const [confirmCancel, setConfirmCancel] = useState(false)
  const c = b.counts
  const ended = c.done + c.failed + c.cancelled
  const eta = etaText(b)
  // a screen reader hears every fifty pictures, not every one
  const fifty = Math.floor(c.done / 50)
  useEffect(() => {
    if (b.status === 'running' && fifty > 0) announce(`${n(fifty * 50)} of ${n(c.total)} pictures done`)
  }, [fifty, b.status, c.total])
  const items = useInfiniteQuery({
    queryKey: [...bulkKey, b.id, 'items', filter],
    queryFn: ({ pageParam }) => bulkApi.items(b.id, { status: filter || undefined, after: pageParam, limit: 60 }),
    initialPageParam: 0,
    getNextPageParam: (last) => last.next ?? undefined,
    refetchInterval: b.status === 'running' ? 10_000 : false,
  })
  const rows = items.data?.pages.flatMap((p) => p.items) ?? []
  const [viewing, setViewing] = useState<number | null>(null)

  return (
    <Step n={4} title="Run" id="bulk-step-run">
      <p className="text-body text-studio-muted">
        Runs behind everything else: any picture, video or API request made meanwhile, yours included, goes first and the batch carries on after it.
      </p>
      <div className="flex flex-wrap gap-2">
        {b.status === 'running' ? (
          <>
            <Button onClick={() => act.mutate('pause')} loading={act.isPending && act.variables === 'pause'}>
              Pause
            </Button>
            <Button variant="ghost" onClick={() => setConfirmCancel(true)}>
              Cancel
            </Button>
          </>
        ) : (
          <Button variant="primary" onClick={() => act.mutate('run')} disabled={c.uploaded === 0 || b.status === 'cancelled'} loading={act.isPending && act.variables === 'run'}>
            {b.status === 'paused' ? `Resume (${n(c.uploaded)} left)` : `Run all ${n(c.uploaded)}`}
          </Button>
        )}
        {c.failed + c.cancelled > 0 && b.status !== 'running' && (
          <Button onClick={() => act.mutate('retryFailed')}>Retry {n(c.failed + c.cancelled)} failed</Button>
        )}
      </div>
      {act.error && <p className="text-small text-studio-danger" role="alert">{act.error.message}</p>}
      <Progress value={c.total ? ended / c.total : 0} label="Batch progress" />
      <p className="text-small tabular-nums text-studio-muted">
        {n(c.done)} done · {n(c.failed)} failed · {n(c.queued + c.running)} working · {n(c.uploaded)} waiting
        {eta && ` · ${eta}`}
      </p>

      <div className="flex items-center gap-2">
        <label htmlFor="bulk-filter" className="section-label">
          Show
        </label>
        <select id="bulk-filter" className={cn(fieldClass, 'h-8 w-auto pr-7')} value={filter} onChange={(e) => setFilter(e.target.value)}>
          {FILTERS.map((f) => (
            <option key={f.value} value={f.value}>
              {f.label}
            </option>
          ))}
        </select>
      </div>
      {rows.length === 0 ? (
        <p className="text-small text-studio-muted">Nothing here yet.</p>
      ) : (
        <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4" aria-label="Pictures">
          {rows.map((i, k) => (
            <li key={i.id}>
              <Pair item={i} action={b.action} onOpen={() => setViewing(k)} />
            </li>
          ))}
        </ul>
      )}
      <BulkViewer items={rows} index={viewing != null && viewing < rows.length ? viewing : null} onIndex={setViewing} action={b.action} />
      {items.hasNextPage && (
        <div>
          <Button size="sm" onClick={() => items.fetchNextPage()} loading={items.isFetchingNextPage}>
            Show more
          </Button>
        </div>
      )}
      <ConfirmDialog
        open={confirmCancel}
        onOpenChange={setConfirmCancel}
        title="Cancel this batch?"
        description="Pictures already made are kept and can still be exported. The rest stop; Retry failed picks them up again later."
        confirmLabel="Cancel batch"
        tone="danger"
        onConfirm={() => act.mutate('cancel')}
      />
    </Step>
  )
}

// ---------------------------------------------------------------- 5. export

const size = (bytes: number) => (bytes > 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(1)} GB` : `${Math.max(1, Math.round(bytes / 1024 ** 2))} MB`)

function ExportStep({ b }: { b: BulkBatch }) {
  const qc = useQueryClient()
  const key = [...bulkKey, b.id, 'exports']
  const [originals, setOriginals] = useState(false)
  const [captions, setCaptions] = useState(true)
  const list = useQuery({
    queryKey: key,
    queryFn: () => bulkApi.exports(b.id),
    refetchInterval: (q) => (q.state.data?.exports.some((e) => e.status === 'queued' || e.status === 'running') ? 3000 : false),
  })
  const start = useMutation({ mutationFn: () => bulkApi.exportStart(b.id, { originals, captions }), onSuccess: () => qc.invalidateQueries({ queryKey: key }) })
  return (
    <Step n={5} title="Export" id="bulk-step-export">
      <p className="text-body text-studio-muted">
        ZIPs of up to 1,000 pictures each, with <span className="font-mono">metadata.jsonl</span> and <span className="font-mono">captions.csv</span>{' '}
        (file, original name, instruction, the prompt the model got, keywords, size, seed, model).
      </p>
      <div className="flex flex-wrap items-center gap-4">
        <label className="flex items-center gap-2 text-body">
          <input type="checkbox" className="size-4 accent-studio-accent" checked={originals} onChange={(e) => setOriginals(e.target.checked)} />
          Include the originals (before / after pairs)
        </label>
        <label className="flex items-center gap-2 text-body">
          <input type="checkbox" className="size-4 accent-studio-accent" checked={captions} onChange={(e) => setCaptions(e.target.checked)} />
          A caption .txt beside each picture
        </label>
        <Button variant="primary" onClick={() => start.mutate()} disabled={b.counts.done === 0} loading={start.isPending}>
          Make ZIPs of {n(b.counts.done)} pictures
        </Button>
      </div>
      {start.error && <p className="text-small text-studio-danger" role="alert">{start.error.message}</p>}
      {!!list.data?.exports.length && (
        <ul className="flex flex-col gap-2" aria-label="Exports">
          {list.data.exports.map((e) => (
            <li key={e.job_id} className="flex flex-col gap-1 rounded-[6px] border border-studio-border p-2">
              <span className="text-small text-studio-muted">
                {new Date(e.created_at).toLocaleString('en-GB')} {e.options.originals ? '· with originals' : ''}
              </span>
              {e.status === 'done' ? (
                <div className="flex flex-wrap gap-2">
                  {e.parts.map((p) => (
                    <a key={p.n} href={p.url} download className={cn(linkButton, 'text-body')}>
                      Part {p.n} of {e.parts.length} · {n(p.count)} pictures · {size(p.bytes)}
                    </a>
                  ))}
                </div>
              ) : e.status === 'failed' ? (
                <span className="text-small text-studio-danger">{e.error}</span>
              ) : (
                <Progress value={e.progress || null} label="Export progress" />
              )}
            </li>
          ))}
        </ul>
      )}
    </Step>
  )
}
