import { jobRunning, useApproveAiVideoGeneration, useRegenerateAiVideo } from '@/hooks/useVideoPodcast'
import type { VideoPodcast, VPAngle } from '@/lib/types'
import { announce } from '@/stores/ui'
import { GenActions, GenFrame } from './GenFrame'
import { StageAction } from './StageAction'
import { aspectClass } from './vp'

export function LookPanel({ vp }: { vp: VideoPodcast }) {
  const host = (a: VPAngle) => vp.hosts.find((h) => h.host_id === a.host_id)?.name
  return (
    <>
      <div>
        <h2 className="font-display text-title font-semibold">Look</h2>
        <p className="text-small text-studio-muted">The camera angles every shot is cut from: a close-up, a 3/4 and a listening shot per host, then a wide and a two-shot.</p>
      </div>
      <StageAction vp={vp} stage="keyframes" label="Make the camera angles" hint="The angles are made from the approved portraits and the set." />
      {vp.angles.length ? (
        <ul aria-label="Camera angles" className="grid grid-cols-2 gap-3 md:grid-cols-3">
          {vp.angles.map((a) => (
            <AngleCard key={a.key} vp={vp} angle={a} hostName={host(a)} />
          ))}
        </ul>
      ) : (
        <p className="text-small text-studio-muted">The angles appear here once the hosts and the set are ready.</p>
      )}
    </>
  )
}

function AngleCard({ vp, angle, hostName }: { vp: VideoPodcast; angle: VPAngle; hostName?: string }) {
  const regen = useRegenerateAiVideo(vp.id)
  const approve = useApproveAiVideoGeneration(vp.id)
  const name = hostName ? `${angle.label}, ${hostName}` : angle.label
  return (
    <li className="flex flex-col gap-2 rounded-[6px] border border-studio-border-strong bg-studio-panel p-2 shadow-card">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="truncate text-body font-medium">{angle.label}</h3>
        {hostName && (
          <span className="truncate text-[11px] text-studio-muted" dir="auto">
            {hostName}
          </span>
        )}
      </div>
      <GenFrame gen={angle.frame} alt={name} aspect={aspectClass(vp.settings.aspect)} />
      <GenActions
        gen={angle.frame}
        emptyHint="Not made yet: press Make the camera angles above."
        what={name}
        disabled={jobRunning(vp)}
        regenerating={regen.isPending}
        approving={approve.isPending}
        onRegenerate={(note) => regen.mutate({ what: 'angle', key: angle.key, ...(note ? { note } : {}) }, { onSuccess: () => announce(`Making ${name} again.`) })}
        onApprove={(id) => approve.mutate(id, { onSuccess: () => announce(`${name} approved.`) })}
      />
      {(regen.error || approve.error) && (
        <p role="alert" className="text-small text-studio-danger">
          {(regen.error ?? approve.error)?.message}
        </p>
      )}
    </li>
  )
}
