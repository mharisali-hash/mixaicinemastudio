import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input, fieldClass } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { qk } from '@/hooks/keys'
import { api } from '@/lib/api'
import type { AdminUser, FeatureKey, UserRights } from '@/lib/types'
import { cn } from '@/lib/utils'
import { Notice } from '@/features/auth/AuthLayout'
import { announce } from '@/stores/ui'

type Choice = 'plan' | 'allow' | 'deny'
const CHOICES: { value: Choice; label: string }[] = [
  { value: 'plan', label: 'Plan' },
  { value: 'allow', label: 'Allow' },
  { value: 'deny', label: 'Deny' },
]
const asChoice = (v: boolean | undefined): Choice => (v === undefined ? 'plan' : v ? 'allow' : 'deny')
const asValue = (c: Choice) => (c === 'plan' ? null : c === 'allow')

function ThreeWay({ name, label, value, onChange }: { name: string; label: string; value: Choice; onChange: (c: Choice) => void }) {
  // a real radio group: arrow keys move between Plan, Allow and Deny
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex overflow-hidden rounded-[6px] border border-studio-border">
      {CHOICES.map((c) => (
        <label
          key={c.value}
          className={cn(
            'cursor-pointer px-2 py-0.5 text-small has-[:focus-visible]:ring-[3px] has-[:focus-visible]:ring-studio-gold/50',
            value === c.value ? 'bg-studio-accent-soft font-medium text-studio-accent-hover' : 'text-studio-muted hover:bg-studio-panel-hover',
          )}
        >
          <input type="radio" className="sr-only" name={name} value={c.value} checked={value === c.value} onChange={() => onChange(c.value)} />
          {c.label}
        </label>
      ))}
    </div>
  )
}

function StaffEditor({ user, rights }: { user: AdminUser; rights: UserRights }) {
  const qc = useQueryClient()
  const [role, setRole] = useState<'none' | 'manager'>(rights.staff_role ?? 'none')
  const [scope, setScope] = useState(rights.manager_scope)
  const [team, setTeam] = useState<string[]>(rights.team)
  const [find, setFind] = useState('')
  const people = useQuery({
    queryKey: qk.adminUsers(find, 'all'),
    queryFn: () => api.admin.users(find, 'all'),
    enabled: role === 'manager' && scope === 'team',
  })
  const save = useMutation({
    mutationFn: () => api.admin.setStaff(user.id, { staff_role: role === 'manager' ? 'manager' : null, manager_scope: scope, team }),
    onSuccess: (r) => {
      qc.setQueryData(qk.userRights(user.id), r)
      void qc.invalidateQueries({ queryKey: ['admin-users'] })
      announce('Staff role saved')
    },
  })
  if (user.is_admin) return <p className="text-small text-studio-muted">Admins can already do everything a manager can.</p>
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-3">
        <Label htmlFor="staff-role">Staff role</Label>
        <select id="staff-role" className={cn(fieldClass, 'h-8 w-40')} value={role} onChange={(e) => setRole(e.target.value as 'none' | 'manager')}>
          <option value="none">None</option>
          <option value="manager">Manager</option>
        </select>
      </div>
      {role === 'manager' && (
        <fieldset className="flex flex-col gap-2">
          <legend className="text-small text-studio-muted">Who they look after</legend>
          <div className="flex gap-4 text-body">
            <label className="flex items-center gap-2">
              <input type="radio" name="mgr-scope" className="accent-studio-accent" checked={scope === 'site'} onChange={() => setScope('site')} />
              Everyone on the site
            </label>
            <label className="flex items-center gap-2">
              <input type="radio" name="mgr-scope" className="accent-studio-accent" checked={scope === 'team'} onChange={() => setScope('team')} />
              A team
            </label>
          </div>
          {scope === 'team' && (
            <div className="flex flex-col gap-1.5">
              <Input aria-label="Find people for the team" placeholder="Find people by name or email" value={find} onChange={(e) => setFind(e.target.value)} />
              <ul className="max-h-40 overflow-y-auto rounded-[6px] border border-studio-border p-1">
                {(people.data ?? [])
                  .filter((p) => p.id !== user.id && !p.is_admin)
                  .map((p) => (
                    <li key={p.id}>
                      <label className="flex items-center gap-2 px-1 py-0.5 text-body">
                        <input
                          type="checkbox"
                          className="size-4 accent-studio-accent"
                          checked={team.includes(p.id)}
                          onChange={(e) => setTeam((t) => (e.target.checked ? [...t, p.id] : t.filter((x) => x !== p.id)))}
                        />
                        {p.display_name || p.email}
                      </label>
                    </li>
                  ))}
              </ul>
              <p className="text-small text-studio-muted">{team.length} in the team</p>
            </div>
          )}
        </fieldset>
      )}
      {save.isError && <Notice>{save.error.message}</Notice>}
      <Button size="sm" variant="secondary" className="self-start" loading={save.isPending} onClick={() => save.mutate()}>
        Save staff role
      </Button>
    </div>
  )
}

