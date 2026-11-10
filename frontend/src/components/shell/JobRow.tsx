import { RotateCcw, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { StatusPill } from '@/components/studio/status-pill'
import { useJobAction } from '@/hooks/useJobs'
import { isActiveJob, jobLabel, jobStatus } from '@/lib/status'
import type { Job } from '@/lib/types'
import { timeAgo } from '@/lib/utils'

export function JobRow({ job }: { job: Job }) {
  const cancel = useJobAction('cancel')
  const retry = useJobAction('retry')
  const active = isActiveJob(job.status)
  const status = jobStatus(job.status, job.status === 'running' ? job.progress : undefined)

  return (
    <li className="flex flex-col gap-2 rounded-[6px] border border-studio-border bg-studio-raised p-2.5 shadow-card">
      <div className="flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-body font-medium">{jobLabel(job.type)}</span>
        <StatusPill status={status} />
      </div>
      {job.status === 'running' && <Progress value={job.progress} label={`${jobLabel(job.type)} progress`} />}
      {job.status === 'queued' && <Progress value={null} label={`${jobLabel(job.type)} waiting`} />}
      <div className="flex items-center gap-2 text-small text-studio-muted">
        <span className="min-w-0 flex-1 truncate" title={job.error || job.message}>
          {job.status === 'failed' && job.error
            ? job.error
            : job.message && job.message.toLowerCase() !== status.label.toLowerCase()
              ? job.message
              : timeAgo(job.finished_at ?? job.created_at)}
        </span>
        {/* TODO: real ETA once the worker reports throughput; placeholder keeps the layout stable */}
        {active && <span className="font-mono">ETA —</span>}
        {job.gpu_seconds != null && !active && <span className="font-mono">{Math.round(job.gpu_seconds)}s GPU</span>}
      </div>
      {(active || job.status === 'failed' || job.status === 'cancelled') && (
        <div className="flex justify-end gap-1">
          {active ? (
            <Button size="sm" variant="ghost" loading={cancel.isPending} onClick={() => cancel.mutate(job.id)}>
              <X aria-hidden />
              Cancel
            </Button>
          ) : (
            <Button size="sm" variant="secondary" loading={retry.isPending} onClick={() => retry.mutate(job.id)}>
              <RotateCcw aria-hidden />
              Retry
            </Button>
          )}
        </div>
      )}
      {(cancel.isError || retry.isError) && (
        <p className="text-small text-studio-danger" role="alert">
          {(cancel.error ?? retry.error)?.message}
        </p>
      )}
    </li>
  )
}
