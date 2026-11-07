import { useId, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { Clapperboard, ImageIcon, SlidersHorizontal, Wand2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Kbd } from '@/components/ui/kbd'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { ErrorState } from '@/components/studio/states'
import { QuickCreateForm } from '@/features/quick/QuickCreateForm'
import { useImageGenerate } from '@/hooks/useMedia'
import { DEFAULT_IMAGE_FORM, imagePayload } from '@/lib/images'
import { modKey } from '@/lib/keyboard'
import { readJSON, writeJSON } from '@/lib/storage'
import { announce } from '@/stores/ui'

type Mode = 'video' | 'image'
const KEY = 'mixai.home.create'

function ImageQuickForm() {
  const uid = useId()
  const navigate = useNavigate()
  const generate = useImageGenerate()
  const [prompt, setPrompt] = useState('')
  const canSubmit = prompt.trim().length >= 3 && !generate.isPending

  const submit = () => {
    if (!canSubmit) return
    generate.mutate(imagePayload({ ...DEFAULT_IMAGE_FORM, prompt }), {
      onSuccess: () => {
        announce('Creating 2 images. They appear on the Create Image page as they finish.')
        navigate('/image/generate')
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
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
          e.preventDefault()
          submit()
        }
      }}
      className="flex flex-col gap-3 rounded-[8px] border border-studio-border-strong bg-studio-panel p-4 shadow-card"
    >
      <div className="flex items-baseline gap-2">
        <ImageIcon aria-hidden className="size-4 shrink-0 self-center text-studio-accent-hover" />
        <h2 id={`${uid}-title`} className="font-display text-title font-semibold">
          Quick Image
        </h2>
        <p className="text-small text-studio-muted max-sm:hidden">Two square variations with Z-Image Turbo, a few seconds each.</p>
      </div>
      <Label htmlFor={`${uid}-prompt`} className="sr-only">
        Describe your image
      </Label>
      <Textarea
        id={`${uid}-prompt`}
        rows={3}
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        placeholder="Describe your image… e.g. A brass telescope on a chart table, warm window light"
        className="min-h-24 text-[15px] leading-6"
      />
      {generate.isError && <ErrorState compact title="Couldn't start the images" error={generate.error} />}
      <div className="flex flex-wrap items-center justify-end gap-3">
        <Button asChild variant="ghost" className="mr-auto">
          <Link to={`/image/generate${prompt.trim() ? `?prompt=${encodeURIComponent(prompt.trim())}` : ''}`}>
            <SlidersHorizontal aria-hidden />
            More options
          </Link>
        </Button>
        <Button type="submit" size="lg" variant="primary" disabled={!canSubmit} loading={generate.isPending} aria-keyshortcuts="Control+Enter">
          <Wand2 aria-hidden />
          Create images
          <Kbd>{modKey}+Enter</Kbd>
        </Button>
      </div>
    </form>
  )
}

/** Home's prompt bar: the Quick Create form, switchable between a video and an image. */
export function HomeCreateBar() {
  const [mode, setMode] = useState<Mode>(() => readJSON<{ mode: Mode }>(KEY, { mode: 'video' }).mode)
  const pick = (m: Mode) => {
    setMode(m)
    writeJSON(KEY, { mode: m })
  }
  return (
    <div className="flex flex-col gap-2">
      <ToggleGroup type="single" aria-label="What to make" value={mode} onValueChange={(v) => v && pick(v as Mode)} className="self-start">
        <ToggleGroupItem value="video">
          <Clapperboard aria-hidden />
          Video
        </ToggleGroupItem>
        <ToggleGroupItem value="image">
          <ImageIcon aria-hidden />
          Image
        </ToggleGroupItem>
      </ToggleGroup>
      {mode === 'video' ? <QuickCreateForm /> : <ImageQuickForm />}
    </div>
  )
}
