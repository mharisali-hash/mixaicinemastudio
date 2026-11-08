import { Outlet } from 'react-router'
import { TransportBar } from '@/features/audio/shell/TransportBar'

/**
 * Podcast Studio's frame. No Audio sidebar, but the same transport along the bottom: the Voices and Produce
 * steps play through the shared player, and that bar is where you pause and seek.
 */
export function PodcastFrame() {
  return (
    <div className="relative flex h-full min-h-0 flex-col overflow-hidden" data-podcast-studio>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <Outlet />
      </div>
      <TransportBar />
    </div>
  )
}
