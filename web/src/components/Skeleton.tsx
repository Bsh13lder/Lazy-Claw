/**
 * Reusable skeleton loading components for placeholder UI.
 */

function Bar({ className = "" }: { className?: string }) {
  return (
    <div className={`bg-bg-tertiary rounded-lg animate-pulse ${className}`} />
  );
}

/** A single card skeleton matching the Overview Card layout */
export function CardSkeleton() {
  return (
    <div className="bg-bg-secondary border border-border rounded-xl p-4 flex items-start gap-3">
      <Bar className="w-10 h-10 rounded-lg shrink-0" />
      <div className="flex-1 space-y-2 py-0.5">
        <Bar className="h-3 w-20" />
        <Bar className="h-5 w-12" />
        <Bar className="h-2.5 w-24" />
      </div>
    </div>
  );
}

/** Grid of card skeletons for Overview page */
export function OverviewSkeleton() {
  return (
    <div className="max-w-4xl mx-auto px-6 py-8">
      <div className="mb-8 space-y-2">
        <Bar className="h-6 w-32" />
        <Bar className="h-4 w-56" />
      </div>
      <div className="mb-6">
        <Bar className="h-12 w-full rounded-xl" />
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <CardSkeleton key={i} />
        ))}
      </div>
    </div>
  );
}

/** A single list-row skeleton */
export function RowSkeleton() {
  return (
    <div className="flex items-center gap-3 px-4 py-3 rounded-xl bg-bg-secondary border border-border">
      <Bar className="w-2 h-2 rounded-full shrink-0" />
      <div className="flex-1 space-y-1.5">
        <Bar className="h-4 w-40" />
        <Bar className="h-3 w-64" />
      </div>
      <Bar className="h-5 w-16 rounded-full" />
    </div>
  );
}

/** List skeleton used by Skills, Jobs, MCP, Memory, Vault pages */
export function ListSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="max-w-4xl mx-auto px-6 py-8">
      <div className="flex items-center justify-between mb-6">
        <div className="space-y-2">
          <Bar className="h-5 w-24" />
          <Bar className="h-3.5 w-16" />
        </div>
        <div className="flex gap-2">
          <Bar className="h-8 w-20 rounded-lg" />
          <Bar className="h-8 w-16 rounded-lg" />
        </div>
      </div>
      <div className="space-y-2">
        {Array.from({ length: rows }).map((_, i) => (
          <RowSkeleton key={i} />
        ))}
      </div>
    </div>
  );
}

/** Settings page skeleton with tabs and sections */
export function SettingsSkeleton() {
  return (
    <div className="max-w-4xl mx-auto px-6 py-8">
      <div className="mb-6 space-y-2">
        <Bar className="h-5 w-20" />
        <Bar className="h-3.5 w-64" />
      </div>
      <Bar className="h-9 w-56 rounded-xl mb-6" />
      <div className="space-y-6">
        <div className="bg-bg-secondary border border-border rounded-xl p-5 space-y-3">
          <Bar className="h-4 w-16" />
          <Bar className="h-3 w-48" />
          <div className="flex gap-2">
            <Bar className="h-9 w-20 rounded-lg" />
            <Bar className="h-9 w-20 rounded-lg" />
            <Bar className="h-9 w-20 rounded-lg" />
          </div>
        </div>
        <div className="bg-bg-secondary border border-border rounded-xl p-5 space-y-3">
          <Bar className="h-4 w-16" />
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="space-y-1">
                <Bar className="h-2.5 w-16" />
                <Bar className="h-6 w-12" />
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
