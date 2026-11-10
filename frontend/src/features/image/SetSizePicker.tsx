import { useId, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { clampSet, SET_MAX, SET_MIN, SET_PICKS } from '@/lib/images'

/** Edit's "Separate pictures": null is off (Variations decide), a number is how many pictures the set makes. */
export function SetSizePicker({ value, onChange }: { value: number | null; onChange: (n: number | null) => void }) {
  const id = useId()
  const [draft, setDraft] = useState<string | null>(null)
  const on = value != null
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <Switch id={`${id}-on`} checked={on} onCheckedChange={(c) => onChange(c ? SET_PICKS[0] : null)} aria-describedby={`${id}-help`} />
        <label htmlFor={`${id}-on`} className="text-body font-medium">
          Separate pictures
        </label>
      </div>
      <p id={`${id}-help`} className="text-small text-studio-muted">
        Each one a different version of your instruction, all in a new folder.
      </p>
      {on && (
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor={`${id}-n`} className="text-small text-studio-muted">
            How many
          </label>
          <Input
            id={`${id}-n`}
            type="number"
            inputMode="numeric"
            min={SET_MIN}
            max={SET_MAX}
            value={draft ?? value}
            onChange={(e) => {
              // typing "16" passes through "1": hold the text and only take numbers that fit
              setDraft(e.target.value)
              const n = Number(e.target.value)
              if (Number.isInteger(n) && n >= SET_MIN && n <= SET_MAX) onChange(n)
            }}
            onBlur={() => {
              if (draft !== null && draft.trim()) onChange(clampSet(Number(draft)))
              setDraft(null)
            }}
            className="w-16 font-mono"
          />
          <div role="group" aria-label="Quick picks" className="flex gap-1">
            {SET_PICKS.map((n) => (
              <Button key={n} type="button" size="sm" variant={value === n ? 'primary' : 'outline'} aria-pressed={value === n} onClick={() => onChange(n)}>
                {n}
              </Button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
