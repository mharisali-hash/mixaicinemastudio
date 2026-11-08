import { useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input, fieldClass } from '@/components/ui/input'
import { ErrorState } from '@/components/studio/states'
import { useMe } from '@/hooks/useAuth'
import { cn } from '@/lib/utils'
import { AdminPage, linkButton, tableWrap, td, th, when } from '../shared'
import { ACTION_LABEL, STATUS, bulkApi, bulkKey, type BulkAction } from './api'

/** Admin: bulk runs for training datasets. The list, and a new batch. */
export function BulkPage() {
  const { data: me } = useMe()
  const nav = useNavigate()
  const list = useQuery({ queryKey: bulkKey, queryFn: bulkApi.list, enabled: !!me?.admin, refetchInterval: 10_000 })
  const [name, setName] = useState('')
  const [action, setAction] = useState<BulkAction>('cutout')
  const create = useMutation({
    mutationFn: () => bulkApi.create({ name, action, options: {} }),
    onSuccess: (b) => nav(`/admin/bulk/${b.id}`),
  })

  return (
    <AdminPage
      id="bulk-title"
      title="Bulk"
      intro="One action over thousands of pictures, for training datasets. Results stay out of every Library and download as ZIPs with their prompts."
    >
      <form
        className="flex flex-wrap items-end gap-3 rounded-[8px] border border-studio-border bg-studio-raised p-3"
        onSubmit={(e) => {
          e.preventDefault()
          create.mutate()
        }}
      >
        <div className="flex min-w-56 flex-1 flex-col gap-1.5">
          <label htmlFor="bulk-name" className="section-label">
            New batch
          </label>
          <Input id="bulk-name" placeholder="e.g. Shoes, white background" value={name} maxLength={200} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="bulk-action" className="section-label">
            Action
          </label>
          <select id="bulk-action" className={cn(fieldClass, 'h-8 pr-7')} value={action} onChange={(e) => setAction(e.target.value as BulkAction)}>
            <option value="cutout">{ACTION_LABEL.cutout}</option>
            <option value="edit">{ACTION_LABEL.edit}</option>
          </select>
        </div>
        <Button type="submit" variant="primary" loading={create.isPending}>
          Create batch
        </Button>
        {create.error && <p className="w-full text-small text-studio-danger" role="alert">{create.error.message}</p>}
      </form>

      {list.error ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : (
        <div className={tableWrap}>
          <table className="w-full text-left text-body" aria-label={`Batches, ${list.data?.length ?? 0} shown`}>
            <thead className="text-small text-studio-muted">
              <tr>
                <th className={th}>Batch</th>
                <th className={th}>Action</th>
                <th className={th}>Pictures</th>
                <th className={th}>Done</th>
                <th className={th}>Failed</th>
                <th className={th}>Status</th>
                <th className={th}>Created</th>
              </tr>
            </thead>
            <tbody>
              {list.data?.length === 0 && (
                <tr>
                  <td className={cn(td, 'py-6 text-center text-studio-muted')} colSpan={7}>
                    No batches yet. Name one above to start.
                  </td>
                </tr>
              )}
              {list.data?.map((b) => (
                <tr key={b.id} className="border-t border-studio-border">
                  <td className={td}>
                    <Link to={`/admin/bulk/${b.id}`} className={linkButton}>
                      {b.name}
                    </Link>
                  </td>
                  <td className={td}>{ACTION_LABEL[b.action]}</td>
                  <td className={cn(td, 'tabular-nums')}>{b.counts.total.toLocaleString('en-GB')}</td>
                  <td className={cn(td, 'tabular-nums')}>{b.counts.done.toLocaleString('en-GB')}</td>
                  <td className={cn(td, 'tabular-nums')}>{b.counts.failed.toLocaleString('en-GB')}</td>
                  <td className={td}>
                    <Badge tone={STATUS[b.status].tone}>{STATUS[b.status].label}</Badge>
                  </td>
                  <td className={cn(td, 'text-studio-muted')}>{when(b.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </AdminPage>
  )
}
