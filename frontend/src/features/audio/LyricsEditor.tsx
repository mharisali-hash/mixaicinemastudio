import { useId, useRef, useState } from 'react'
import { Music2, Sparkles } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/alert-dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Textarea } from '@/components/ui/textarea'
import { SwitchRow } from '@/components/studio/switch-row'
import { AerabButton } from '@/components/text/AerabButton'
import { useWriteLyrics } from '@/hooks/useModels'
import { aerabNote, aerabTerm, hasArabicScript, isArabicScriptLang } from '@/lib/aerab'
import { ApiError } from '@/lib/api'
import { insertSection, LYRIC_SECTIONS, LYRICS_LANGUAGES, LYRICS_MAX, PART_TAGS, type LyricSection } from '@/lib/audio'
import { cn } from '@/lib/utils'
import { announce } from '@/stores/ui'

interface Props {
  lyrics: string
  onLyrics: (v: string) => void
  instrumental: boolean
  onInstrumental: (on: boolean) => void
  // lead child + group: show the who-sings markers
  parts?: boolean
  // the writer's language, so the song can be sung in it
  onLanguage?: (lyricsLanguage: string) => void
  // the song's language, so Add aerab marks Arabic with Arabic rules
  language?: string
  // Arabic-script lyrics: add aerab to unmarked lines before singing (server side)
  aerab?: boolean
  onAerab?: (on: boolean) => void
}

const SELECT = 'h-8 w-full rounded-[6px] border border-studio-border-strong bg-studio-raised px-2 text-body'

function writerError(e: unknown) {
  if (e instanceof ApiError && e.status === 503) return "The lyrics writer is offline: the studio's language model isn't answering. Write the lyrics yourself, or try again in a minute."
  return e instanceof Error ? e.message : "Couldn't write lyrics just now."
}

/** Lyrics with section buttons, a character count, an Instrumental switch and the ✨ writer. */
export function LyricsEditor({ lyrics, onLyrics, instrumental, onInstrumental, onLanguage, language, aerab = true, onAerab, parts = false }: Props) {
  const uid = useId()
  const field = useRef<HTMLTextAreaElement>(null)
  const [pending, setPending] = useState<string | null>(null)
  const arabicScript = !instrumental && hasArabicScript(lyrics)

  const add = (s: LyricSection | string) => {
    const el = field.current
    const { text, cursor } = insertSection(lyrics, el?.selectionStart ?? lyrics.length, s)
    if (text.length > LYRICS_MAX) return announce('The lyrics are at the limit.')
    onLyrics(text)
    // put the caret under the new tag once React has the new value in
    requestAnimationFrame(() => {
      el?.focus()
      el?.setSelectionRange(cursor, cursor)
    })
  }

  const take = (text: string, note?: string) => {
    onLyrics(text.slice(0, LYRICS_MAX))
    announce(note ? `Lyrics written into the editor. ${note}` : 'Lyrics written into the editor.')
  }

  const near = lyrics.length > LYRICS_MAX * 0.9

  return (
    <div className="flex flex-col gap-2 rounded-[6px] border border-studio-border bg-studio-raised/60 p-2.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <Label htmlFor={`${uid}-lyrics`} className="mr-1 flex items-center gap-1">
          <Music2 aria-hidden className="size-3.5 text-studio-accent" />
          Lyrics
        </Label>
        <div role="toolbar" aria-label="Insert a section" className="flex flex-wrap gap-1">
          {LYRIC_SECTIONS.map((s) => (
            <Button key={s} type="button" size="sm" variant="ghost" disabled={instrumental} onClick={() => add(s)} aria-label={`Insert [${s}]`} className="h-6 px-1.5 font-mono text-[12px]">
              [{s}]
            </Button>
          ))}
        </div>
        {parts && (
          <div role="toolbar" aria-label="Who sings" className="flex flex-wrap gap-1">
            {PART_TAGS.map((t) => (
              <Button key={t} type="button" size="sm" variant="ghost" disabled={instrumental} onClick={() => add(t)} aria-label={`Insert ${t}`} className="h-6 px-1.5 font-mono text-[12px] text-studio-accent">
                {t}
              </Button>
            ))}
          </div>
        )}
        <div className="ml-auto flex items-center gap-1.5">
          {!instrumental && <AerabButton text={lyrics} onText={onLyrics} language={language} max={LYRICS_MAX} showNote={false} />}
          <WriteLyrics
            disabled={instrumental}
            onWritten={(text, lang, note) => {
              onLanguage?.(lang)
              if (lyrics.trim()) setPending(text)
              else take(text, note)
            }}
          />
        </div>
      </div>
      <Textarea
        ref={field}
        id={`${uid}-lyrics`}
        rows={5}
        dir="auto"
        value={instrumental ? '' : lyrics}
        disabled={instrumental}
        maxLength={LYRICS_MAX}
        onChange={(e) => onLyrics(e.target.value)}
        placeholder={instrumental ? 'Instrumental: no lyrics are sent.' : '[verse]\nFirst lines here…\n\n[chorus]\nThe line everyone sings back'}
        aria-describedby={`${uid}-count`}
        className="min-h-24 resize-y font-mono text-[13px] leading-5"
      />
      <div className="flex flex-wrap items-start justify-between gap-2">
        <SwitchRow
          id={`${uid}-inst`}
          checked={instrumental}
          onChange={onInstrumental}
          title="Instrumental"
          hint="No vocals. Your lyrics stay here for later but aren't sent."
          className="max-w-sm py-1.5"
        />
        {arabicScript && onAerab && (
          <SwitchRow
            id={`${uid}-aerab`}
            checked={aerab}
            onChange={onAerab}
            title="Aerab before singing"
            hint="Lines without their vowel marks (aerab / tashkeel) get them just before the song is made, so vowels aren't guessed."
            className="max-w-sm py-1.5"
          />
        )}
        <p id={`${uid}-count`} className={cn('font-mono text-small', near ? 'text-studio-warning' : 'text-studio-muted')}>
          {lyrics.length.toLocaleString('en')} / {LYRICS_MAX.toLocaleString('en')}
          {' '}<span className="sr-only">characters</span>
        </p>
      </div>
      <ConfirmDialog
        open={pending !== null}
        onOpenChange={(o) => !o && setPending(null)}
        title="Replace your lyrics?"
        description="The new lyrics take the place of what's in the editor now."
        confirmLabel="Replace"
        onConfirm={() => {
          if (pending !== null) take(pending)
          setPending(null)
        }}
      />
    </div>
  )
}

