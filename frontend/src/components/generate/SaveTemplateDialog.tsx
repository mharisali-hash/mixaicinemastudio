import { useId, useRef, useState } from 'react'
import { Brackets } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { useImageOptions } from '@/hooks/useMedia'
import { useSaveTemplate } from '@/hooks/useStudio'
import { placeholdersIn } from '@/lib/images'
import { findStyle } from '@/lib/styles'
import { markSlot, templateBody, type TemplateDraft } from '@/lib/templates'
import { plural } from '@/lib/utils'
import { announce } from '@/stores/ui'
import { useModelLabels } from './useModelLabel'

interface Props {
  draft: TemplateDraft | null
  onOpenChange: (open: boolean) => void
}

/** "Save as template": name it, mark the words that change as [slots], and it joins My templates. */
export function SaveTemplateDialog({ draft, onOpenChange }: Props) {
  return (
    <Dialog open={!!draft} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">{draft && <SaveForm draft={draft} onDone={() => onOpenChange(false)} />}</DialogContent>
    </Dialog>
  )
}

function SaveForm({ draft, onDone }: { draft: TemplateDraft; onDone: () => void }) {
  const uid = useId()
  const save = useSaveTemplate()
  const options = useImageOptions()
  const labelOf = useModelLabels()
  const field = useRef<HTMLTextAreaElement>(null)
  const [title, setTitle] = useState((draft.title ?? '').slice(0, 120))
  const [prompt, setPrompt] = useState(draft.prompt)
  const [labels, setLabels] = useState<string[]>(draft.refLabels?.length ? draft.refLabels : draft.target === 'edit' ? ['Photo'] : [])
  const [hint, setHint] = useState<string | null>(null)
  const slots = placeholdersIn(prompt)
  const style = findStyle(options.data?.styles ?? [], draft.style, options.data?.style_aliases)
  const kept = [
    draft.target === 'edit' ? 'Edit Image' : 'Create Image',
    draft.model && draft.model !== 'auto' ? labelOf(draft.model) : null,
    style ? `${style.label} style` : null,
    draft.aspect,
    plural(draft.count, 'image'),
  ].filter(Boolean)
  const blocked = !title.trim() ? 'Give it a name.' : !prompt.trim() ? 'The prompt is empty.' : draft.target === 'edit' && labels.some((l) => !l.trim()) ? 'Name each picture.' : null

  const mark = () => {
    const el = field.current
    if (!el) return
    const out = markSlot(prompt, el.selectionStart, el.selectionEnd)
    if (!out) {
      setHint('Select a word or short phrase in the prompt first (up to 40 letters).')
      return
    }
    setHint(null)
    setPrompt(out.text)
    requestAnimationFrame(() => {
      el.focus()
      el.setSelectionRange(out.caret, out.caret)
    })
    announce('Marked as a slot to fill in each time.')
  }

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault()
        if (blocked || save.isPending) return
        save.mutate(templateBody(draft, title, prompt, labels), {
          onSuccess: (t) => {
            announce(`Saved “${t.title}” to My templates.`)
            onDone()
          },
        })
      }}
    >
      <DialogHeader className="mb-0">
        <DialogTitle>Save as template</DialogTitle>
        <DialogDescription>It appears under My templates for everyone in this workspace, with these settings filled in.</DialogDescription>
      </DialogHeader>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${uid}-name`}>Name</Label>
        <Input id={`${uid}-name`} value={title} maxLength={120} autoFocus onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Product on marble" />
      </div>
      <div className="flex flex-col gap-1.5">
        <div className="flex items-end justify-between gap-2">
          <Label htmlFor={`${uid}-prompt`}>{draft.target === 'edit' ? 'Instruction' : 'Prompt'}</Label>
          <Button type="button" size="sm" variant="ghost" onClick={mark} onMouseDown={(e) => e.preventDefault()}>
            <Brackets aria-hidden />
            Make selection a [slot]
          </Button>
        </div>
        <Textarea id={`${uid}-prompt`} ref={field} value={prompt} rows={5} onChange={(e) => setPrompt(e.target.value)} aria-describedby={`${uid}-slots`} />
        <p id={`${uid}-slots`} className="text-small text-studio-muted">
          {slots.length
            ? `Filled in each time: ${slots.join(', ')}.`
            : 'Put [square brackets] around the words that change each time, e.g. [product]. Or keep it exactly as it is.'}
        </p>
        {hint && (
          <p role="alert" className="text-small text-studio-danger">
            {hint}
          </p>
        )}
      </div>
      {draft.target === 'edit' && (
        <fieldset className="flex flex-col gap-1.5">
          <legend className="mb-1 text-small font-medium">What each picture is</legend>
          <div className="flex flex-wrap gap-2">
            {labels.map((l, i) => (
              <Input
                key={i}
                aria-label={`Picture ${i + 1} label`}
                value={l}
                maxLength={40}
                onChange={(e) => setLabels((cur) => cur.map((x, j) => (j === i ? e.target.value : x)))}
                className="w-36"
              />
            ))}
          </div>
        </fieldset>
      )}
      <p className="text-small text-studio-muted">Keeps: {kept.join(' · ')}</p>
      {save.isError && (
        <p role="alert" className="text-small text-studio-danger">
          {save.error.message}
        </p>
      )}
      <DialogFooter className="mt-0">
        <Button type="button" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" disabled={!!blocked} loading={save.isPending} title={blocked ?? undefined}>
          Save template
        </Button>
      </DialogFooter>
    </form>
  )
}
