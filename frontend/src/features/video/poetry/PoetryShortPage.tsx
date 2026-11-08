import { useRef, useState } from 'react'
import { Link, useParams } from 'react-router'
import { ArrowLeft, ArrowRight, Check, Loader2 } from 'lucide-react'
import { CAPTION_STYLES } from '@/components/generate/CaptionStylePicker'
import { ErrorState } from '@/components/studio/states'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { StageTimeline } from '@/features/quick/StageTimeline'
import { useCharacterLooks } from '@/hooks/useCharacterLooks'
import { useVoiceChoices } from '@/hooks/useGallery'
import { useContinuePoetry, usePoetry } from '@/hooks/usePoetry'
import { POETRY_STEPS, blockingStep, bookendSummary, initialStep, jobBusy, lengthLabel, stageOf, stepStates, voiceSummary, type PoetryShort, type PoetryStageKey, type StepState } from '@/lib/poetry'
import type { QuickStage, QuickStageKey } from '@/lib/types'
import { cn } from '@/lib/utils'
import { announce } from '@/stores/ui'
import { PoemStep, RenderStep, ScenesStep, SongStep, TimingStep } from './PoetrySteps'

const NOUN: Record<PoetryStageKey, string> = { poem: 'poem', song: 'song', timing: 'line timing', scenes: 'scenes', render: 'video' }

const REVIEW_TEXT: Record<PoetryStageKey, string> = {
  poem: 'Read the poem. Change any line you like, then continue to the song.',
  song: 'Listen to the song. Sing it again if it isn’t right, then continue to the timing.',
  timing: 'Check that each line comes in when it is sung. Nudge or tap any that are off, then continue.',
  scenes: 'Look over the scenes. Make any of them again, then continue to the finished video.',
  render: '',
}

export function PoetryShortPage() {
  const { id = '' } = useParams()
  const q = usePoetry(id)
  if (q.isPending)
    return (
      <div className="flex flex-col gap-2 p-6">
        <Skeleton className="h-10" />
        <Skeleton className="h-64" />
      </div>
    )
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} className="m-6" />
  return <Workspace key={q.data.id} ps={q.data} />
}

function timeline(ps: PoetryShort): QuickStage[] {
  // StageTimeline speaks Quick Create's stages; a review reads as waiting with a note
  return ps.stages.map((s) => ({
    key: s.key as unknown as QuickStageKey,
    label: s.status === 'review' ? `${s.label}: your review` : s.label,
    status: s.status === 'review' ? 'pending' : s.status,
    started_at: s.started_at,
    finished_at: s.finished_at,
    detail: s.detail,
  }))
}

function Workspace({ ps }: { ps: PoetryShort }) {
  const [step, setStep] = useState(() => initialStep(ps))
  const states = stepStates(ps)
  const current = POETRY_STEPS[step - 1]
  const go = (n: number) => setStep(Math.min(POETRY_STEPS.length, Math.max(1, n)))
  return (
    <main data-f6-region tabIndex={-1} aria-labelledby="ps-title" className="flex h-full min-h-0 flex-col focus-visible:outline-none">
      <header className="flex flex-wrap items-center gap-2 border-b border-studio-border bg-studio-panel px-3 py-2">
        <Button asChild size="sm" variant="ghost">
          <Link to="/video/poetry">
            <ArrowLeft aria-hidden />
            All Shorts
          </Link>
        </Button>
        <h1 id="ps-title" className="font-display text-[18px] font-semibold" dir="auto" lang={ps.language}>
          {ps.title || 'Poetry Short'}
        </h1>
        <Summary ps={ps} />
      </header>
      <Stepper current={step} states={states} onPick={go} />
      <section role="tabpanel" id={`ps-panel-${current.id}`} aria-labelledby={`ps-tab-${current.id}`} tabIndex={-1} className="min-h-0 flex-1 overflow-y-auto focus-visible:outline-none">
        <div className="mx-auto flex w-full max-w-5xl flex-col gap-4 px-4 py-5 md:px-8">
          <Progress ps={ps} />
          <ReviewBanner ps={ps} onGo={go} />
          <StepGate ps={ps} stepIndex={step - 1} onGo={go} />
          {current.id === 'poem' && <PoemStep ps={ps} />}
          {current.id === 'song' && <SongStep ps={ps} />}
          {current.id === 'timing' && <TimingStep ps={ps} />}
          {current.id === 'scenes' && <ScenesStep ps={ps} />}
          {current.id === 'render' && <RenderStep ps={ps} />}
        </div>
      </section>
      <footer className="flex items-center gap-2 border-t border-studio-border bg-studio-panel px-3 py-2">
        <Button type="button" size="sm" variant="ghost" disabled={step === 1} onClick={() => go(step - 1)}>
          <ArrowLeft aria-hidden />
          Back
        </Button>
        <span className="text-[12px] text-studio-muted">
          Step {step} of {POETRY_STEPS.length}: {current.label}
        </span>
        {step < POETRY_STEPS.length && <NextButton ps={ps} step={step} onGo={go} />}
      </footer>
    </main>
  )
}

