import { ArrowRight, Clapperboard, Loader2 } from 'lucide-react'
import { EmptyState, ErrorState } from '@/components/studio/states'
import { Button } from '@/components/ui/button'
import { StageTimeline } from '@/features/quick/StageTimeline'
import { jobRunning, useContinueAiVideo, useCreateAiVideo } from '@/hooks/useVideoPodcast'
import { busy, type Episode } from '@/lib/podcast'
import { formatEstimate } from '@/lib/shots'
import type { VideoPodcast } from '@/lib/types'
import { announce } from '@/stores/ui'
import { CamerasPanel } from './CamerasPanel'
import { HostsPanel } from './HostsPanel'
import { LookPanel } from './LookPanel'
import { RenderPanel } from './RenderPanel'
import { ResultPanel } from './ResultPanel'
import { SetPanel } from './SetPanel'
import { VP_STEPS, blockingStep, timelineStages } from './vp'

// The AI video's six steps as they run on in a video episode's stepper. `step` and `onGo` count from 1 within
// the video steps; the episode page adds its own six in front.

export function VideoStepBody({ vp, step, onGo, refreshError }: { vp: VideoPodcast; step: number; onGo: (n: number) => void; refreshError?: React.ReactNode }) {
  const current = VP_STEPS[step - 1]
  return (
    <>
      <Progress vp={vp} />
      <ReviewBanner vp={vp} onGo={onGo} />
      <StepGate vp={vp} stepIndex={step - 1} onGo={onGo} />
      {refreshError}
      {current.id === 'hosts' && <HostsPanel vp={vp} />}
      {current.id === 'set' && <SetPanel vp={vp} />}
      {current.id === 'look' && <LookPanel vp={vp} />}
      {current.id === 'cameras' && <CamerasPanel vp={vp} />}
      {current.id === 'render' && <RenderPanel vp={vp} />}
      {current.id === 'result' && <ResultPanel vp={vp} onRender={() => onGo(5)} />}
    </>
  )
}

/** A video episode whose AI video record is missing (made before Podcast Studio, then deleted): make it here. */
export function VideoStart({ ep }: { ep: Episode }) {
  const create = useCreateAiVideo(ep.id)
  const why = !ep.hosts.length ? 'Add the hosts first.' : null
  return (
    <>
      <EmptyState
        icon={<Clapperboard />}
        title={`AI video of ${ep.title}`}
        action={
          <Button type="button" variant="primary" aria-disabled={!!why || undefined} aria-describedby="vp-start-why" loading={create.isPending} onClick={() => !why && create.mutate({})}>
            Start the video
          </Button>
        }
      >
        The hosts on camera in a set, lip-synced to the episode mix and cut between angles.
      </EmptyState>
      <p id="vp-start-why" aria-live="polite" className="text-center text-small text-studio-muted">
        {why}
      </p>
      {create.isError && <ErrorState error={create.error} className="mx-auto max-w-md" />}
    </>
  )
}

function Progress({ vp }: { vp: VideoPodcast }) {
  const job = vp.job
  const running = jobRunning(vp)
  return (
    <div className="flex flex-col gap-2">
      <StageTimeline stages={timelineStages(vp)} />
      <p role="status" aria-live="polite" className="text-small text-studio-muted">
        {running ? (
          <>
            <Loader2 aria-hidden className="mr-1 inline size-3.5 animate-spin" />
            {job?.message || 'Working…'}
            {vp.eta_s ? ` · about ${formatEstimate(vp.eta_s)} left` : ''}
          </>
        ) : null}
      </p>
      {!busy(job) && job?.status === 'failed' && (
        <p role="alert" className="text-small text-studio-danger">
          The last run failed{job.error ? `: ${job.error}` : '.'}
        </p>
      )}
    </div>
  )
}

// a review step's Next approves and moves the pipeline on, not just the view
export function VideoNextButton({ vp, step, onGo }: { vp: VideoPodcast; step: number; onGo: (n: number) => void }) {
  const go = useContinueAiVideo(vp.id)
  const status = vp.stages.find((s) => s.key === VP_STEPS[step - 1].stage)?.status
  const approve = status === 'review' && !jobRunning(vp)
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
            announce(`Approved. Working on the ${NOUN[VP_STEPS[step].stage].name} now.`)
            onGo(step + 1)
          },
        })
      }}
    >
      {approve ? `Approve and continue to ${VP_STEPS[step].label}` : `Next: ${VP_STEPS[step].label}`}
      <ArrowRight aria-hidden />
    </Button>
  )
}

const NOUN: Record<string, { name: string; plural: boolean }> = {
  cast: { name: 'host portraits', plural: true },
  set: { name: 'set', plural: false },
  keyframes: { name: 'camera angles', plural: true },
  plan: { name: 'cameras and the cut', plural: true },
  render: { name: 'render', plural: false },
  assemble: { name: 'final cut', plural: false },
}

/** Says why a step is still empty and offers the one action that moves it along. */
function StepGate({ vp, stepIndex, onGo }: { vp: VideoPodcast; stepIndex: number; onGo: (n: number) => void }) {
  const go = useContinueAiVideo(vp.id)
  const block = blockingStep(vp, stepIndex)
  if (!block) return null
  const here = NOUN[VP_STEPS[stepIndex].stage].name
  const { name: what, plural } = NOUN[block.step.stage]
  const be = plural ? 'are' : 'is'
  const running = block.status === 'running' || jobRunning(vp)
  return (
    <section aria-label="What this step is waiting for" className="flex flex-wrap items-center gap-3 rounded-[6px] border border-studio-border-strong bg-studio-raised p-3">
      <p className="mr-auto text-small">
        {running
          ? `Working on the ${what} first; the ${here} ${NOUN[VP_STEPS[stepIndex].stage].plural ? 'follow on their own' : 'follows on its own'}.`
          : block.status === 'review'
            ? `The ${what} ${be} waiting for your OK. Approve ${plural ? 'them' : 'it'} and the ${here} ${NOUN[VP_STEPS[stepIndex].stage].plural ? 'are' : 'is'} made next.`
            : block.status === 'failed'
              ? `The ${what} step failed, so the ${here} can't start yet.`
              : `The ${here} ${NOUN[VP_STEPS[stepIndex].stage].plural ? 'come' : 'comes'} after the ${what}. Start there.`}
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
      {go.isError && (
        <p role="alert" className="w-full text-small text-studio-danger">
          {go.error.message}
        </p>
      )}
    </section>
  )
}

function ReviewBanner({ vp, onGo }: { vp: VideoPodcast; onGo: (n: number) => void }) {
  const go = useContinueAiVideo(vp.id)
  if (!vp.review || jobRunning(vp)) return null
  const hosts = vp.review === 'hosts'
  return (
    <section aria-label="Waiting for your review" className="flex flex-wrap items-center gap-3 rounded-[6px] border border-studio-accent bg-studio-accent-soft p-3 shadow-card">
      <p className="mr-auto text-small">
        {hosts
          ? 'Have a look at the host portraits. Regenerate any you don’t like, then continue to the set.'
          : 'Check the camera angles. Regenerate any that look wrong, then continue to the cameras and the cut.'}{' '}
        <span className="text-studio-muted">Anything not approved yet is approved as it is.</span>
      </p>
      <Button type="button" size="sm" variant="ghost" onClick={() => onGo(hosts ? 1 : 3)}>
        {hosts ? 'See the hosts' : 'See the angles'}
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
