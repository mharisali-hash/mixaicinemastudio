import { User } from 'lucide-react'
import { EmptyState, ErrorState } from '@/components/studio/states'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Skeleton } from '@/components/ui/skeleton'
import { useAiVideoCharacters } from '@/hooks/useVideoPodcast'

/** Characters from the workspace's other projects, as cards like the Cast page's. */
export function CharacterPicker({
  vpId,
  hostName,
  open,
  onOpenChange,
  onPick,
}: {
  vpId: string
  hostName: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onPick: (characterId: string) => void
}) {
  const q = useAiVideoCharacters(vpId, open)
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="font-display text-title font-semibold">Use a character for {hostName}</DialogTitle>
          <DialogDescription className="text-small text-studio-muted">The character is copied here with its approved portrait. The original stays as it is.</DialogDescription>
        </DialogHeader>
        {q.isPending ? (
          <div className="grid grid-cols-3 gap-2">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="aspect-[3/4]" />
            ))}
          </div>
        ) : q.isError ? (
          <ErrorState error={q.error} onRetry={() => q.refetch()} />
        ) : !q.data.length ? (
          <EmptyState icon={<User />} title="No characters yet">
            Characters from your film projects show up here.
          </EmptyState>
        ) : (
          <ul aria-label="Characters" className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {q.data.map((c) => (
              <li key={c.id}>
                <button
                  type="button"
                  onClick={() => onPick(c.id)}
                  className="flex w-full flex-col overflow-hidden rounded-[6px] border border-studio-border-strong bg-studio-panel text-left shadow-card transition-colors duration-150 hover:bg-studio-panel-hover"
                >
                  <span className="darkroom block aspect-square border-x-0 border-t-0">
                    {c.portrait_url ? (
                      <img src={c.portrait_url} alt="" className="size-full object-cover" loading="lazy" />
                    ) : (
                      <span className="flex size-full items-center justify-center text-studio-on-dark-muted">
                        <User aria-hidden className="size-8" />
                      </span>
                    )}
                  </span>
                  <span className="flex flex-col gap-0.5 p-2">
                    <span className="truncate font-display text-panel font-semibold">{c.name}</span>
                    <span className="truncate text-[11px] text-studio-muted">{c.project_title}</span>
                    {c.description && <span className="line-clamp-2 text-small text-studio-muted">{c.description}</span>}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </DialogContent>
    </Dialog>
  )
}
