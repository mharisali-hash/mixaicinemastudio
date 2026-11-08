import { useState } from 'react'
import { ChevronLeft, ChevronRight, Columns2 } from 'lucide-react'
import { BeforeAfter } from '@/components/media/BeforeAfter'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { cn } from '@/lib/utils'
import type { BulkBatch, BulkItem } from './api'

interface Props {
  items: BulkItem[]
  index: number | null
  onIndex: (i: number | null) => void
  action: BulkBatch['action']
}

/** Full-size before and after for one bulk picture. ←/→ browse what the grid has loaded, Esc closes. */
export function BulkViewer({ items, index, onIndex, action }: Props) {
  const item = index != null ? items[index] : undefined
  const [compare, setCompare] = useState(false)
  const go = (d: number) => index != null && items.length > 1 && onIndex((index + d + items.length) % items.length)
  // w=0 is the original file; the grid only ever loads thumbs
  const before = item?.source_url ? `${item.source_url}?w=0` : null
  const after = item?.result_url ? `${item.result_url}?w=0` : null
  const backdrop = action === 'cutout' ? 'checker' : 'bg-studio-darkroom'

  return (
    <Dialog open={!!item} onOpenChange={(o) => !o && onIndex(null)}>
      <DialogContent
        className="max-h-[95vh] max-w-6xl gap-3"
        aria-describedby={undefined}
        onKeyDown={(e) => {
          // the compare slider uses the arrows itself while it has focus
          if (e.target instanceof HTMLElement && e.target.getAttribute('role') === 'slider') return
          if (e.key === 'ArrowLeft') go(-1)
          else if (e.key === 'ArrowRight') go(1)
        }}
      >
        {item && (
          <>
            <div className="flex flex-wrap items-center gap-2 pr-8">
              <DialogTitle className="min-w-0 flex-1 truncate text-panel" title={item.filename}>
                {item.filename}
              </DialogTitle>
              <span className="text-small tabular-nums text-studio-muted">
                {index! + 1} of {items.length}
              </span>
            </div>
            {compare && before && after ? (
              <BeforeAfter before={before} after={after} alt={item.filename} aspectClass="h-[70vh]" className={backdrop} globalLoupeKey />
            ) : (
              <div className={cn('grid gap-2', after || !before ? 'sm:grid-cols-2' : '')}>
                {before && (
                  <figure className="flex flex-col gap-1">
                    <img src={before} alt={`${item.filename}, before`} className="max-h-[70vh] w-full rounded-[6px] bg-studio-darkroom object-contain" />
                    <figcaption className="text-small text-studio-muted">Before</figcaption>
                  </figure>
                )}
                <figure className="flex flex-col gap-1">
                  {after ? (
                    <img src={after} alt={`${item.filename}, after`} className={cn('max-h-[70vh] w-full rounded-[6px] object-contain', backdrop)} />
                  ) : (
                    <div className="grid min-h-48 place-items-center rounded-[6px] bg-studio-raised text-small text-studio-muted">
                      {item.status === 'failed' ? 'Failed' : item.status === 'queued' || item.status === 'running' ? 'Working…' : 'Not run yet'}
                    </div>
                  )}
                  <figcaption className="text-small text-studio-muted">After</figcaption>
                </figure>
              </div>
            )}
            {(item.prompt_used || item.instruction || item.error) && (
              <DialogDescription className={cn('text-small', item.error && 'text-studio-danger')}>
                {item.error ?? item.prompt_used ?? item.instruction}
              </DialogDescription>
            )}
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" variant="ghost" onClick={() => go(-1)} disabled={items.length < 2} aria-label="Previous picture">
                <ChevronLeft className="size-4" />
              </Button>
              <Button size="sm" variant="ghost" onClick={() => go(1)} disabled={items.length < 2} aria-label="Next picture">
                <ChevronRight className="size-4" />
              </Button>
              {before && after && (
                <Button size="sm" onClick={() => setCompare((c) => !c)} aria-pressed={compare}>
                  <Columns2 className="size-4" />
                  {compare ? 'Side by side' : 'Compare'}
                </Button>
              )}
              {after && (
                <a href={after} target="_blank" rel="noreferrer" className="ml-auto text-small text-studio-accent-hover underline-offset-2 hover:underline">
                  Open full size
                </a>
              )}
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
