import { useId, useRef, useState } from 'react'
import { FileText, FileUp, ScrollText, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { AerabOnNote } from '@/components/text/AerabButton'
import { useAddSource, usePasteScript, usePatchEpisode, useRemoveSource } from '@/hooks/usePodcast'
import { useVoiceEngines } from '@/hooks/useVoices'
import { isArabicScriptLang } from '@/lib/aerab'
import { FORMATS, type Episode, type Format } from '@/lib/podcast'
import { allLanguages } from '@/lib/voice'
import { cn } from '@/lib/utils'
import { announce } from '@/stores/ui'
import { SELECT } from '../voice/AddVoiceDialog'

type Mode = 'topic' | 'paste' | 'upload' | 'script'

const MODES: { id: Mode; label: string; hint: string }[] = [
  { id: 'topic', label: 'A topic', hint: 'The writer works from what it knows and from your hosts’ personas.' },
  { id: 'paste', label: 'Paste material', hint: 'An article, notes or a transcript. The script sticks to its facts.' },
  { id: 'upload', label: 'Upload a document', hint: '.txt, .md, .pdf or .docx up to 20 MB. Long ones are summarised first.' },
  { id: 'script', label: 'I have a script', hint: 'HOST: lines, one turn each. A line starting with # begins a chapter.' },
]

const DOC_ACCEPT = '.txt,.md,.markdown,.pdf,.docx,text/plain,text/markdown,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document'

const startMode = (ep: Episode): Mode => (ep.sources.docs.length ? 'upload' : ep.sources.text ? 'paste' : 'topic')

export function SourceStep({ ep, onNext, onScript }: { ep: Episode; onNext: () => void; onScript: () => void }) {
  const uid = useId()
  const patch = usePatchEpisode(ep.id)
  const add = useAddSource(ep.id)
  const remove = useRemoveSource(ep.id)
  const paste = usePasteScript(ep.id)
  const { engines } = useVoiceEngines()
  const languages = allLanguages(engines.map((e) => e.languages))
  const [mode, setMode] = useState<Mode>(() => startMode(ep))
  const [topic, setTopic] = useState(ep.sources.topic)
  const [text, setText] = useState(ep.sources.text)
  const [script, setScript] = useState('')
  const file = useRef<HTMLInputElement>(null)

  const saveTopic = () => topic.trim() !== ep.sources.topic && patch.mutate({ sources: { topic: topic.trim() } })
  const saveText = () => text.trim() !== ep.sources.text && patch.mutate({ sources: { text: text.trim() } })
  const upload = (files: FileList | null) => {
    for (const f of Array.from(files ?? [])) add.mutate(f, { onSuccess: () => announce(`${f.name} added to the material.`) })
    if (file.current) file.current.value = ''
  }
  const applyScript = () =>
    paste.mutate(script, {
      onSuccess: (next) => {
        announce(`Script in: ${next.chapters.length} chapter${next.chapters.length === 1 ? '' : 's'}, ${next.hosts.length} host${next.hosts.length === 1 ? '' : 's'}.`)
        onScript()
      },
    })
  const error = patch.error ?? add.error ?? remove.error ?? paste.error

  return (
    <>
      <div>
        <h2 className="font-display text-title font-semibold">Where the episode comes from</h2>
        <p className="text-small text-studio-muted">Set the basics, then give the writer something to work from.</p>
      </div>
      <fieldset className="grid gap-3 rounded-[6px] border border-studio-border bg-studio-panel p-3 sm:grid-cols-3">
        <legend className="section-label px-1">Episode</legend>
        <label className="flex flex-col gap-1 text-small">
          <span>Format</span>
          <select value={ep.format} onChange={(e) => patch.mutate({ format: e.target.value as Format })} className={SELECT}>
            {FORMATS.map((f) => (
              <option key={f.id} value={f.id}>
                {f.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-small">
          <span>Language</span>
          <select value={ep.language} onChange={(e) => patch.mutate({ language: e.target.value })} className={SELECT}>
            {(languages.length ? languages : [{ code: ep.language, name: ep.language }]).map((l) => (
              <option key={l.code} value={l.code}>
                {l.name}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-small">
          <span>Length (minutes)</span>
          <Input
            type="number"
            min={1}
            max={120}
            defaultValue={ep.target_minutes}
            onBlur={(e) => {
              const v = Math.min(120, Math.max(1, Number(e.target.value) || ep.target_minutes))
              if (v !== ep.target_minutes) patch.mutate({ target_minutes: v })
            }}
          />
        </label>
        {isArabicScriptLang(ep.language) && <AerabOnNote language={ep.language} className="sm:col-span-3" />}
      </fieldset>

      <div role="radiogroup" aria-label="Source" className="grid gap-2 sm:grid-cols-4">
        {MODES.map((m) => (
          <label
            key={m.id}
            className={cn(
              'flex cursor-pointer flex-col gap-0.5 rounded-[6px] border p-2.5 text-small has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-studio-accent',
              mode === m.id ? 'border-studio-accent bg-studio-accent-soft' : 'border-studio-border bg-studio-panel hover:bg-studio-panel-hover',
            )}
          >
            <span className="flex items-center gap-2 font-medium">
              <input type="radio" name={`${uid}-mode`} value={m.id} checked={mode === m.id} onChange={() => setMode(m.id)} className="accent-studio-accent" />
              {m.label}
            </span>
            <span className="text-[11px] text-studio-muted">{m.hint}</span>
          </label>
        ))}
      </div>

      <label className="flex flex-col gap-1 text-small">
        <span className="section-label">Topic</span>
        <Textarea value={topic} onChange={(e) => setTopic(e.target.value)} onBlur={saveTopic} rows={2} dir="auto" placeholder="What is this episode about?" />
      </label>

      {mode === 'paste' && (
        <label className="flex flex-col gap-1 text-small">
          <span className="section-label">Material</span>
          <Textarea value={text} onChange={(e) => setText(e.target.value)} onBlur={saveText} rows={10} maxLength={200000} dir="auto" placeholder="Paste an article, notes or a transcript" />
          <span className="text-[11px] text-studio-muted">{text.length.toLocaleString()} characters{text.length > 12000 ? ' · long: the writer reads a summary of it' : ''}</span>
        </label>
      )}

      {mode === 'upload' && (
        <div className="flex flex-col gap-2">
          <input ref={file} id={`${uid}-file`} type="file" accept={DOC_ACCEPT} multiple className="sr-only" onChange={(e) => upload(e.target.files)} />
          <Button type="button" className="self-start" loading={add.isPending} onClick={() => file.current?.click()}>
            <FileUp aria-hidden />
            Upload a document
          </Button>
          {ep.sources.docs.length > 0 && (
            <ul aria-label="Documents" className="divide-y divide-studio-border rounded-[6px] border border-studio-border bg-studio-panel">
              {ep.sources.docs.map((d) => (
                <li key={d.id} className="flex items-center gap-2 px-3 py-2 text-small">
                  <FileText aria-hidden className="size-4 shrink-0 text-studio-muted" />
                  <span className="min-w-0 flex-1 truncate">{d.name}</span>
                  <span className="text-[11px] text-studio-muted">
                    {d.type.toUpperCase()} · {d.chars.toLocaleString()} characters{d.pages ? ` · ${d.pages} pages` : ''}
                    {d.truncated ? ' · cut to fit' : ''}
                  </span>
                  <Button type="button" size="icon-sm" variant="ghost" aria-label={`Remove ${d.name}`} onClick={() => remove.mutate(d.id)}>
                    <Trash2 aria-hidden />
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {mode === 'script' && (
        <div className="flex flex-col gap-2">
          <label className="flex flex-col gap-1 text-small">
            <span className="section-label">Your script</span>
            <Textarea
              value={script}
              onChange={(e) => setScript(e.target.value)}
              rows={12}
              maxLength={200000}
              dir="auto"
              placeholder={'# Welcome\nAyesha: Welcome to the show.\nBilal: Glad to be here.'}
            />
          </label>
          {ep.sources.has_script && <p className="text-[11px] text-studio-warning">Using a script replaces the lines the episode has now.</p>}
          <Button type="button" variant="primary" className="self-start" disabled={!script.trim()} loading={paste.isPending} onClick={applyScript}>
            <ScrollText aria-hidden />
            Use this script
          </Button>
        </div>
      )}

      {error && (
        <p role="alert" className="text-small text-studio-danger">
          {error.message}
        </p>
      )}
      {mode !== 'script' && (
        <p className="text-small text-studio-muted">
          Next, pick the hosts.{' '}
          <button type="button" className="underline underline-offset-2 hover:text-studio-text" onClick={onNext}>
            Go to Hosts
          </button>
        </p>
      )}
    </>
  )
}
