import { ArrowRight } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { jobRunning, useContinueAiVideo } from '@/hooks/useVideoPodcast'
import type { VideoPodcast, VPStageKey } from '@/lib/types'
import { announce } from '@/stores/ui'

/** The step's main action while the pipeline sits on its stage: start it, or carry on after a review. */
export function StageAction({ vp, stage, label, hint }: { vp: VideoPodcast; stage: VPStageKey; label: string; hint: string }) {
  const go = useContinueAiVideo(vp.id)
  if (vp.stage !== stage || jobRunning(vp)) return null
  const st = vp.stages.find((s) => s.key === stage)?.status
  // a review has its own banner above the steps
  if (st === 'done' || st === 'skipped' || st === 'review' || vp.review) return null
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-[6px] border border-studio-accent/40 bg-studio-accent-soft p-3">
      <p className="mr-auto text-small">{st === 'failed' ? 'That stage failed. Try it again, or change something first.' : hint}</p>
      <Button type="button" variant="primary" loading={go.isPending} onClick={() => go.mutate(undefined, { onSuccess: () => announce('Working on it.') })}>
        {st === 'failed' ? 'Try again' : label}
        <ArrowRight aria-hidden />
      </Button>
      {go.isError && (
        <p role="alert" className="w-full text-small text-studio-danger">
          {go.error.message}
        </p>
      )}
    </div>
  )
}
