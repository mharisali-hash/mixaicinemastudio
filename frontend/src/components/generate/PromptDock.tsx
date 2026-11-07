import { useId, useLayoutEffect, useRef, useState } from 'react'
import { ChevronUp, SlidersHorizontal } from 'lucide-react'
import { Textarea } from '@/components/ui/textarea'
import { MentionTextarea, type MentionOptions } from '@/components/mentions/MentionTextarea'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { useBreakpoint } from '@/hooks/useBreakpoint'
import { readDockMode, writeDockMode, type DockMode } from '@/lib/dockPrefs'
import type { MediaKind } from '@/lib/types'
import { cn } from '@/lib/utils'
import { IntoChip } from './DockChips'
import { MagicPromptBar } from './MagicPromptBar'
import type { MagicPrompt } from './useMagicPrompt'

interface Props {
  title: string
  icon: React.ReactNode
  // template button, template badge…
  headerExtra?: React.ReactNode
  promptLabel: string
  prompt: string
  onPrompt: (v: string) => void
  placeholder: string
  maxLength?: number
  promptRef?: React.RefObject<HTMLTextAreaElement | null>
  // text tied to the prompt with aria-describedby (e.g. the Qwen quotes hint)
  promptHint?: React.ReactNode
  // under the prompt, e.g. the [placeholder] helper
  belowPrompt?: React.ReactNode
  // source images, start/end frames: the "refs" area
  refs?: React.ReactNode
  chips: React.ReactNode
  // P4 "Generate into…": shows the folder chip for this kind of result
  into?: MediaKind
  // negative, seed, steps…; shown in Advanced mode
  advanced?: React.ReactNode
  magic?: MagicPrompt
  // @-mentions: on by default; pass the reference budget so the dock can count against it
  mentions?: MentionOptions | false
  error?: React.ReactNode
  footer: React.ReactNode
  onSubmit: () => void
  // sticky on top of the results (desktop) or a bottom sheet (phone); off on embedded uses
  docked?: boolean
  className?: string
}

const MAX_H = 240

/** The prompt dock shared by every generator page. */
export function PromptDock({
  title,
  icon,
  headerExtra,
  promptLabel,
  prompt,
  onPrompt,
  placeholder,
  maxLength,
  promptRef,
  promptHint,
  belowPrompt,
  refs,
  chips,
  into,
  advanced,
  magic,
  mentions = {},
  error,
  footer,
  onSubmit,
  docked = true,
  className,
}: Props) {
  const uid = useId()
  const own = useRef<HTMLTextAreaElement>(null)
  const field = promptRef ?? own
  const [mode, setMode] = useState<DockMode>(readDockMode)
  const phone = useBreakpoint() === 'mobile'
  const sheet = docked && phone
  const [open, setOpen] = useState(false)
  const showOptions = !sheet || open
  // the page's own heading when docked; a section heading when embedded (Home)
  const Heading = docked ? 'h1' : 'h2'

  // grow with the text up to a limit, then scroll
  useLayoutEffect(() => {
    const el = field.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(MAX_H, Math.max(el.scrollHeight, 0))}px`
  }, [prompt, field])

  const changeMode = (m: DockMode) => {
    setMode(m)
    writeDockMode(m)
  }

  return (
    <form
      aria-labelledby={`${uid}-title`}
      onSubmit={(e) => {
        e.preventDefault()
        onSubmit()
      }}
      onKeyDown={(e) => {
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
          e.preventDefault()
          onSubmit()
        }
      }}
      className={cn(
        'flex flex-col gap-3 border border-studio-border-strong bg-studio-panel p-3 shadow-card md:p-4',
        docked
          ? 'z-20 max-md:sticky max-md:bottom-0 max-md:order-last max-md:mt-auto max-md:max-h-[85vh] max-md:overflow-y-auto max-md:-mx-4 max-md:rounded-t-[12px] max-md:border-x-0 max-md:border-b-0 max-md:shadow-modal md:sticky md:top-0 md:rounded-[8px]'
          : 'rounded-[8px]',
        className,
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        {icon}
        <Heading id={`${uid}-title`} className="font-display text-panel font-semibold md:text-title">
          {title}
        </Heading>
        {headerExtra}
        <div className="ml-auto flex items-center gap-2">
          {advanced && showOptions && (
            <ToggleGroup type="single" aria-label="Controls" value={mode} onValueChange={(v) => v && changeMode(v as DockMode)} className="h-8">
              <ToggleGroupItem value="simple" className="h-6 px-2.5 text-small">
                Simple
              </ToggleGroupItem>
              <ToggleGroupItem value="advanced" className="h-6 px-2.5 text-small">
                Advanced
              </ToggleGroupItem>
            </ToggleGroup>
          )}
          {sheet && (
            <button
              type="button"
              aria-expanded={open}
              aria-controls={`${uid}-opts`}
              onClick={() => setOpen((o) => !o)}
              className="inline-flex h-8 items-center gap-1 rounded-full border border-studio-border-strong bg-studio-raised px-3 text-small"
            >
              {open ? <ChevronUp aria-hidden className="size-3.5 rotate-180" /> : <SlidersHorizontal aria-hidden className="size-3.5" />}
              {open ? 'Hide options' : 'Options'}
            </button>
          )}
        </div>
      </div>

      {refs}

      <div className="flex flex-col gap-1.5">
        <label htmlFor={`${uid}-prompt`} className="sr-only">
          {promptLabel}
        </label>
        {mentions ? (
          <MentionTextarea
            {...mentions}
            textareaRef={field}
            id={`${uid}-prompt`}
            rows={sheet ? 2 : 3}
            maxLength={maxLength}
            value={prompt}
            onValueChange={onPrompt}
            placeholder={placeholder}
            aria-describedby={[promptHint ? `${uid}-hint` : null, `${uid}-at`].filter(Boolean).join(' ')}
            className="min-h-14 resize-none text-[15px] leading-6"
          />
        ) : (
          <Textarea
            ref={field}
            id={`${uid}-prompt`}
            rows={sheet ? 2 : 3}
            maxLength={maxLength}
            value={prompt}
            onChange={(e) => onPrompt(e.target.value)}
            placeholder={placeholder}
            // Urdu or Arabic prompts read right to left
            dir="auto"
            aria-describedby={promptHint ? `${uid}-hint` : undefined}
            className="min-h-14 resize-none text-[15px] leading-6"
          />
        )}
        {mentions && (
          <span id={`${uid}-at`} className="sr-only">
            Type @ to mention cast, locations, products or logos.
          </span>
        )}
        {promptHint && (
          <p id={`${uid}-hint`} className="text-small text-studio-muted">
            {promptHint}
          </p>
        )}
        {belowPrompt}
      </div>

      {showOptions && (
        <div id={`${uid}-opts`} className="flex flex-col gap-3">
          <div role="group" aria-label="Settings" className="flex flex-wrap items-center gap-1.5">
            {chips}
            {into && <IntoChip kind={into} />}
          </div>
          {advanced && mode === 'advanced' && (
            <div role="group" aria-label="Advanced settings" className="rounded-[6px] border border-studio-border bg-studio-raised/60 p-3">
              {advanced}
            </div>
          )}
          {magic && <MagicPromptBar magic={magic} canPreview={prompt.trim().length >= 3} />}
        </div>
      )}

      {error}
      {footer}
    </form>
  )
}
