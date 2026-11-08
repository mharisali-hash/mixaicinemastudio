import { useDeferredValue, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input, fieldClass } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { api } from '@/lib/api'
import type { AdminUser } from '@/lib/types'
import { cn } from '@/lib/utils'
import { Notice } from '@/features/auth/AuthLayout'
import { announce } from '@/stores/ui'
import { endOfDay, pkr, startOfDay, usePackages } from './shared'

const nameOf = (u: AdminUser) => u.display_name || u.email

/** Finds the person to give a package to, when the dialog is opened from the Packages page. */
function PersonPicker({ value, onChange }: { value: AdminUser | null; onChange: (u: AdminUser | null) => void }) {
  const [q, setQ] = useState('')
  const query = useDeferredValue(q.trim())
  const found = useQuery({
    queryKey: ['admin-users', 'pick', query],
    queryFn: () => api.admin.users(query, 'active', ''),
    enabled: query.length >= 2,
  })
  if (value) {
    return (
      <div className="flex flex-col gap-1.5">
        <Label>Person</Label>
        <p className="flex items-center gap-2 text-body">
          <span className="font-medium">{nameOf(value)}</span>
          {value.display_name && <span className="text-studio-muted">{value.email}</span>}
          <Button type="button" size="sm" variant="ghost" className="ml-auto" onClick={() => onChange(null)}>
            Change person
          </Button>
        </p>
      </div>
    )
  }
  // admins have no limits, so a package means nothing to them
  const people = (found.data ?? []).filter((u) => !u.is_admin).slice(0, 8)
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor="assign-person">Person</Label>
      <Input id="assign-person" type="search" autoFocus placeholder="Name or email" value={q} onChange={(e) => setQ(e.target.value)} />
      {query.length >= 2 && (
        <ul className="flex flex-col rounded-[6px] border border-studio-border" aria-label="Matching people">
          {people.length === 0 ? (
            <li className="px-3 py-2 text-small text-studio-muted">{found.isFetching ? 'Searching…' : 'Nobody matches.'}</li>
          ) : (
            people.map((u) => (
              <li key={u.id}>
                <button
                  type="button"
                  className="flex w-full flex-wrap gap-x-2 px-3 py-2 text-left text-body hover:bg-studio-panel focus-visible:bg-studio-panel focus-visible:outline-none"
                  onClick={() => onChange(u)}
                >
                  <span className="font-medium">{nameOf(u)}</span>
                  <span className="text-studio-muted">{u.email}</span>
                  <span className="ml-auto text-small text-studio-muted">now: {u.package_name || 'Free'}</span>
                </button>
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  )
}

/** Puts one person on a package. Opened for a known person (Users) or a known package (Packages). */
export function AssignPackageDialog({ user, packageId, open, onOpenChange }: {
  user?: AdminUser | null
  packageId?: string
  open: boolean
  onOpenChange: (o: boolean) => void
}) {
  const qc = useQueryClient()
  const packages = usePackages()
  const [picked, setPicked] = useState<AdminUser | null>(null)
  const [pkgId, setPkgId] = useState('')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [notes, setNotes] = useState('')
  const person = user ?? picked
  const choices = (packages.data ?? []).filter((p) => !p.custom_for_user_id || p.custom_for_user_id === person?.id)
  const chosen = pkgId || packageId || choices[0]?.id || ''
  const assign = useMutation({
    mutationFn: (who: AdminUser) =>
      api.admin.assignPackage(who.id, { package_id: chosen, starts_at: startOfDay(start), ends_at: endOfDay(end), notes: notes.trim() }),
    onSuccess: (_, who) => {
      announce(`Package changed for ${nameOf(who)}`)
      for (const key of ['admin-users', 'admin-subscriptions', 'admin-packages']) void qc.invalidateQueries({ queryKey: [key] })
      setPicked(null)
      onOpenChange(false)
    },
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{user ? `Change package for ${nameOf(user)}` : 'Give this package to someone'}</DialogTitle>
          <DialogDescription>Starts straight away unless you pick a date. Whatever they have now ends.</DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-3"
          noValidate
          onSubmit={(e) => {
            e.preventDefault()
            if (person) assign.mutate(person)
          }}
        >
          {assign.isError && <Notice>{assign.error.message}</Notice>}
          {!user && <PersonPicker value={picked} onChange={setPicked} />}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="assign-package">Package</Label>
            <select id="assign-package" className={cn(fieldClass, 'h-8')} value={chosen} onChange={(e) => setPkgId(e.target.value)}>
              {choices.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} · {pkr(p.price_pkr)}
                </option>
              ))}
            </select>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="assign-start">Starts</Label>
              <Input id="assign-start" type="date" value={start} onChange={(e) => setStart(e.target.value)} />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="assign-end">Ends</Label>
              <Input id="assign-end" type="date" value={end} onChange={(e) => setEnd(e.target.value)} aria-describedby="assign-end-hint" />
              <p id="assign-end-hint" className="text-small text-studio-muted">Empty: the package's own length.</p>
            </div>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="assign-notes">Note (only admins see it)</Label>
            <Input id="assign-notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
          </div>
          <DialogFooter>
            <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" loading={assign.isPending} disabled={!chosen || !person}>
              {user ? 'Change package' : 'Give package'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