/** UserDrawer › Rights (contract v42): one person's exceptions to their plan, and whether they're a manager. */
export function UserRightsSection({ user }: { user: AdminUser }) {
  const qc = useQueryClient()
  // an older server (or a test's catch-all mock) answers something else; then there's simply no section
  const rights = useQuery({
    queryKey: qk.userRights(user.id),
    queryFn: async () => {
      const r = await api.admin.userRights(user.id)
      return r && typeof r === 'object' && 'overrides' in r ? r : null
    },
  })
  const overview = useQuery({
    queryKey: qk.adminRights,
    queryFn: async () => {
      const o = await api.admin.rights()
      return o && typeof o === 'object' && Array.isArray(o.features) ? o : null
    },
  })
  const [modelQ, setModelQ] = useState('')
  const save = useMutation({
    mutationFn: (body: { features: Record<string, boolean | null>; models: Record<string, boolean | null> }) => api.admin.setUserRights(user.id, body),
    onSuccess: (r) => {
      qc.setQueryData(qk.userRights(user.id), r)
      announce('Rights saved')
    },
  })
  const r = rights.data
  if (rights.isError) return <Notice>{rights.error.message}</Notice>
  if (rights.isPending || overview.isPending) return <p role="status" className="text-body text-studio-muted">Loading rights…</p>
  if (!r || !overview.data) return null

  const setFeature = (key: FeatureKey, c: Choice) =>
    save.mutate({ features: { ...r.overrides.features, [key]: asValue(c) }, models: r.overrides.models })
  const setModel = (key: string, c: Choice) => save.mutate({ features: r.overrides.features, models: { ...r.overrides.models, [key]: asValue(c) } })
  const studios = overview.data.features.filter((f) => !f.own_switch)
  const needle = modelQ.trim().toLowerCase()
  // only searched-for models and the ones with an exception, so the list stays short
  const models = overview.data.models.filter((m) => (needle ? m.label.toLowerCase().includes(needle) : m.key in r.overrides.models))
  const effective = (on: boolean) => <Badge tone={on ? 'success' : 'neutral'}>{on ? 'On' : 'Off'}</Badge>

  return (
    <div className="flex flex-col gap-3">
      {r.admin ? (
        <p className="text-small text-studio-muted">Admins have every studio and model.</p>
      ) : (
        <>
          <p className="text-small text-studio-muted">
            On the {r.package} plan. Plan follows the package; Allow and Deny are exceptions for this person only.
          </p>
          <table className="w-full text-body">
            <caption className="sr-only">Studios for {user.display_name || user.email}</caption>
            <thead className="text-left text-small text-studio-muted">
              <tr>
                <th scope="col" className="py-1 font-medium">Studio</th>
                <th scope="col" className="py-1 font-medium">Choice</th>
                <th scope="col" className="py-1 font-medium">Now</th>
              </tr>
            </thead>
            <tbody>
              {studios.map((f) => (
                <tr key={f.key} className="border-t border-studio-border">
                  <th scope="row" className="py-1 pr-2 text-left font-normal">
                    {f.label}
                  </th>
                  <td className="py-1">
                    <ThreeWay name={`rf-${f.key}`} label={f.label} value={asChoice(r.overrides.features[f.key])} onChange={(c) => setFeature(f.key, c)} />
                  </td>
                  <td className="py-1">{effective(r.features[f.key])}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="rights-model-q">Models</Label>
            <Input id="rights-model-q" placeholder="Find a model to make an exception" value={modelQ} onChange={(e) => setModelQ(e.target.value)} />
            {models.length ? (
              <ul className="flex flex-col divide-y divide-studio-border">
                {models.map((m) => (
                  <li key={m.key} className="flex items-center gap-2 py-1 text-body">
                    <span className="min-w-0 flex-1 truncate">{m.label}</span>
                    <ThreeWay name={`rm-${m.key}`} label={m.label} value={asChoice(r.overrides.models[m.key])} onChange={(c) => setModel(m.key, c)} />
                    {effective(r.models === null || r.models.includes(m.key))}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-small text-studio-muted">{needle ? 'No model by that name.' : 'No model exceptions.'}</p>
            )}
          </div>
          {save.isError && <Notice>{save.error.message}</Notice>}
        </>
      )}
      <StaffEditor key={`${r.staff_role}-${r.manager_scope}`} user={user} rights={r} />
    </div>
  )
}
