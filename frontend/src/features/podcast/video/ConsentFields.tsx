import { useId } from 'react'
import { ShieldCheck } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { LIKENESS_STATEMENT } from './vp'

// the same shape as the voice-clone consent in AddVoiceDialog, worded for a face
export function ConsentFields({
  name,
  agreed,
  onName,
  onAgreed,
}: {
  name: string
  agreed: boolean
  onName: (v: string) => void
  onAgreed: (v: boolean) => void
}) {
  const uid = useId()
  return (
    <fieldset className="flex flex-col gap-2 rounded-[6px] border border-studio-border-strong bg-studio-raised/60 p-3">
      <legend className="float-left mb-1 flex w-full items-center gap-1.5 text-body font-medium">
        <ShieldCheck aria-hidden className="size-4 text-studio-accent" />
        Consent
      </legend>
      <p className="text-small text-studio-muted">A real person's face needs their agreement. The name, the statement and today's date are kept with the video.</p>
      <div>
        <Label htmlFor={`${uid}-who`} className="mb-1.5">
          Person's name
        </Label>
        <Input id={`${uid}-who`} value={name} maxLength={120} onChange={(e) => onName(e.target.value)} placeholder="The person in the photo" />
      </div>
      <label className="flex items-start gap-2 text-body">
        <input type="checkbox" checked={agreed} onChange={(e) => onAgreed(e.target.checked)} className="mt-1 size-4 accent-studio-accent" />
        <span>{LIKENESS_STATEMENT}</span>
      </label>
    </fieldset>
  )
}
