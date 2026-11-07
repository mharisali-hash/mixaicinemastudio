import { useState } from 'react'
import { useLocation, useSearchParams } from 'react-router'
import { Mic } from 'lucide-react'
import { EmptyState } from '@/components/studio/states'
import { GeneratorPage, ResultsHeading } from '@/components/generate/GeneratorPage'
import { SessionResults } from '@/components/generate/SessionResults'
import { useRecordRun } from '@/components/generate/useRecordRun'
import { flatItems, useMediaList } from '@/hooks/useMedia'
import { useVoiceChoices } from '@/hooks/useGallery'
import { useSpeak, useVoiceEngines } from '@/hooks/useVoices'
import { DEFAULT_SPEAK_FORM, isSpeech, speakFormFrom, type SpeakForm } from '@/lib/voice'
import type { MediaItem, SpeakResponse } from '@/lib/types'
import { announce } from '@/stores/ui'
import { CompareBoard, type ComparePair } from './CompareBoard'
import { SpeakDock, type SpeakRun } from './SpeakDock'

const PAGE = 'audio-speak'

export function SpeakPage() {
  // arriving again with a new ?voice= starts a fresh dock
  return <Speak key={useLocation().key} />
}

function Speak() {
  const [params] = useSearchParams()
  const asked = params.get('voice')
  const [form, setForm] = useState<SpeakForm>(() => ({ ...DEFAULT_SPEAK_FORM, voiceId: asked, text: params.get('text') ?? '' }))
  // gallery voices too: ?voice=gallery:<id> arrives from the Gallery's "Use in Speak"
  const voices = useVoiceChoices()
  const { engines } = useVoiceEngines()
  // ?voice= arrives before the library does; take its language once it's known
  const [adopted, setAdopted] = useState<string | null>(null)
  const linked = asked ? voices.find((v) => v.id === asked) : undefined
  if (linked && adopted !== linked.id) {
    setAdopted(linked.id)
    setForm((f) => (f.voiceId === linked.id ? { ...f, language: linked.language } : f))
  }

  const results = useMediaList({ kind: 'audio', origin: 'generated' })
  const speak = useSpeak()
  const record = useRecordRun<SpeakRun>(PAGE)
  const [pair, setPair] = useState<ComparePair | null>(null)
  const listed = flatItems(results.data)
  // the response is a snapshot; the list copy follows the job to ready
  const live = (m: MediaItem) => listed.find((x) => x.id === m.id) ?? m

  // a run from the dock empties the text once every take is queued; Retry leaves the dock alone
  const run = async (r: SpeakRun, fromDock = false) => {
    let res: SpeakResponse[]
    try {
      res = await Promise.all(r.bodies.map((b) => speak.mutateAsync(b)))
    } catch {
      // the dock shows speak.error
      return
    }
    if (fromDock) setForm((f) => ({ ...f, text: '' }))
    const items = res.flatMap((x) => x.items ?? [])
    record({ prompt: r.form.text, summary: r.summary, settings: r, items, jobs: res.flatMap((x) => x.jobs ?? []), label: items.length > 1 ? 'Your takes' : 'Your take' })
    const [a, b] = res.map((x) => x.items?.[0])
    if (r.bodies.length === 2 && a && b) {
      setPair({ a: { item: a, label: r.labels[0] }, b: { item: b, label: r.labels[1] }, kept: null })
      announce('Speaking A and B. Both appear side by side when ready.')
    } else {
      announce(`Speaking ${items.length > 1 ? `${items.length} takes. They appear` : 'your line. It appears'} below when ready.`)
    }
  }

  const refill = (next: SpeakForm) => {
    setForm(next)
    announce('Settings copied into the dock.')
  }

  return (
    <GeneratorPage label="Speak" targets={[]}>
      <SpeakDock form={form} setForm={setForm} voices={voices} engines={engines} onRun={(r) => run(r, true)} pending={speak.isPending} error={speak.error} />
      {pair && (
        <CompareBoard
          pair={{ ...pair, a: { ...pair.a, item: live(pair.a.item) }, b: { ...pair.b, item: live(pair.b.item) } }}
          onKeep={(side) => setPair((p) => (p ? { ...p, kept: side } : p))}
          onClose={() => setPair(null)}
        />
      )}
      <ResultsHeading>Your takes</ResultsHeading>
      <SessionResults<SpeakRun>
        page={PAGE}
        label="Results"
        items={listed.filter((m) => isSpeech(m.params))}
        loading={results.isPending}
        error={results.isError ? results.error : undefined}
        onRetryLoad={() => results.refetch()}
        hasMore={results.hasNextPage}
        loadingMore={results.isFetchingNextPage}
        onLoadMore={() => results.fetchNextPage()}
        onRetry={(req) => run(req.settings)}
        onReuse={(row) => refill(row.request?.settings.form ?? (row.items[0] ? speakFormFrom(row.items[0].params) : { ...DEFAULT_SPEAK_FORM, text: row.prompt }))}
        reuseItem={(m) => refill(speakFormFrom(m.params))}
        empty={
          <EmptyState icon={<Mic />} title="Nothing spoken yet">
            Write a line above and pick a voice. Each take appears here as it finishes.
          </EmptyState>
        }
      />
    </GeneratorPage>
  )
}
