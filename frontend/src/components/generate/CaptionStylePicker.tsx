import { useId } from 'react'
import { cn } from '@/lib/utils'

export type CaptionStyle = 'classic' | 'pill' | 'highlight' | 'clean'

export const CAPTION_STYLES: { value: CaptionStyle; label: string; hint: string }[] = [
  { value: 'classic', label: 'Classic', hint: 'Plain subtitles on a dark band' },
  { value: 'pill', label: 'Karaoke', hint: 'Each word lights up as it is said' },
  { value: 'highlight', label: 'Highlight', hint: 'A gold block behind the word being said' },
  { value: 'clean', label: 'Word by word', hint: 'Words rise in one at a time' },
]

const SAMPLE = ['Every', 'word', 'in', 'time']

// CSS-only previews, so nothing has to render on the server to show what a style looks like
function Preview({ style }: { style: CaptionStyle }) {
  return (
    <span aria-hidden className="darkroom relative flex h-12 items-end justify-center overflow-hidden rounded-[4px] pb-1.5">
      <span
        className={cn(
          'flex gap-1 text-[11px] leading-tight font-semibold',
          style === 'classic' && 'rounded-[2px] bg-black/60 px-1.5 py-0.5 text-white',
          style === 'pill' && 'rounded-[6px] bg-[#faf6ee] px-2 py-0.5 text-[#8f8576]',
        )}
      >
        {SAMPLE.map((w, i) => (
          <span key={w} className={cn('caption-demo relative', `caption-demo-${style}`)} style={{ animationDelay: `${i * 0.45}s` }}>
            {w}
          </span>
        ))}
      </span>
    </span>
  )
}

export function CaptionStylePicker({
  value,
  onChange,
  disabled,
  legend = 'Caption style',
}: {
  value: CaptionStyle
  onChange: (v: CaptionStyle) => void
  disabled?: boolean
  legend?: string
}) {
  const uid = useId()
  return (
    <fieldset disabled={disabled} className="flex flex-col gap-1.5">
      <legend className="section-label mb-1">{legend}</legend>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {CAPTION_STYLES.map((s) => (
          <label
            key={s.value}
            className={cn(
              'flex cursor-pointer flex-col gap-1 rounded-[6px] border p-1.5 transition-colors duration-150',
              'border-studio-border-strong bg-studio-raised hover:bg-studio-panel-hover',
              'has-[:checked]:border-studio-accent has-[:checked]:bg-studio-accent-soft has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-studio-accent',
            )}
          >
            <input
              type="radio"
              name={`${uid}-caption`}
              value={s.value}
              checked={value === s.value}
              onChange={() => onChange(s.value)}
              aria-describedby={`${uid}-${s.value}`}
              className="sr-only"
            />
            <Preview style={s.value} />
            <span className="text-small font-medium">{s.label}</span>
            <span id={`${uid}-${s.value}`} className="text-[11px] leading-snug text-studio-muted">
              {s.hint}
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  )
}
