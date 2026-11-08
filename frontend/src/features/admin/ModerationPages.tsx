import { useState } from 'react'
import { Link } from 'react-router'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AudioLines, Ban, Check, Eye, ShieldAlert, Trash2, Undo2 } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input, fieldClass } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { ChipGroup } from '@/components/studio/chip'
import { EmptyState, ErrorState } from '@/components/studio/states'
import { qk } from '@/hooks/keys'
import { api } from '@/lib/api'
import type { ModPerson, ReviewItem, ReviewState } from '@/lib/types'
import { cn } from '@/lib/utils'
import { Notice } from '@/features/auth/AuthLayout'
import { announce } from '@/stores/ui'
import { AdminPage, linkButton, tableWrap, td, th, when } from './shared'

// the reasons that come up most; the moderator can always write their own
const PRESETS = [
  'Sexual or revealing content',
  'Depicts a revered religious figure',
  'Hateful or abusive content',
  'Someone else’s likeness or work without permission',
  'Spam or misuse of the studio',
]

type ReasonMode = 'remove' | 'warn' | 'block' | 'unblock'
const MODE_TEXT: Record<ReasonMode, { title: string; action: string; tone: 'primary' | 'danger' }> = {
  remove: { title: 'Remove this item', action: 'Remove', tone: 'danger' },
  warn: { title: 'Send a warning', action: 'Send warning', tone: 'primary' },
  block: { title: 'Put the account on hold', action: 'Put on hold', tone: 'danger' },
  unblock: { title: 'Lift the hold', action: 'Lift the hold', tone: 'primary' },
}

export interface ReasonResult {
  reason: string
  warn: boolean
  email: boolean
}

/** One dialog for every moderation action: a reason (required, except to lift a hold), and whether to email. */
function ReasonDialog({ mode, who, open, onOpenChange, onSubmit, pending, error }: {
  mode: ReasonMode
  who: string
  open: boolean
  onOpenChange: (o: boolean) => void
  onSubmit: (r: ReasonResult) => void
  pending: boolean
  error?: Error | null
}) {
  const [preset, setPreset] = useState('')
  const [text, setText] = useState('')
  const [warn, setWarn] = useState(true)
  const [email, setEmail] = useState(false)
  const reason = [preset, text.trim()].filter(Boolean).join('. ')
  const needsReason = mode !== 'unblock'
  const t = MODE_TEXT[mode]
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent aria-describedby="reason-desc">
        <DialogHeader>
          <DialogTitle>{t.title}</DialogTitle>
          <DialogDescription id="reason-desc">
            {mode === 'block'
              ? `${who} will still be able to sign in and download their work, but not make anything until the hold is lifted. They see your reason.`
              : mode === 'unblock'
                ? `${who} can create again straight away.`
                : `${who} sees your reason in the app.`}
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault()
            if (needsReason && reason.length < 3) return
            onSubmit({ reason, warn, email })
          }}
        >
          {needsReason && (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="reason-preset">Reason</Label>
              <select id="reason-preset" className={cn(fieldClass, 'h-9')} value={preset} onChange={(e) => setPreset(e.target.value)}>
                <option value="">Choose one, or write your own below</option>
                {PRESETS.map((p) => (
                  <option key={p}>{p}</option>
                ))}
              </select>
            </div>
          )}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="reason-text">{needsReason ? 'In your words' : 'A note for them (optional)'}</Label>
            <Textarea id="reason-text" rows={3} maxLength={900} value={text} onChange={(e) => setText(e.target.value)} />
          </div>
          {mode === 'remove' && (
            <label className="flex items-center gap-2 text-body">
              <input type="checkbox" className="size-4 accent-studio-accent" checked={warn} onChange={(e) => setWarn(e.target.checked)} />
              Count it as a warning
            </label>
          )}
          <label className="flex items-center gap-2 text-body">
            <input type="checkbox" className="size-4 accent-studio-accent" checked={email} onChange={(e) => setEmail(e.target.checked)} />
            Also email them
          </label>
          {error && <Notice>{error.message}</Notice>}
          <DialogFooter>
            <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" variant={t.tone} loading={pending} disabled={needsReason && reason.length < 3}>
              {t.action}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function useModAction<T>(fn: (arg: T) => Promise<unknown>, done: (arg: T) => string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: (_, arg) => {
      announce(done(arg))
      void qc.invalidateQueries({ queryKey: ['moderation'] })
    },
  })
}

