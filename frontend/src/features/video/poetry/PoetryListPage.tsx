import { Link } from 'react-router'
import { Feather } from 'lucide-react'
import { EmptyState, ErrorState } from '@/components/studio/states'
import { Skeleton } from '@/components/ui/skeleton'
import { usePoetryList } from '@/hooks/usePoetry'
import { clock } from '@/lib/poetry'
import { NewShortForm } from './NewShortForm'

const STATUS: Record<string, string> = { running: 'Working', review: 'Needs your OK', failed: 'Stopped', done: 'Finished', idle: 'Paused' }

export function PoetryListPage() {
  const list = usePoetryList()
  return (
    <main data-f6-region tabIndex={-1} aria-labelledby="poetry-h" className="relative h-full overflow-y-auto focus-visible:outline-none">
      <div className="mx-auto grid w-full max-w-6xl gap-6 px-4 py-6 md:px-8 grid-cols-[minmax(0,1fr)] lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="flex flex-col gap-2">
          <h1 id="poetry-h" className="font-display text-[22px] font-semibold">
            Poetry Short
          </h1>
          <p className="text-body text-studio-muted">A poem, sung, with each verse appearing on screen as it is sung over slow cinematic scenes.</p>
          <NewShortForm />
        </div>
        <section aria-labelledby="poetry-mine" className="flex flex-col gap-2">
          <h2 id="poetry-mine" className="font-display text-title font-semibold">
            Your Shorts
          </h2>
          {list.isPending && <Skeleton className="h-24" />}
          {list.isError && <ErrorState error={list.error} onRetry={() => list.refetch()} />}
          {list.data?.length === 0 && (
            <EmptyState icon={<Feather />} title="No Shorts yet">
              Your first one appears here as soon as it starts.
            </EmptyState>
          )}
          <ul className="flex flex-col gap-2">
            {list.data?.map((s) => (
              <li key={s.id}>
                <Link
                  to={`/video/poetry/${s.id}`}
                  className="flex items-center gap-3 rounded-[6px] border border-studio-border bg-studio-raised p-2 hover:border-studio-border-hover focus-visible:outline-2 focus-visible:outline-studio-accent"
                >
                  <span className="flex aspect-[9/16] w-10 shrink-0 overflow-hidden rounded-[3px] bg-studio-panel">
                    {s.thumb_url && <img src={s.thumb_url} alt="" className="size-full object-cover" />}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span dir="auto" lang={s.language} className="block truncate text-body font-medium">
                      {s.title || 'Untitled'}
                    </span>
                    <span className="block text-small text-studio-muted">
                      {STATUS[s.status] ?? s.status} · {s.lines} lines{s.duration_s ? ` · ${clock(s.duration_s)}` : ''} · {s.aspect}
                    </span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </main>
  )
}
