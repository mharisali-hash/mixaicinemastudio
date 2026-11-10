import { useId } from 'react'
import { RadioGroup } from 'radix-ui'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { IMAGE_ASPECTS, MAX_COUNT } from '@/lib/images'
import type { ImageAspect } from '@/lib/types'

export function ImageAspectTiles({
  value,
  onChange,
  allowAuto,
  autoLabel = 'Source',
}: {
  value: ImageAspect | null
  onChange: (v: ImageAspect | null) => void
  // Edit can keep the source's own shape
  allowAuto?: boolean
  autoLabel?: string
}) {
  const id = useId()
  const tile =
    'flex flex-col items-center gap-0.5 rounded-[6px] border p-1.5 text-center transition-colors duration-150 border-studio-border-strong bg-studio-raised text-studio-muted hover:bg-studio-panel-hover data-[state=checked]:border-studio-accent data-[state=checked]:bg-studio-accent-soft data-[state=checked]:text-studio-text'
  return (
    <div>
      <div id={id} className="section-label mb-2">
        Aspect
      </div>
      <RadioGroup.Root
        aria-labelledby={id}
        value={value ?? 'auto'}
        onValueChange={(v) => onChange(v === 'auto' ? null : (v as ImageAspect))}
        className="grid grid-cols-4 gap-1.5 sm:grid-cols-8"
      >
        {allowAuto && (
          <RadioGroup.Item value="auto" aria-label="Same as the source" className={tile}>
            <span aria-hidden className="flex h-6 items-center text-small">
              ⟲
            </span>
            <span aria-hidden className="text-small font-medium">
              {autoLabel}
            </span>
          </RadioGroup.Item>
        )}
        {IMAGE_ASPECTS.map((a) => (
          <RadioGroup.Item key={a.value} value={a.value} aria-label={`${a.value} ${a.label}, ${a.size}`} className={tile}>
            <span className="flex h-6 items-center" aria-hidden>
              <span className="rounded-[2px] border-[1.5px] border-current" style={{ width: a.w, height: a.h }} />
            </span>
            <span aria-hidden className="text-small font-medium">
              {a.value}
            </span>
          </RadioGroup.Item>
        ))}
      </RadioGroup.Root>
    </div>
  )
}

export function CountPicker({ value, onChange, max = MAX_COUNT }: { value: number; onChange: (n: number) => void; max?: number }) {
  const id = useId()
  return (
    <div>
      <div id={id} className="section-label mb-2">
        Variations
      </div>
      <ToggleGroup type="single" aria-labelledby={id} value={String(value)} onValueChange={(v) => v && onChange(Number(v))} className="flex-wrap">
        {Array.from({ length: max }, (_, i) => (
          <ToggleGroupItem key={i} value={String(i + 1)} aria-label={`${i + 1} ${i ? 'images' : 'image'}`} className="w-10">
            {i + 1}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
    </div>
  )
}
