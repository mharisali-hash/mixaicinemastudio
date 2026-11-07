import { useId } from 'react'
import { Check, Sparkles, Undo2, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { ApiError } from '@/lib/api'
import type { MagicMode } from '@/lib/types'
import type { MagicPrompt } from './useMagicPrompt'

const MODES: { value: MagicMode; label: string; hint: string }[] = [
  { value: 'auto', label: 'Auto prompt correction', hint: 'Improves short prompts, leaves detailed ones as written' },
  { value: 'on', label: 'Always correct', hint: 'Always improves the prompt before running' },
  { value: 'off', label: 'Off: use my prompt exactly', hint: 'Sends exactly what you wrote' },
]

function errorText(e: unknown) {
  if (e instanceof ApiError && (e.status === 404 || e.status === 405)) return "Prompt enhancement isn't available on this server yet."
  return e instanceof Error ? e.message : 'Could not enhance the prompt.'
}

export function MagicPromptBar({ magic, canPreview }: { magic: MagicPrompt; canPreview: boolean }) {
  const uid = useId()
  const { draft, stale } = magic
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor={`${uid}-m`} className="flex items-center gap-1 text-small text-studio-muted">
          <Sparkles aria-hidden className="size-3.5 text-studio-accent-hover" />
          Prompt correction
        </label>
        <select
          id={`${uid}-m`}
          value={magic.mode}
          onChange={(e) => magic.setMode(e.target.value as MagicMode)}
          aria-describedby={`${uid}-h`}
          className="h-8 rounded-[6px] border border-studio-border-strong bg-studio-raised px-2 text-small focus-visible:outline-2 focus-visible:outline-studio-accent"
        >
          {MODES.map((m) => (
            <option key={m.value} value={m.value}>
              {m.label}
            </option>
          ))}
        </select>
        <span id={`${uid}-h`} className="text-small text-studio-muted max-md:sr-only">
          {MODES.find((m) => m.value === magic.mode)?.hint}
        </span>
        {magic.mode !== 'off' && (
          <Button type="button" size="sm" variant="ghost" disabled={!canPreview} loading={magic.previewing} onClick={magic.preview}>
            Preview enhancement
          </Button>
        )}
      </div>
      {magic.error != null && !draft && (
        <p className="text-small text-studio-muted" role="status">
          {errorText(magic.error)}
        </p>
      )}
      {draft && !magic.showPanel && (
        <p className="text-small text-studio-muted" role="status">
          Your prompt is already detailed, so it goes as written.
        </p>
      )}
      {magic.showPanel && draft && (
        <section aria-label="Enhanced prompt" className="flex flex-col gap-2 rounded-[6px] border border-studio-gold/60 bg-studio-raised p-2.5">
          <label htmlFor={`${uid}-t`} className="section-label">
            Enhanced prompt
          </label>
          <Textarea
            id={`${uid}-t`}
            dir="auto"
            rows={3}
            value={draft.text}
            onChange={(e) => magic.edit(e.target.value)}
            readOnly={draft.accepted && !stale}
            className="min-h-16 text-body"
          />
          {draft.notes && <p className="text-small text-studio-muted">{draft.notes}</p>}
          {stale ? (
            <p className="text-small text-studio-warning" role="status">
              You changed the prompt after this preview. Preview again, or it runs with {magic.mode === 'on' ? 'Always correct' : 'Auto prompt correction'}.
            </p>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              {draft.accepted ? (
                <>
                  <span className="inline-flex items-center gap-1 text-small text-studio-success" role="status">
                    <Check aria-hidden className="size-3.5" />
                    Runs with the enhanced prompt
                  </span>
                  <Button type="button" size="sm" variant="ghost" onClick={magic.undo}>
                    <Undo2 aria-hidden />
                    Edit again
                  </Button>
                </>
              ) : (
                <Button type="button" size="sm" variant="secondary" onClick={magic.accept}>
                  <Check aria-hidden />
                  Use enhanced prompt
                </Button>
              )}
              <Button type="button" size="sm" variant="ghost" onClick={magic.discard}>
                <X aria-hidden />
                Keep mine
              </Button>
            </div>
          )}
        </section>
      )}
    </div>
  )
}