// ---------------------------------------------------------------- review

const KINDS = [
  { value: 'image', label: 'Images' },
  { value: 'video', label: 'Videos' },
  { value: 'audio', label: 'Audio' },
] as const
const STATES = [
  { value: 'queue', label: 'To review' },
  { value: 'reviewed', label: 'Reviewed' },
  { value: 'removed', label: 'Removed' },
] as const

function Tile({ item, selected, onToggle, onOpen, onRemove, onWarn, onRestore }: {
  item: ReviewItem
  selected: boolean
  onToggle: (on: boolean) => void
  onOpen: () => void
  onRemove: () => void
  onWarn: () => void
  onRestore: () => void
}) {
  const label = item.title || item.prompt.slice(0, 60) || `${item.kind} from ${item.owner.name || item.owner.email}`
  return (
    <li className={cn('flex flex-col overflow-hidden rounded-[8px] border bg-studio-raised', selected ? 'border-studio-accent' : 'border-studio-border')}>
      <div className="relative aspect-square bg-studio-panel">
        {item.thumb_url && item.kind !== 'audio' ? (
          <img src={item.thumb_url} alt="" loading="lazy" className={cn('size-full object-cover', item.removed_at && 'opacity-40 grayscale')} />
        ) : (
          <div className="flex size-full items-center justify-center text-studio-muted">
            <AudioLines aria-hidden className="size-8" />
          </div>
        )}
        <label className="absolute left-2 top-2 flex size-7 items-center justify-center rounded-[6px] bg-studio-panel/90">
          <input type="checkbox" className="size-4 accent-studio-accent" checked={selected} onChange={(e) => onToggle(e.target.checked)} aria-label={`Select ${label}`} />
        </label>
        {item.kind === 'video' && <Badge tone="neutral" className="absolute right-2 top-2">Video</Badge>}
      </div>
      <div className="flex flex-1 flex-col gap-1 p-2">
        <p className="truncate text-body font-medium" title={label}>
          {label}
        </p>
        <p className="truncate text-small text-studio-muted">
          {item.owner.name || item.owner.email} · {when(item.created_at)}
        </p>
        {item.removed_at && <p className="text-small text-studio-danger">Removed: {item.removed_reason}</p>}
        <div className="mt-auto flex flex-wrap gap-1 pt-1">
          <Button size="sm" variant="ghost" onClick={onOpen} aria-label={`Open ${label}`}>
            <Eye aria-hidden /> Open
          </Button>
          {item.removed_at ? (
            <Button size="sm" variant="ghost" onClick={onRestore} aria-label={`Put back ${label}`}>
              <Undo2 aria-hidden /> Put back
            </Button>
          ) : (
            <Button size="sm" variant="ghost" onClick={onRemove} aria-label={`Remove ${label}`}>
              <Trash2 aria-hidden /> Remove
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={onWarn} aria-label={`Warn the owner of ${label}`}>
            <ShieldAlert aria-hidden /> Warn
          </Button>
        </div>
      </div>
    </li>
  )
}

function Viewer({ item, onClose }: { item: ReviewItem | null; onClose: () => void }) {
  return (
    <Dialog open={!!item} onOpenChange={(o) => !o && onClose()}>
      <DialogContent aria-describedby="viewer-desc" className="max-w-3xl">
        {item && (
          <>
            <DialogHeader>
              <DialogTitle>{item.title || 'Untitled'}</DialogTitle>
              <DialogDescription id="viewer-desc">
                By {item.owner.name || item.owner.email} ({item.owner.email}), {when(item.created_at)}
              </DialogDescription>
            </DialogHeader>
            {item.file_url &&
              (item.kind === 'image' ? (
                <img src={item.file_url} alt={item.prompt || item.title} className="max-h-[60vh] w-full rounded-[6px] object-contain" />
              ) : item.kind === 'video' ? (
                <video src={item.file_url} controls className="max-h-[60vh] w-full rounded-[6px]" />
              ) : (
                <audio src={item.file_url} controls className="w-full" />
              ))}
            {item.prompt && (
              <p className="text-body">
                <span className="text-studio-muted">Prompt: </span>
                {item.prompt}
              </p>
            )}
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}

/** Moderation › Review (contract v42): what people made, newest first; mark it fine, remove it, or warn. */
export function ReviewPage() {
  const [state, setState] = useState<ReviewState>('queue')
  const [kind, setKind] = useState<'image' | 'video' | 'audio' | null>(null)
  const [user, setUser] = useState('')
  const [q, setQ] = useState('')
  const [since, setSince] = useState('')
  const [until, setUntil] = useState('')
  const [selected, setSelected] = useState<string[]>([])
  const [open, setOpen] = useState<ReviewItem | null>(null)
  const [acting, setActing] = useState<{ mode: 'remove' | 'warn'; item: ReviewItem } | null>(null)
  const filters = { state, kind: kind ?? undefined, user: user || undefined, q: q.trim() || undefined, since: since || undefined, until: until || undefined }
  const people = useQuery({ queryKey: qk.modPeople('', 'all'), queryFn: () => api.moderation.people() })
  const items = useInfiniteQuery({
    queryKey: qk.review(filters),
    queryFn: ({ pageParam }) => api.moderation.items({ ...filters, before: pageParam ?? undefined, limit: 60 }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next,
  })
  const list = items.data?.pages.flatMap((p) => p.items) ?? []
  const reviewed = useModAction((ids: string[]) => api.moderation.reviewed(ids), (ids) => `${ids.length} marked as fine`)
  const remove = useModAction(
    ({ id, r }: { id: string; r: ReasonResult }) => api.moderation.remove(id, { reason: r.reason, warn: r.warn, email: r.email }),
    () => 'Item removed',
  )
  const warn = useModAction(
    ({ item, r }: { item: ReviewItem; r: ReasonResult }) => api.moderation.warn(item.owner.id, { reason: r.reason, item_id: item.id, email: r.email }),
    () => 'Warning sent',
  )
  const restore = useModAction((id: string) => api.moderation.restore(id), () => 'Item put back')
  const toggle = (id: string, on: boolean) => setSelected((s) => (on ? [...s, id] : s.filter((x) => x !== id)))

  return (
    <AdminPage id="review-title" title="Review" intro="What people have made, newest first. Mark what's fine to clear it, or remove what breaks the rules.">
      <div role="search" className="flex flex-wrap items-end gap-3">
        <ChipGroup label="Show" options={STATES} value={state} onChange={(v) => v && setState(v)} allowEmpty={false} />
        <ChipGroup label="Type" options={KINDS} value={kind} onChange={setKind} />
        <div className="flex flex-col gap-1">
          <Label htmlFor="rv-user">Person</Label>
          <select id="rv-user" className={cn(fieldClass, 'h-8 w-48')} value={user} onChange={(e) => setUser(e.target.value)}>
            <option value="">Everyone</option>
            {(people.data?.users ?? []).map((p) => (
              <option key={p.id} value={p.id}>
                {p.name || p.email}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="rv-since">From</Label>
          <Input id="rv-since" type="date" className="h-8" value={since} onChange={(e) => setSince(e.target.value)} />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="rv-until">To</Label>
          <Input id="rv-until" type="date" className="h-8" value={until} onChange={(e) => setUntil(e.target.value)} />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="rv-q">Search</Label>
          <Input id="rv-q" className="h-8 w-48" placeholder="Title, name or email" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      </div>

      {selected.length > 0 && (
        <div role="toolbar" aria-label="Selected items" className="sticky top-12 z-10 flex items-center gap-2 rounded-[8px] border border-studio-border bg-studio-panel px-3 py-2">
          <span className="text-body">{selected.length} selected</span>
          <Button size="sm" loading={reviewed.isPending} onClick={() => reviewed.mutate(selected, { onSuccess: () => setSelected([]) })}>
            <Check aria-hidden /> Looks fine
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setSelected([])}>
            Clear
          </Button>
        </div>
      )}

      {items.isError ? (
        <ErrorState error={items.error} onRetry={() => items.refetch()} />
      ) : items.isPending ? (
        <p role="status" className="text-body text-studio-muted">
          Loading…
        </p>
      ) : !list.length ? (
        <EmptyState title={state === 'queue' ? 'Nothing waiting for review' : 'Nothing here'}>Try another filter.</EmptyState>
      ) : (
        <>
          <ul aria-label="Items" className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
            {list.map((it) => (
              <Tile
                key={it.id}
                item={it}
                selected={selected.includes(it.id)}
                onToggle={(on) => toggle(it.id, on)}
                onOpen={() => setOpen(it)}
                onRemove={() => setActing({ mode: 'remove', item: it })}
                onWarn={() => setActing({ mode: 'warn', item: it })}
                onRestore={() => restore.mutate(it.id)}
              />
            ))}
          </ul>
          {items.hasNextPage && (
            <Button variant="secondary" className="self-center" loading={items.isFetchingNextPage} onClick={() => items.fetchNextPage()}>
              Show more
            </Button>
          )}
        </>
      )}
      <Viewer item={open} onClose={() => setOpen(null)} />
      {acting && (
        <ReasonDialog
          key={`${acting.mode}-${acting.item.id}`}
          mode={acting.mode}
          who={acting.item.owner.name || acting.item.owner.email}
          open
          onOpenChange={(o) => !o && setActing(null)}
          pending={remove.isPending || warn.isPending}
          error={(acting.mode === 'remove' ? remove.error : warn.error) as Error | null}
          onSubmit={(r) =>
            acting.mode === 'remove'
              ? remove.mutate({ id: acting.item.id, r }, { onSuccess: () => setActing(null) })
              : warn.mutate({ item: acting.item, r }, { onSuccess: () => setActing(null) })
          }
        />
      )}
    </AdminPage>
  )
}

// ---------------------------------------------------------------- people

/** Moderation › People: warnings in the last 90 days, holds, and the actions on an account. */
export function PeoplePage() {
  const [q, setQ] = useState('')
  const [status, setStatus] = useState<'all' | 'warned' | 'blocked'>('all')
  const [acting, setActing] = useState<{ mode: 'warn' | 'block' | 'unblock'; person: ModPerson } | null>(null)
  const people = useQuery({ queryKey: qk.modPeople(q, status), queryFn: () => api.moderation.people(q, status) })
  const act = useModAction(
    ({ mode, person, r }: { mode: 'warn' | 'block' | 'unblock'; person: ModPerson; r: ReasonResult }) =>
      mode === 'warn'
        ? api.moderation.warn(person.id, { reason: r.reason, email: r.email })
        : mode === 'block'
          ? api.moderation.block(person.id, { reason: r.reason, email: r.email })
          : api.moderation.unblock(person.id, { reason: r.reason, email: r.email }),
    ({ mode, person }) => `${person.name || person.email}: ${mode === 'warn' ? 'warning sent' : mode === 'block' ? 'on hold' : 'hold lifted'}`,
  )
  const limit = people.data?.warn_limit ?? 3

  return (
    <AdminPage id="people-title" title="People" intro={`Warnings count for 90 days. At ${limit} or more, putting the account on hold is suggested; it's always your call.`}>
      <div role="search" className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col gap-1">
          <Label htmlFor="pp-q">Search</Label>
          <Input id="pp-q" className="h-8 w-60" placeholder="Name or email" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <ChipGroup
          label="Show"
          options={[
            { value: 'all', label: 'Everyone' },
            { value: 'warned', label: 'Warned' },
            { value: 'blocked', label: 'On hold' },
          ]}
          value={status}
          onChange={(v) => v && setStatus(v)}
          allowEmpty={false}
        />
      </div>
      {people.isError ? (
        <ErrorState error={people.error} onRetry={() => people.refetch()} />
      ) : !people.data ? (
        <p role="status" className="text-body text-studio-muted">
          Loading…
        </p>
      ) : !people.data.users.length ? (
        <EmptyState title="Nobody here">Try another search.</EmptyState>
      ) : (
        <div className={tableWrap}>
          <table className="w-full text-body">
            <caption className="sr-only">People you look after</caption>
            <thead className="bg-studio-panel text-left text-small text-studio-muted">
              <tr>
                <th scope="col" className={th}>Person</th>
                <th scope="col" className={th}>Warnings</th>
                <th scope="col" className={th}>Status</th>
                <th scope="col" className={th}>
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {people.data.users.map((p) => {
                const over = p.warnings >= limit
                return (
                  <tr key={p.id} className="border-t border-studio-border">
                    <th scope="row" className={`${td} text-left font-normal`}>
                      <span className="block font-medium">{p.name || p.email}</span>
                      <span className="block text-small text-studio-muted">{p.email}</span>
                    </th>
                    <td className={td}>
                      <Badge tone={over ? 'danger' : p.warnings ? 'warning' : 'neutral'}>{p.warnings}</Badge>
                    </td>
                    <td className={td}>
                      {p.blocked_at ? (
                        <span className="text-small">
                          <Badge tone="danger">On hold</Badge> <span className="text-studio-muted">{p.blocked_reason}</span>
                        </span>
                      ) : (
                        <Badge tone="success">Active</Badge>
                      )}
                    </td>
                    <td className={`${td} whitespace-nowrap text-right`}>
                      <Button size="sm" variant="ghost" onClick={() => setActing({ mode: 'warn', person: p })}>
                        <ShieldAlert aria-hidden /> Warn
                      </Button>
                      {p.blocked_at ? (
                        <Button size="sm" variant="secondary" onClick={() => setActing({ mode: 'unblock', person: p })}>
                          <Undo2 aria-hidden /> Lift hold
                        </Button>
                      ) : (
                        <Button size="sm" variant={over ? 'danger' : 'ghost'} onClick={() => setActing({ mode: 'block', person: p })}>
                          <Ban aria-hidden /> Put on hold
                        </Button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      {acting && (
        <ReasonDialog
          key={`${acting.mode}-${acting.person.id}`}
          mode={acting.mode}
          who={acting.person.name || acting.person.email}
          open
          onOpenChange={(o) => !o && setActing(null)}
          pending={act.isPending}
          error={act.error as Error | null}
          onSubmit={(r) => act.mutate({ ...acting, r }, { onSuccess: () => setActing(null) })}
        />
      )}
    </AdminPage>
  )
}

// ---------------------------------------------------------------- log

const ACTION_LABEL: Record<string, string> = {
  moderation_remove: 'Removed an item',
  moderation_restore: 'Put an item back',
  moderation_reviewed: 'Marked items as fine',
  moderation_warning: 'Sent a warning',
  moderation_block: 'Put an account on hold',
  moderation_unblock: 'Lifted a hold',
}

/** Moderation › Log: every moderation action, newest first; a manager sees the ones about their people. */
export function ModerationLogPage() {
  const log = useQuery({ queryKey: qk.modLog, queryFn: api.moderation.log })
  return (
    <AdminPage id="modlog-title" title="Moderation log" intro="Every removal, warning and hold, with who did it and why.">
      {log.isError ? (
        <ErrorState error={log.error} onRetry={() => log.refetch()} />
      ) : !log.data ? (
        <p role="status" className="text-body text-studio-muted">
          Loading…
        </p>
      ) : !log.data.length ? (
        <EmptyState title="Nothing yet">Actions show up here as they happen.</EmptyState>
      ) : (
        <div className={tableWrap}>
          <table className="w-full text-body">
            <caption className="sr-only">Moderation actions</caption>
            <thead className="bg-studio-panel text-left text-small text-studio-muted">
              <tr>
                <th scope="col" className={th}>When</th>
                <th scope="col" className={th}>Who</th>
                <th scope="col" className={th}>What</th>
                <th scope="col" className={th}>Reason</th>
              </tr>
            </thead>
            <tbody>
              {log.data.map((row) => (
                <tr key={row.id} className="border-t border-studio-border align-top">
                  <td className={`${td} whitespace-nowrap`}>{when(row.at)}</td>
                  <td className={td}>{row.actor}</td>
                  <td className={td}>{ACTION_LABEL[row.action] ?? row.action}</td>
                  <td className={`${td} text-studio-muted`}>{String(row.detail.reason ?? (row.detail.count ? `${row.detail.count} items` : ''))}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-small text-studio-muted">
        Admins see the whole <Link className={linkButton} to="/admin/audit">audit log</Link>.
      </p>
    </AdminPage>
  )
}
