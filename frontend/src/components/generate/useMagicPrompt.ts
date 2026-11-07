import { useState } from 'react'
import { usePromptEnhance } from '@/hooks/useGenEstimate'
import { plainPrompt, relink, uniqueMentions } from '@/lib/mentions'
import type { MagicFields, MagicMode, PromptEnhanceRequest } from '@/lib/types'

interface Draft {
  // the prompt it was made from; editing the prompt afterwards makes it stale
  from: string
  text: string
  changed: boolean
  notes?: string | null
  accepted: boolean
}

export interface MagicContext {
  kind: PromptEnhanceRequest['kind']
  model?: string
  style?: string | null
  brandKitId?: string
}

const KEY = 'jafat.promptCorrection'
const MODES: MagicMode[] = ['auto', 'on', 'off']

// the choice is the person's, so it outlives a cleared prompt (which remounts the form) and a reload
function remembered(): MagicMode {
  try {
    const v = localStorage.getItem(KEY) as MagicMode | null
    return v && MODES.includes(v) ? v : 'auto'
  } catch {
    return 'auto'
  }
}

/** Prompt correction (Magic prompt): Auto · Always · Off, plus an on-demand preview the user can edit and accept. */
export function useMagicPrompt(prompt: string, ctx: MagicContext) {
  const [mode, setModeState] = useState<MagicMode>(remembered)
  const [draft, setDraft] = useState<Draft | null>(null)
  const enhance = usePromptEnhance()
  const current = prompt.trim()
  const stale = !!draft && draft.from !== current

  const preview = () => {
    if (current.length < 3) return
    enhance.mutate(
      {
        // the writer sees names; mentions are linked back up when the draft is used
        prompt: plainPrompt(current),
        kind: ctx.kind,
        mode: mode === 'off' ? 'on' : mode,
        ...(ctx.model ? { model: ctx.model } : {}),
        ...(ctx.style ? { style: ctx.style } : {}),
        ...(ctx.brandKitId ? { brand_kit_id: ctx.brandKitId } : {}),
      },
      { onSuccess: (r) => setDraft({ from: current, text: r?.enhanced || current, changed: !!r?.changed, notes: r?.notes, accepted: false }) },
    )
  }

  const setMode = (m: MagicMode) => {
    setModeState(m)
    try {
      localStorage.setItem(KEY, m)
    } catch {
      // private windows may refuse; the choice still holds for this page
    }
    if (m === 'off') setDraft(null)
  }

  /** The prompt and magic fields for the request body. */
  const fields = (): { prompt: string } & MagicFields => {
    if (draft?.accepted && !stale) return { prompt: relink(draft.text.trim(), uniqueMentions(current)), prompt_enhanced: true }
    // "auto" is the server default, so it isn't sent; older servers never see the field
    return mode === 'auto' ? { prompt: current } : { prompt: current, magic_prompt: mode }
  }

  return {
    mode,
    setMode,
    draft,
    stale,
    preview,
    previewing: enhance.isPending,
    error: enhance.error,
    edit: (text: string) => setDraft((d) => (d ? { ...d, text } : d)),
    accept: () => setDraft((d) => (d ? { ...d, accepted: true } : d)),
    undo: () => setDraft((d) => (d ? { ...d, accepted: false } : d)),
    discard: () => setDraft(null),
    fields,
    // the panel shows when there's something worth looking at
    showPanel: !!draft && (mode === 'on' || draft.changed || draft.accepted),
  }
}

export type MagicPrompt = ReturnType<typeof useMagicPrompt>
