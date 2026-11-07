import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { ArrowLeft, Clapperboard, Download, IdCard, ImageIcon, LayoutGrid, Mic, Shirt, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/alert-dialog'
import { Make3DLink } from '@/components/generate/Make3DLink'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ErrorState } from '@/components/studio/states'
import { useCharacter, useDeleteCharacter } from '@/hooks/useCharacterStudio'
import { api } from '@/lib/api'
import type { Character } from '@/lib/types'
import { announce } from '@/stores/ui'
import { PortraitSection } from './PortraitSection'
import { LooksTab } from './LooksTab'
import { ProfileForm } from './ProfileForm'
import { SheetTab } from './SheetTab'
import { TestClipTab } from './TestClipTab'
import { VoiceTab } from './VoiceTab'

function deleteText(c: Character) {
  const films = c.films ?? []
  if (!films.length) return `${c.name}, their portrait and sheet are deleted. This can't be undone.`
  const names = films.map((f) => `“${f.title}”`).join(', ')
  return `${c.name} is in ${films.length === 1 ? 'the film' : `${films.length} films`}: ${names}. Deleting removes them from those films and their shots too, with their portrait and sheet. This can't be undone.`
}

export function CharacterPage() {
  const { characterId } = useParams()
  const navigate = useNavigate()
  const q = useCharacter(characterId)
  const remove = useDeleteCharacter()
  const [confirming, setConfirming] = useState(false)

  if (q.isPending) {
    return (
      <main className="mx-auto flex max-w-5xl flex-col gap-4 px-4 py-6 md:px-8" aria-busy="true">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-96" />
      </main>
    )
  }
  if (q.isError) {
    return (
      <main className="mx-auto max-w-5xl px-4 py-6 md:px-8">
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      </main>
    )
  }
  const c = q.data
  const films = c.films ?? []

  return (
    <main data-f6-region tabIndex={-1} className="relative h-full overflow-y-auto focus-visible:outline-none" aria-label={`Character: ${c.name}`}>
      <div className="mx-auto flex max-w-5xl flex-col gap-5 px-4 py-6 md:px-8">
        <Link to="/characters" className="inline-flex items-center gap-1 self-start text-small text-studio-muted hover:text-studio-text">
          <ArrowLeft aria-hidden className="size-3.5" />
          Character Studio
        </Link>
        <header className="flex flex-wrap items-center gap-3">
          <div className="darkroom size-14 shrink-0 overflow-hidden rounded-full">
            {c.portrait?.thumb_url && <img src={c.portrait.thumb_url} alt="" className="size-full object-cover" />}
          </div>
          <div className="min-w-0 flex-1">
            <h1 dir="auto" className="truncate font-display text-title font-semibold">
              {c.name}
            </h1>
            <p className="text-small text-studio-muted">
              {films.length ? <>In {films.map((f) => f.title).join(', ')}</> : 'Not in a film yet. Cast them from a film’s Cast page.'}
            </p>
          </div>
          {c.portrait && <Make3DLink start={{ character: c.id }} size="md" variant="secondary" ariaLabel={`Make 3D model of ${c.name}`} />}
          <Button asChild variant="secondary">
            <a href={api.characters.packUrl(c.id)} download>
              <Download aria-hidden />
              Export pack
            </a>
          </Button>
          <Button variant="ghost" onClick={() => setConfirming(true)}>
            <Trash2 aria-hidden />
            Delete
          </Button>
        </header>
        {remove.isError && <ErrorState compact title="Couldn't delete the character" error={remove.error} />}

        <Tabs defaultValue="profile" className="flex flex-col gap-4">
          <TabsList aria-label="Character sections">
            <TabsTrigger value="profile">
              <IdCard aria-hidden />
              Profile
            </TabsTrigger>
            <TabsTrigger value="portrait">
              <ImageIcon aria-hidden />
              Portrait
            </TabsTrigger>
            <TabsTrigger value="sheet">
              <LayoutGrid aria-hidden />
              Sheet
            </TabsTrigger>
            <TabsTrigger value="looks">
              <Shirt aria-hidden />
              Looks
            </TabsTrigger>
            <TabsTrigger value="voice">
              <Mic aria-hidden />
              Voice
            </TabsTrigger>
            <TabsTrigger value="test-clip">
              <Clapperboard aria-hidden />
              Test clip
            </TabsTrigger>
          </TabsList>
          <TabsContent value="profile">
            <ProfileForm character={c} />
          </TabsContent>
          <TabsContent value="portrait">
            <div className="max-w-xl">
              <PortraitSection key={c.id} character={c} />
            </div>
          </TabsContent>
          <TabsContent value="sheet">
            <SheetTab character={c} />
          </TabsContent>
          <TabsContent value="looks">
            <LooksTab character={c} />
          </TabsContent>
          <TabsContent value="voice">
            <VoiceTab character={c} />
          </TabsContent>
          <TabsContent value="test-clip">
            <TestClipTab character={c} />
          </TabsContent>
        </Tabs>
      </div>
      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title={`Delete ${c.name}?`}
        description={deleteText(c)}
        confirmLabel="Delete everywhere"
        tone="danger"
        onConfirm={() =>
          remove.mutate(
            { id: c.id },
            {
              onSuccess: () => {
                announce(`${c.name} deleted.`)
                navigate('/characters')
              },
            },
          )
        }
      />
    </main>
  )
}
