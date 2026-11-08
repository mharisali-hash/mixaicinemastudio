import { useId } from 'react'
import { Clapperboard } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { usePatchEpisode } from '@/hooks/usePodcast'
import type { Episode } from '@/lib/podcast'
import { announce } from '@/stores/ui'

/** An audio episode's way on to video: it becomes a video podcast and its stepper runs on into the video steps. */
export function AiVideoEntry({ ep, onVideo }: { ep: Episode; onVideo: () => void }) {
  const uid = useId()
  const patch = usePatchEpisode(ep.id)
  const why = !ep.hosts.length ? 'Add the hosts first (step 2).' : null

  return (
    <section aria-labelledby={`${uid}-ai`} className="flex flex-col gap-3 rounded-[6px] border border-studio-border-strong bg-studio-panel p-3 shadow-card">
      <h3 id={`${uid}-ai`} className="font-display text-[15px] font-semibold">
        Video podcast
      </h3>
      <p className="text-[11px] text-studio-muted">
        Your hosts on camera in a generated set, lip-synced to the mix and cut between angles. The video steps are added after this one; the audio stays as it is.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant="primary"
          aria-disabled={!!why || undefined}
          aria-describedby={why ? `${uid}-why` : undefined}
          loading={patch.isPending}
          onClick={() => {
            if (why) return
            patch.mutate(
              { medium: 'video' },
              {
                onSuccess: () => {
                  announce('This is now a video podcast. On to the cast.')
                  onVideo()
                },
              },
            )
          }}
        >
          <Clapperboard aria-hidden />
          Turn into a video podcast
        </Button>
        {why && (
          <span id={`${uid}-why`} className="text-small text-studio-muted">
            {why}
          </span>
        )}
      </div>
      {patch.isError && (
        <p role="alert" className="text-small text-studio-danger">
          {patch.error.message}
        </p>
      )}
    </section>
  )
}
