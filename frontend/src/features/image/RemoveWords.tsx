import { useId } from 'react'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

const QUICK = ['men', 'women', 'children', 'people', 'text', 'the background objects']

/** Remove…: say what should go; only that is taken out of the picture. */
export function RemoveWords({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const uid = useId()
  return (
    <div className="flex flex-col gap-2 rounded-[6px] border border-studio-border bg-studio-raised px-2.5 py-2">
      <label htmlFor={`${uid}-words`} className="text-small font-medium">
        What should go?
      </label>
      <Input id={`${uid}-words`} value={value} onChange={(e) => onChange(e.target.value)} maxLength={200} placeholder="e.g. men, the dog, the text" aria-describedby={`${uid}-hint`} />
      <div role="group" aria-label="Quick picks" className="flex flex-wrap gap-1.5">
        {QUICK.map((w) => (
          <button
            key={w}
            type="button"
            aria-pressed={value.trim() === w}
            onClick={() => onChange(w)}
            className={cn(
              'min-h-8 rounded-full border px-3 text-small transition-colors focus-visible:outline-2 focus-visible:outline-studio-accent',
              value.trim() === w ? 'border-studio-accent bg-studio-accent/15' : 'border-studio-border hover:bg-studio-panel-hover',
            )}
          >
            {w}
          </button>
        ))}
      </div>
      <p id={`${uid}-hint`} className="text-[12px] text-studio-muted">
        Only what you name is taken out and filled in from around it. Everything else stays as it was.
      </p>
    </div>
  )
}
