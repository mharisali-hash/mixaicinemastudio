import { Link, useNavigate } from 'react-router'
import { ChevronRight, KeyRound, LogOut, UserCog } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { useLogout, useMe } from '@/hooks/useAuth'
import { useQuillCursor } from '@/lib/quill'
import { Card, ComfyServicesCard, ConnectionsCard, LlmCard, TemplatesCard, UpscaleCard } from './SystemChecks'

function AccountCard() {
  const { data: me } = useMe()
  const logout = useLogout()
  const navigate = useNavigate()
  const [quill, setQuill] = useQuillCursor()
  return (
    <Card title="Account & preferences">
      {me && (
        <dl className="grid grid-cols-[80px_1fr] gap-x-3 gap-y-1 text-body">
          <dt className="text-studio-muted">Name</dt>
          <dd>{me.display_name || '—'}</dd>
          <dt className="text-studio-muted">Email</dt>
          <dd className="break-all">{me.email}</dd>
          <dt className="text-studio-muted">Role</dt>
          <dd className="capitalize">{me.role}</dd>
        </dl>
      )}
      <label className="flex items-center gap-3 rounded-[6px] border border-studio-border bg-studio-raised p-2.5">
        <Switch checked={quill} onCheckedChange={setQuill} aria-describedby="quill-hint" />
        <span>
          <span className="block text-body font-medium">Quill cursor</span>
          <span id="quill-hint" className="block text-small text-studio-muted">
            The feather pen pointer. Turn it off for the standard arrow; touch screens never use it.
          </span>
        </span>
      </label>
      <Link to="/settings/account" className="flex items-center gap-3 self-start rounded-[6px] border border-studio-border-strong bg-studio-raised px-3 py-2 text-body font-medium hover:border-studio-border-hover hover:bg-studio-panel-hover focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-studio-gold/50">
        <UserCog aria-hidden className="size-4 text-studio-accent" />
        Name, password and sign-ins
        <ChevronRight aria-hidden className="size-4 text-studio-muted" />
      </Link>
      <Button
        variant="secondary"
        className="self-start"
        loading={logout.isPending}
        onClick={() => logout.mutate(undefined, { onSettled: () => navigate('/login', { replace: true }) })}
      >
        <LogOut aria-hidden />
        Log out
      </Button>
    </Card>
  )
}

// contract v27: only rendered for users the server lets near the API (the admin while PUBLIC_API is off)
function ApiCard() {
  return (
    <Card title="API">
      <p className="text-body text-studio-muted">Tokens for your own scripts and tools, and the API reference.</p>
      <Link
        to="/settings/api"
        className="flex items-center gap-3 self-start rounded-[6px] border border-studio-border-strong bg-studio-raised px-3 py-2 text-body font-medium hover:border-studio-border-hover hover:bg-studio-panel-hover focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-studio-gold/50"
      >
        <KeyRound aria-hidden className="size-4 text-studio-accent" />
        API tokens
        <ChevronRight aria-hidden className="size-4 text-studio-muted" />
      </Link>
    </Card>
  )
}

export function SettingsPage() {
  const { data: me } = useMe()
  return (
    <main data-f6-region tabIndex={-1} className="relative h-full overflow-y-auto focus-visible:outline-none" aria-labelledby="settings-title">
      <div className="mx-auto flex max-w-5xl flex-col gap-4 px-4 py-6 md:px-8">
        <div>
          <h1 id="settings-title" className="font-display text-title font-semibold">
            Settings
          </h1>
          <p className="text-body text-studio-muted">System check for the GPU box, plus your account.</p>
        </div>
        <div className="grid gap-4 lg:grid-cols-2">
          <ConnectionsCard />
          <AccountCard />
          {/* the model and workflow checks reach into the GPU box; the server keeps them for the admin */}
          {me?.admin && <ComfyServicesCard />}
          {me?.admin && <LlmCard />}
          <UpscaleCard />
          {me?.api_access && <ApiCard />}
        </div>
        {me?.admin && <TemplatesCard />}
        {/* TODO: studio defaults (takes per shot, draft/final) once the backend stores per-user preferences */}
      </div>
    </main>
  )
}
