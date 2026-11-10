import { useMemo, useState } from 'react'
import { Inbox, RotateCcw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { ApiMadeChip } from '@/components/studio/api-made-chip'
import { Chip } from '@/components/studio/chip'
import { EmptyState, ErrorState } from '@/components/studio/states'
import { JobRow } from '@/components/shell/JobRow'
import { TopBarActions } from '@/components/shell/TopBarActions'
import { useApiJobs, useJobs } from '@/hooks/useJobs'
import { isActiveJob } from '@/lib/status'
import type { Job } from '@/lib/types'

type Filter = 'active' | 'failed' | 'done' | 'all'

const FILTERS: { value: Filter; label: string; test: (j: Job) => boolean }[] = [
  { value: 'active', label: 'Running & waiting', test: (j) => isActiveJob(j.status) },
  { value: 'failed', label: 'Failed', test: (j) => j.status === 'failed' },
  { value: 'done', label: 'Finished', test: (j) => j.status === 'done' || j.status === 'cancelled' },
  { value: 'all', label: 'Everything', test: () => true },
]

// Pictures and video run on separate GPUs at the same time, each with its own line; sound fits on either.
// Grouping by job.gpu put everything in one list: every worker stamps the same driver name there.
const LANES: { key: NonNullable<Job['lane']>; label: string }[] = [
  { key: 'image', label: 'Pictures' },
  { key: 'video', label: 'Video' },
  { key: 'audio', label: 'Sound' },
  { key: 'general', label: 'Writing and finishing' },
]

function lanes(list: Job[]) {
  return LANES.map(({ key, label }) => {
    const items = list.filter((j) => (j.lane ?? 'general') === key)
    const running = items.filter((j) => j.status === 'running').length
    return { key, label, items, running, waiting: items.length - running }
  }).filter((l) => l.items.length > 0)
}

export function QueuePage() {
  const [apiOnly, setApiOnly] = useState(false)
  const studioJobs = useJobs()
  const apiJobs = useApiJobs(apiOnly)
  const jobs = apiOnly ? apiJobs : studioJobs
  const [filter, setFilter] = useState<Filter>('active')
  const list = useMemo(() => jobs.data ?? [], [jobs.data])
  const counts = useMemo(() => Object.fromEntries(FILTERS.map((f) => [f.value, list.filter(f.test).length])) as Record<Filter, number>, [list])
  const visible = list.filter(FILTERS.find((f) => f.value === filter)!.test)
  const running = list.filter((j) => j.status === 'running').length

  return (
    <main data-f6-region tabIndex={-1} className="relative h-full overflow-y-auto focus-visible:outline-none" aria-labelledby="queue-title">
      <TopBarActions>
        <Button size="sm" variant="secondary" onClick={() => jobs.refetch()} loading={jobs.isFetching}>
          <RotateCcw aria-hidden />
          Refresh
        </Button>
      </TopBarActions>
      <div className="mx-auto flex max-w-5xl flex-col gap-4 px-4 py-6 md:px-8">
        <div>
          <h1 id="queue-title" className="font-display text-title font-semibold">
            Queue
          </h1>
          <p className="text-body text-studio-muted" aria-live="polite">
            {running} running · {counts.active - running} waiting · {apiOnly ? 'made through the API, refreshes every 10 s' : 'updates live'}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <div role="group" aria-label="Show" className="flex flex-wrap gap-1">
            {FILTERS.map((f) => (
              <Chip key={f.value} selected={filter === f.value} onClick={() => setFilter(f.value)}>
                {f.label} ({counts[f.value]})
              </Chip>
            ))}
          </div>
          <ApiMadeChip on={apiOnly} onChange={setApiOnly} />
        </div>

        {jobs.isPending && [0, 1, 2].map((i) => <Skeleton key={i} className="h-20" />)}
        {jobs.isError && <ErrorState error={jobs.error} onRetry={() => jobs.refetch()} />}
        {jobs.isSuccess && visible.length === 0 && (
          <EmptyState icon={<Inbox />} title={filter === 'active' ? 'Nothing running' : 'Nothing here'}>
            {filter === 'active' ? 'Generations, renders and upscales show up here while they run.' : 'Try another filter.'}
          </EmptyState>
        )}
        {visible.length > 0 &&
          (filter === 'active' ? (
            lanes(visible).map(({ key, label, items, running: on, waiting }) => (
              <section key={key} aria-label={label}>
                <h2 className="section-label mb-2">
                  {label} · {on} running · {waiting} waiting
                </h2>
                <ul className="flex flex-col gap-2">
                  {items.map((j) => (
                    <JobRow key={j.id} job={j} />
                  ))}
                </ul>
              </section>
            ))
          ) : (
            <ul className="flex flex-col gap-2" aria-label="Jobs">
              {visible.map((j) => (
                <JobRow key={j.id} job={j} />
              ))}
            </ul>
          ))}
      </div>
    </main>
  )
}
