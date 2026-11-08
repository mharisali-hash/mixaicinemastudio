import { Link, useLocation } from 'react-router'
import { Lock } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useAllows } from '@/hooks/useUsage'
import { featureForPath } from '@/lib/nav'

/** A studio the person's plan doesn't have, reached by a bookmark or an old link (contract v42): a calm note
 * rather than a page whose every button would be refused. The server is what actually says no. */
export function FeatureGate({ children }: { children: React.ReactNode }) {
  const { pathname } = useLocation()
  const allows = useAllows()
  const feature = featureForPath(pathname)
  if (!feature || allows(feature)) return <>{children}</>
  return (
    <main className="flex h-full items-center justify-center p-6" aria-labelledby="gate-title">
      <div className="flex max-w-sm flex-col items-center gap-3 text-center">
        <Lock aria-hidden className="size-8 text-studio-muted" />
        <h1 id="gate-title" className="font-display text-title font-semibold">
          This studio isn't part of your plan
        </h1>
        <p className="text-body text-studio-muted">Everything you made before is still in your Library.</p>
        <Button asChild variant="secondary">
          <Link to="/">Back to Home</Link>
        </Button>
      </div>
    </main>
  )
}
