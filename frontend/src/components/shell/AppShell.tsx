import { useEffect } from 'react'
import { Outlet, useNavigate, useParams } from 'react-router'
import { Tabs } from '@/components/ui/tabs'
import { useEventStream } from '@/hooks/useEventStream'
import { useBreakpoint } from '@/hooks/useBreakpoint'
import { useF6Cycle } from '@/hooks/useF6Cycle'
import { isStage } from '@/lib/stages'
import { useFavourites } from '@/stores/favourites'
import { useUi } from '@/stores/ui'
import { TopBar } from './TopBar'
import { QueueDrawer } from './QueueDrawer'
import { Rail } from './Rail'
import { BottomBar } from './BottomBar'
import { Toaster } from './Toaster'
import { VerifyBanner } from './VerifyBanner'
import { NoticeBanner } from './NoticeBanner'
import { FeatureGate } from './FeatureGate'

function LiveAnnouncer() {
  const { text, n } = useUi((s) => s.announcement)
  return (
    <div aria-live="polite" aria-atomic="true" className="sr-only">
      {/* key change makes screen readers repeat identical messages */}
      <span key={n}>{text}</span>
    </div>
  )
}

export function AppShell() {
  const { projectId, stage } = useParams()
  const navigate = useNavigate()
  const mobile = useBreakpoint() === 'mobile'
  useEventStream(true)
  useF6Cycle()
  // hearts live on the server since P4; the first visit also hands over the old browser-only ones
  useEffect(() => {
    void useFavourites.getState().sync()
  }, [])

  // The rail takes 72 px beside everything; phones get a bottom bar instead.
  // The Tabs root spans the top bar (tab list) and the workspace (tab panels) so
  // aria-controls/labelledby wire up; the URL is the source of truth.
  return (
    <div className="relative flex h-full overflow-hidden">
      {!mobile && <Rail />}
      <Tabs
        value={isStage(stage) ? stage : ''}
        onValueChange={(v) => projectId && navigate(`/projects/${projectId}/${v}`)}
        activationMode="manual"
        className="flex min-w-0 flex-1 flex-col"
      >
        <VerifyBanner />
        <NoticeBanner />
        <TopBar />
        <div className="min-h-0 flex-1">
          <FeatureGate>
            <Outlet />
          </FeatureGate>
        </div>
        {mobile && <BottomBar />}
      </Tabs>
      <QueueDrawer />
      <Toaster />
      <LiveAnnouncer />
    </div>
  )
}
