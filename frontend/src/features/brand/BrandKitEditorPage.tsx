import { useId, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { ArrowLeft, Save, Stamp, Trash2 } from 'lucide-react'
import { ConfirmDialog } from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Kbd } from '@/components/ui/kbd'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { EmptyState, ErrorState } from '@/components/studio/states'
import { useBrandKit, useDeleteKit, useSetDefaultKit, useUpdateKit } from '@/hooks/useBrandKits'
import { draftFrom, kitPatch, kitProblems, type BrandKit, type KitDraft } from '@/lib/brand'
import { modKey } from '@/lib/keyboard'
import { announce } from '@/stores/ui'
import { AdStyleSection } from './AdStyleSection'
import { ClosingSection } from './ClosingSection'
import { ContactSection } from './ContactSection'
import { EditorSection } from './EditorSection'
import { ExtrasSection } from './ExtrasSection'
import { FontsSection } from './FontsSection'
import { LogoSection } from './LogoSection'
import { MoodRefsSection } from './MoodRefsSection'
import { PaletteSection } from './PaletteSection'
import { ProductsSection } from './ProductsSection'

export function BrandKitEditor({ kit }: { kit: BrandKit }) {
  const uid = useId()
  const navigate = useNavigate()
  const [draft, setDraft] = useState<KitDraft>(() => draftFrom(kit))
  const [confirmDelete, setConfirmDelete] = useState(false)
  const save = useUpdateKit(kit.id)
  const makeDefault = useSetDefaultKit()
  const remove = useDeleteKit()
  const set = <K extends keyof KitDraft>(k: K, v: KitDraft[K]) => setDraft((d) => ({ ...d, [k]: v }))

  const urls = useMemo(() => Object.fromEntries(Object.entries(kit.assets ?? {}).map(([id, a]) => [id, a.missing ? null : a.media_url])), [kit.assets])
  const problems = kitProblems(draft)
  const dirty = JSON.stringify(kitPatch(draft)) !== JSON.stringify(kitPatch(draftFrom(kit)))

  const submit = () => {
    if (!dirty || problems.length || save.isPending) return
    save.mutate(kitPatch(draft), {
      onSuccess: (k) => {
        setDraft(draftFrom(k))
        announce(`${k.name} saved.`)
      },
    })
  }

  return (
    <form
      aria-labelledby={`${uid}-title`}
      onSubmit={(e) => {
        e.preventDefault()
        submit()
      }}
      onKeyDown={(e) => {
        if (e.key === 's' && (e.ctrlKey || e.metaKey)) {
          e.preventDefault()
          submit()
        }
      }}
      className="flex flex-col gap-4"
    >
      <header className="sticky top-0 z-10 -mx-4 flex flex-wrap items-center gap-3 border-b border-studio-border bg-studio-bg/95 px-4 py-3 backdrop-blur md:-mx-8 md:px-8">
        <Button asChild size="icon-sm" variant="ghost">
          <Link to="/brand-kits" aria-label="All brand kits">
            <ArrowLeft aria-hidden />
          </Link>
        </Button>
        <Stamp aria-hidden className="size-5 text-studio-accent-hover" />
        <h1 id={`${uid}-title`} className="sr-only">
          Brand kit: {kit.name}
        </h1>
        <label htmlFor={`${uid}-name`} className="sr-only">
          Kit name
        </label>
        <Input
          id={`${uid}-name`}
          value={draft.name}
          maxLength={200}
          aria-invalid={!draft.name.trim() || undefined}
          onChange={(e) => set('name', e.target.value)}
          className="h-9 max-w-80 font-display text-title font-semibold"
        />
        <label className="flex items-center gap-2 text-small">
          <Switch
            checked={kit.is_default}
            disabled={kit.is_default || makeDefault.isPending}
            onCheckedChange={(v) => v && makeDefault.mutate(kit.id, { onSuccess: () => announce(`${kit.name} is now the default brand kit.`) })}
            aria-describedby={`${uid}-defhint`}
          />
          Default kit
        </label>
        <span id={`${uid}-defhint`} className="sr-only">
          {kit.is_default ? 'This kit is the default. Make another kit the default to change it.' : 'Generators preselect the default kit.'}
        </span>
        <span className="ml-auto text-small text-studio-muted" aria-live="polite">
          {save.isPending ? 'Saving…' : dirty ? 'Unsaved changes' : 'All changes saved'}
        </span>
        <Button type="button" variant="ghost" size="sm" onClick={() => setConfirmDelete(true)}>
          <Trash2 aria-hidden />
          Delete
        </Button>
        <Button type="submit" variant="primary" disabled={!dirty || problems.length > 0} loading={save.isPending} aria-keyshortcuts="Control+S">
          <Save aria-hidden />
          Save
          <Kbd>{modKey}+S</Kbd>
        </Button>
      </header>

      {problems.length > 0 && (
        <div role="alert" className="rounded-[6px] border border-studio-danger/40 bg-studio-danger/5 p-3 text-small">
          <p className="font-medium">Fix these before saving:</p>
          <ul className="list-disc pl-5">
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </div>
      )}
      {save.isError && <ErrorState compact title="Couldn't save the kit" error={save.error} />}
      {makeDefault.isError && <ErrorState compact title="Couldn't make it the default" error={makeDefault.error} />}

      <LogoSection logos={draft.logos} urls={urls} onChange={(v) => set('logos', v)} />
      <ProductsSection products={draft.products} urls={urls} onChange={(v) => set('products', v)} />
      <PaletteSection palette={draft.palette} onChange={(v) => set('palette', v)} />
      <FontsSection fonts={draft.font_files} urls={urls} onChange={(v) => set('font_files', v)} />

      <EditorSection title="Words and look" hint="Fed to the AI when it writes and draws. Tone of voice is used for scripts and captions, not pictures.">
        <div className="grid gap-3 md:grid-cols-2">
          <div className="flex flex-col gap-1 md:col-span-2">
            <label htmlFor={`${uid}-tag`} className="text-small font-medium">
              Tagline
            </label>
            <Input id={`${uid}-tag`} dir="auto" maxLength={300} value={draft.tagline} onChange={(e) => set('tagline', e.target.value)} placeholder="e.g. Brewed slow. Poured cold. · آہستہ تیار، ٹھنڈا پیش" />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor={`${uid}-style`} className="text-small font-medium">
              Style & look
            </label>
            <Textarea id={`${uid}-style`} dir="auto" rows={3} value={draft.style_text} onChange={(e) => set('style_text', e.target.value)} placeholder="e.g. minimal, premium, soft daylight, lots of negative space" />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor={`${uid}-voice`} className="text-small font-medium">
              Tone of voice
            </label>
            <Textarea id={`${uid}-voice`} dir="auto" rows={3} value={draft.voice_text} onChange={(e) => set('voice_text', e.target.value)} placeholder="e.g. warm, confident, never shouty; short sentences" />
          </div>
        </div>
      </EditorSection>

      <ContactSection
        contact={draft.contact}
        socials={draft.socials}
        claims={draft.claims}
        onContact={(v) => set('contact', v)}
        onSocials={(v) => set('socials', v)}
        onClaims={(v) => set('claims', v)}
      />
      <MoodRefsSection ids={draft.reference_media_ids} urls={urls} onChange={(v) => set('reference_media_ids', v)} />
      <AdStyleSection kit={kit} />
      <ClosingSection
        kitId={kit.id}
        value={draft.settings.closing ?? 'auto'}
        onChange={(v) => set('settings', { ...draft.settings, closing: v })}
        hasLogo={!!kit.logos?.primary}
        dirty={dirty}
      />
      <ExtrasSection kitId={kit.id} settings={draft.settings} onChange={(s) => set('settings', s)} />

      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title={`Delete “${kit.name}”?`}
        description="Projects using it fall back to no kit. Uploaded logos, products and pictures stay in your Library."
        confirmLabel="Delete kit"
        tone="danger"
        onConfirm={() =>
          remove.mutate(kit.id, {
            onSuccess: () => {
              announce(`${kit.name} deleted.`)
              navigate('/brand-kits')
            },
          })
        }
      />
    </form>
  )
}

export function BrandKitEditorPage() {
  const { kitId } = useParams()
  const kit = useBrandKit(kitId)
  return (
    <main data-f6-region tabIndex={-1} className="relative h-full overflow-y-auto focus-visible:outline-none" aria-label="Brand kit editor">
      <div className="mx-auto flex max-w-5xl flex-col gap-4 px-4 pb-10 md:px-8">
        {kit.isPending ? (
          <div className="flex flex-col gap-4 pt-6">
            <Skeleton className="h-10 w-80" />
            <Skeleton className="h-48" />
          </div>
        ) : kit.isError ? (
          <div className="pt-6">
            <ErrorState error={kit.error} onRetry={() => kit.refetch()} />
          </div>
        ) : kit.data ? (
          // remount when switching kits so the draft starts from the right one
          <BrandKitEditor key={kit.data.id} kit={kit.data} />
        ) : (
          <EmptyState title="Brand kit not found" action={<Link to="/brand-kits">All brand kits</Link>} />
        )}
      </div>
    </main>
  )
}
