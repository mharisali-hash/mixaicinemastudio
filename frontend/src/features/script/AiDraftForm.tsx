import { useState } from 'react'
import { Loader2, Sparkles } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ErrorState } from '@/components/studio/states'
import { AerabOnNote } from '@/components/text/AerabButton'
import { useAiJob } from '@/hooks/useAi'
import { hasArabicScript } from '@/lib/aerab'
import { api } from '@/lib/api'

// "Let AI draft" on an empty scene: one line in, a full scene back (written straight in, the scene is empty).
export function AiDraftForm({ sceneId, onCancel }: { sceneId: string; onCancel: () => void }) {
  const [idea, setIdea] = useState('')
  const draft = useAiJob((text: string) => api.ai.assist(sceneId, { action: 'draft_from_idea', idea: text }))

  return (
    <form
      className="flex w-full max-w-md flex-col gap-2 text-left"
      onSubmit={(e) => {
        e.preventDefault()
        if (idea.trim() && !draft.working) draft.run(idea.trim())
      }}
    >
      <label htmlFor="ai-draft-idea" className="section-label">
        One-line idea
      </label>
      <Input
        id="ai-draft-idea"
        autoFocus
        value={idea}
        disabled={draft.working}
        onChange={(e) => setIdea(e.target.value)}
        onKeyDown={(e) => e.key === 'Escape' && !draft.working && onCancel()}
        placeholder="The keeper climbs the stairs one last time"
        dir="auto"
      />
      {hasArabicScript(idea) && <AerabOnNote />}
      {draft.working ? (
        <p role="status" className="inline-flex items-center gap-1.5 text-small text-studio-muted">
          <Loader2 aria-hidden className="size-3.5 animate-spin text-studio-accent" />
          {draft.job?.status === 'queued' ? 'Waiting for a worker…' : draft.job?.message || 'Sending to the AI…'}
        </p>
      ) : (
        <div className="flex gap-2">
          <Button type="submit" variant="primary" disabled={!idea.trim()}>
            <Sparkles aria-hidden />
            Draft scene
          </Button>
          <Button type="button" variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        </div>
      )}
      {draft.error && <ErrorState compact title="The AI couldn't draft this scene" error={draft.error} />}
    </form>
  )
}