/** The choices the Short was made with, in one line under the title on narrow screens. */
function Summary({ ps }: { ps: PoetryShort }) {
  const st = ps.settings
  const voices = useVoiceChoices()
  const voiceName = st.sing_voice_id ? voices.find((v) => v.id === st.sing_voice_id)?.name : undefined
  const caption = CAPTION_STYLES.find((c) => c.value === (st.caption_style ?? 'classic'))?.label
  const bits: React.ReactNode[] = [st.aspect, lengthLabel(st.length_s), voiceSummary(st, voiceName)]
  if (st.character_id && st.look_id) bits.push(<LookName key="look" characterId={st.character_id} lookId={st.look_id} />)
  if (caption) bits.push(`${caption} captions`)
  if (bookendSummary(st)) bits.push(bookendSummary(st))
  return (
    <p className="ml-auto flex flex-wrap gap-x-1 text-[12px] text-studio-muted">
      {bits.map((b, i) => (
        <span key={i}>
          {i > 0 && <span aria-hidden>· </span>}
          {b}
        </span>
      ))}
    </p>
  )
}

function LookName({ characterId, lookId }: { characterId: string; lookId: string }) {
  const name = useCharacterLooks(characterId).data?.looks.find((l) => l.id === lookId)?.name
  return <>Look: {name ?? '…'}</>
}

function Progress({ ps }: { ps: PoetryShort }) {
  const failed = ps.stages.find((s) => s.status === 'failed')
  return (
    <div className="flex flex-col gap-2">
      <StageTimeline stages={timeline(ps)} />
      <p role="status" aria-live="polite" className="text-small text-studio-muted">
        {jobBusy(ps) ? (
          <>
            <Loader2 aria-hidden className="mr-1 inline size-3.5 animate-spin" />
            {ps.job?.message || 'Working…'}
          </>
        ) : null}
      </p>
      {!jobBusy(ps) && failed && <RetryFailed ps={ps} what={failed.label} detail={failed.detail} />}
    </div>
  )
}

function RetryFailed({ ps, what, detail }: { ps: PoetryShort; what: string; detail: string | null }) {
  const go = useContinuePoetry(ps.id)
  return (
    <div role="alert" className="flex flex-wrap items-center gap-2 rounded-[6px] border border-studio-danger/40 bg-studio-danger/5 p-2 text-small">
      <span className="mr-auto">
        {what} stopped{detail ? `: ${detail}` : '.'}
      </span>
      <Button type="button" size="sm" variant="secondary" loading={go.isPending} onClick={() => go.mutate()}>
        Try again
      </Button>
    </div>
  )
}

// a review step's Next approves and moves the pipeline on, not just the view
function NextButton({ ps, step, onGo }: { ps: PoetryShort; step: number; onGo: (n: number) => void }) {
  const go = useContinuePoetry(ps.id)
  const here = POETRY_STEPS[step - 1]
  const next = POETRY_STEPS[step]
  const approve = stageOf(ps, here.id)?.status === 'review' && !jobBusy(ps)
  return (
    <Button
      type="button"
      size="sm"
      variant="primary"
      className="ml-auto"
      loading={go.isPending}
      onClick={() => {
        if (!approve) return onGo(step + 1)
        go.mutate(undefined, {
          onSuccess: () => {
            announce(`Approved. Working on the ${NOUN[next.id]} now.`)
            onGo(step + 1)
          },
        })
      }}
    >
      {approve ? `Approve and continue to ${next.label}` : `Next: ${next.label}`}
      <ArrowRight aria-hidden />
    </Button>
  )
}

function ReviewBanner({ ps, onGo }: { ps: PoetryShort; onGo: (n: number) => void }) {
  const go = useContinuePoetry(ps.id)
  if (!ps.review || jobBusy(ps)) return null
  const i = POETRY_STEPS.findIndex((s) => s.id === ps.review)
  return (
    <section aria-label="Waiting for your review" className="flex flex-wrap items-center gap-3 rounded-[6px] border border-studio-accent bg-studio-accent-soft p-3 shadow-card">
      <p className="mr-auto text-small">{REVIEW_TEXT[ps.review]}</p>
      <Button type="button" size="sm" variant="ghost" onClick={() => onGo(i + 1)}>
        See the {NOUN[ps.review]}
      </Button>
      <Button type="button" variant="primary" loading={go.isPending} onClick={() => go.mutate(undefined, { onSuccess: () => announce('Continuing.') })}>
        Continue
        <ArrowRight aria-hidden />
      </Button>
      {go.isError && (
        <p role="alert" className="w-full text-small text-studio-danger">
          {go.error.message}
        </p>
      )}
    </section>
  )
}

