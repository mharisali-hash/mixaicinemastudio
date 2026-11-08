import { useDeferredValue, useState } from 'react'
import { Search } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { EmptyState, ErrorState } from '@/components/studio/states'
import { flatItems, useMediaList } from '@/hooks/useMedia'
import type { MediaItem, MediaKind } from '@/lib/types'
import { MediaTile } from './MediaTile'
import { UploadZone } from './UploadZone'

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  kind: MediaKind
  // more than one kind: a switch above the search flips between them (the list asks for one kind at a time)
  kinds?: MediaKind[]
  max: number
  title: string
  description?: string
  // already chosen elsewhere; they count against `max`
  taken?: string[]
  confirmLabel?: string
  // studio parts (contract v37) to offer besides the Library; pictures default to a character's sheets
  includeHome?: string
  onConfirm: (items: MediaItem[]) => void
}

export function MediaPicker(props: Props) {
  return (
    <Dialog open={props.open} onOpenChange={props.onOpenChange}>
      <DialogContent className="max-w-4xl">{props.open && <PickerBody {...props} />}</DialogContent>
    </Dialog>
  )
}

const KIND_LABEL: Partial<Record<MediaKind, string>> = { image: 'Pictures', video: 'Clips', audio: 'Audio' }

function PickerBody({ kind: first, kinds, max, title, description, taken = [], confirmLabel = 'Use selected', includeHome, onConfirm, onOpenChange }: Props) {
  const [kind, setKind] = useState<MediaKind>(first)
  const [q, setQ] = useState('')
  const query = useDeferredValue(q.trim())
  const [picked, setPicked] = useState<MediaItem[]>([])
  // pictures are picked as references or start frames, so a character's sheet is offered too
  const home = includeHome ?? (kind === 'image' ? 'character' : '')
  const list = useMediaList({ kind, include: 'project', ...(home ? { include_home: home } : {}), ...(query ? { q: query } : {}) })
  const items = flatItems(list.data).filter((m) => m.media_url || m.thumb_url)
  const room = Math.max(0, max - taken.length)
  const full = picked.length >= room

  const toggle = (m: MediaItem) =>
    setPicked((p) => (p.some((x) => x.id === m.id) ? p.filter((x) => x.id !== m.id) : p.length < room ? [...p, m] : p))

  return (
    <div className="flex min-h-0 flex-col gap-3">
      <DialogHeader className="mb-0">
        <DialogTitle>{title}</DialogTitle>
        <DialogDescription>
          {description ?? `Pick up to ${room} from your library, or upload new ones.`} {picked.length}/{room} selected.
        </DialogDescription>
      </DialogHeader>
      {kinds && kinds.length > 1 && (
        <div role="group" aria-label="Show" className="flex gap-1">
          {kinds.map((k) => (
            <Button key={k} type="button" size="sm" variant={k === kind ? 'primary' : 'ghost'} aria-pressed={k === kind} onClick={() => setKind(k)}>
              {KIND_LABEL[k] ?? `${k}s`}
            </Button>
          ))}
        </div>
      )}
      <div className="relative">
        <Search aria-hidden className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-studio-muted" />
        <Input type="search" placeholder={`Search ${kind}s`} aria-label={`Search ${kind}s`} value={q} onChange={(e) => setQ(e.target.value)} className="pl-8" />
      </div>
      {kind !== 'model' && <UploadZone compact kinds={[kind]} onUploaded={(m) => !full && setPicked((p) => (p.length < room ? [...p, m] : p))} />}
      <div className="max-h-[48vh] min-h-40 overflow-y-auto pr-1">
        {list.isPending ? (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(140px,1fr))] gap-3">
            {Array.from({ length: 8 }, (_, i) => (
              <Skeleton key={i} className="aspect-square" />
            ))}
          </div>
        ) : list.isError ? (
          <ErrorState error={list.error} onRetry={() => list.refetch()} />
        ) : items.length === 0 ? (
          <EmptyState title={query ? `Nothing matches “${query}”` : `No ${kind}s yet`}>Upload one above to get started.</EmptyState>
        ) : (
          <ul className="grid grid-cols-[repeat(auto-fill,minmax(140px,1fr))] gap-3" aria-label="Library">
            {items.map((m) => {
              const on = picked.some((x) => x.id === m.id) || taken.includes(m.id)
              return (
                <li key={m.id}>
                  <MediaTile item={m} selectable selected={on} disabled={full || taken.includes(m.id)} onToggle={() => !taken.includes(m.id) && toggle(m)} />
                </li>
              )
            })}
          </ul>
        )}
        {list.hasNextPage && (
          <div className="flex justify-center pt-3">
            <Button variant="secondary" size="sm" loading={list.isFetchingNextPage} onClick={() => list.fetchNextPage()}>
              Load more
            </Button>
          </div>
        )}
      </div>
      {full && room > 0 && (
        <p className="text-small text-studio-muted" role="status">
          That's the most you can use at once ({max}). Unselect one to swap it.
        </p>
      )}
      <DialogFooter className="mt-0">
        <Button variant="ghost" onClick={() => onOpenChange(false)}>
          Cancel
        </Button>
        <Button
          variant="primary"
          disabled={!picked.length}
          onClick={() => {
            onConfirm(picked)
            onOpenChange(false)
          }}
        >
          {confirmLabel}
          {picked.length > 0 && ` (${picked.length})`}
        </Button>
      </DialogFooter>
    </div>
  )
}