function WriteLyrics({ disabled, onWritten }: { disabled?: boolean; onWritten: (lyrics: string, language: string, note?: string) => void }) {
  const uid = useId()
  const [open, setOpen] = useState(false)
  const [topic, setTopic] = useState('')
  const [language, setLanguage] = useState('en')
  const [mood, setMood] = useState('')
  const [withAerab, setWithAerab] = useState(true)
  const write = useWriteLyrics()
  const ready = topic.trim().length > 0

  const go = () => {
    if (!ready || write.isPending) return
    write.mutate(
      { topic: topic.trim().slice(0, 500), language, ...(mood.trim() ? { mood: mood.trim() } : {}), ...(isArabicScriptLang(language) ? { aerab: withAerab } : {}) },
      {
        onSuccess: (res) => {
          setOpen(false)
          const note = isArabicScriptLang(language) && withAerab ? aerabNote(res.marked ?? 0, res.kept ?? 0, res.low_coverage ?? 0) : undefined
          onWritten(res.lyrics ?? '', language, note)
        },
      },
    )
  }

  return (
    <Popover
      open={open}
      onOpenChange={(o) => {
        setOpen(o)
        if (o) write.reset()
      }}
    >
      <PopoverTrigger asChild>
        <Button type="button" size="sm" variant="secondary" disabled={disabled}>
          <Sparkles aria-hidden />
          Write lyrics
        </Button>
      </PopoverTrigger>
      <PopoverContent aria-label="Write lyrics" align="end" className="flex w-[min(360px,calc(100vw-24px))] flex-col gap-3">
        <div>
          <Label htmlFor={`${uid}-topic`} className="mb-1.5">
            What is the song about?
          </Label>
          <Input
            id={`${uid}-topic`}
            value={topic}
            maxLength={500}
            dir="auto"
            onChange={(e) => setTopic(e.target.value)}
            onKeyDown={(e) => {
              if (e.key !== 'Enter') return
              // stays inside the popover; the dock's Generate is a different decision
              e.preventDefault()
              e.stopPropagation()
              go()
            }}
            placeholder="e.g. a mother waiting at the station for her son"
          />
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <Label htmlFor={`${uid}-lang`} className="mb-1.5">
              Language
            </Label>
            <select id={`${uid}-lang`} value={language} onChange={(e) => setLanguage(e.target.value)} className={SELECT}>
              {LYRICS_LANGUAGES.map((l) => (
                <option key={l.value} value={l.value}>
                  {l.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <Label htmlFor={`${uid}-mood`} className="mb-1.5">
              Mood
            </Label>
            <Input id={`${uid}-mood`} value={mood} onChange={(e) => setMood(e.target.value)} placeholder="Hopeful" />
          </div>
        </div>
        {isArabicScriptLang(language) && (
          <label className="flex items-start gap-2 text-small">
            <input type="checkbox" checked={withAerab} onChange={(e) => setWithAerab(e.target.checked)} className="mt-0.5" />
            <span>
              {aerabTerm(language) === 'tashkeel' ? (
                <>
                  With tashkeel <span lang="ar">(فَتْحَة، كَسْرَة، ضَمَّة)</span>
                </>
              ) : (
                <>
                  With aerab <span lang="ur">(زَبَر، زیر، پیش)</span>
                </>
              )}
              <span className="block text-studio-muted">Clearer pronunciation when it's sung. Takes a few seconds longer.</span>
            </span>
          </label>
        )}
        {write.error && (
          <p role="alert" className="text-small text-studio-danger">
            {writerError(write.error)}
          </p>
        )}
        <div className="flex items-center justify-end gap-2">
          <p className="mr-auto text-small text-studio-muted">Verse, chorus and bridge, ready to edit.</p>
          <Button type="button" size="sm" variant="primary" disabled={!ready} loading={write.isPending} onClick={go}>
            <Sparkles aria-hidden />
            Write
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  )
}