/** Says why a step is still empty and offers the one action that moves it along. */
function StepGate({ ps, stepIndex, onGo }: { ps: PoetryShort; stepIndex: number; onGo: (n: number) => void }) {
  const go = useContinuePoetry(ps.id)
  const block = blockingStep(ps, stepIndex)
  if (!block) return null
  const here = NOUN[POETRY_STEPS[stepIndex].id]
  const what = NOUN[block.step.id]
  const running = block.status === 'running' || jobBusy(ps)
  return (
    <section aria-label="What this step is waiting for" className="flex flex-wrap items-center gap-3 rounded-[6px] border border-studio-border-strong bg-studio-raised p-3">
      <p className="mr-auto text-small">
        {running
          ? `Working on the ${what} first; the ${here} follow${here.endsWith('s') ? '' : 's'} on its own.`
          : block.status === 'review'
            ? `The ${what} is waiting for your OK. Approve it and the ${here} comes next.`
            : block.status === 'failed'
              ? `The ${what} step stopped, so the ${here} can't start yet.`
              : `The ${here} comes after the ${what}. Start there.`}
      </p>
      {!running && block.status === 'review' && (
        <Button type="button" variant="primary" loading={go.isPending} onClick={() => go.mutate(undefined, { onSuccess: () => announce(`Approved. Working on the ${here} now.`) })}>
          Approve the {what} and continue
          <ArrowRight aria-hidden />
        </Button>
      )}
      {!running && block.status !== 'review' && (
        <Button type="button" variant="secondary" onClick={() => onGo(block.index + 1)}>
          Go to {block.step.label}
        </Button>
      )}
    </section>
  )
}

const STATE_TEXT: Record<StepState, string> = { done: 'done', todo: 'to do', busy: 'working', review: 'needs your OK', failed: 'stopped' }

function Stepper({ current, states, onPick }: { current: number; states: Record<PoetryStageKey, StepState>; onPick: (n: number) => void }) {
  const refs = useRef<(HTMLButtonElement | null)[]>([])
  const n = POETRY_STEPS.length
  const onKey = (e: React.KeyboardEvent, i: number) => {
    const to = e.key === 'ArrowRight' ? i + 1 : e.key === 'ArrowLeft' ? i - 1 : e.key === 'Home' ? 0 : e.key === 'End' ? n - 1 : null
    if (to === null) return
    e.preventDefault()
    const k = (to + n) % n
    onPick(k + 1)
    refs.current[k]?.focus()
  }
  return (
    <nav aria-label="Poetry Short steps" className="border-b border-studio-border bg-studio-raised px-2 py-1.5">
      <div role="tablist" aria-label="Steps" className="flex gap-1 overflow-x-auto">
        {POETRY_STEPS.map((s, i) => {
          const on = i + 1 === current
          const st = states[s.id]
          return (
            <button
              key={s.id}
              ref={(el) => {
                refs.current[i] = el
              }}
              type="button"
              role="tab"
              id={`ps-tab-${s.id}`}
              aria-selected={on}
              aria-controls={`ps-panel-${s.id}`}
              tabIndex={on ? 0 : -1}
              onClick={() => onPick(i + 1)}
              onKeyDown={(e) => onKey(e, i)}
              className={cn(
                'flex h-9 shrink-0 items-center gap-2 rounded-[6px] border px-3 text-small focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-studio-accent',
                on ? 'border-studio-accent bg-studio-accent-soft font-medium text-studio-text' : 'border-transparent text-studio-muted hover:bg-studio-panel-hover hover:text-studio-text',
              )}
            >
              <span
                aria-hidden
                className={cn(
                  'flex size-5 items-center justify-center rounded-full border text-[11px] [&_svg]:size-3',
                  st === 'done' ? 'border-studio-success bg-studio-success text-studio-accent-fg' : st === 'review' ? 'border-studio-accent bg-studio-accent text-studio-accent-fg' : st === 'failed' ? 'border-studio-danger text-studio-danger' : 'border-studio-border-strong',
                )}
              >
                {st === 'done' ? <Check /> : st === 'busy' ? <Loader2 className="animate-spin" /> : i + 1}
              </span>
              {s.label}
              {st === 'review' && <span aria-hidden className="text-[11px] text-studio-accent-hover">needs your OK</span>}
              <span className="sr-only">, {STATE_TEXT[st]}</span>
            </button>
          )
        })}
      </div>
    </nav>
  )
}
