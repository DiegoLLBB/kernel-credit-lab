/** Grey placeholders in the shape of the page, shown while data loads (no layout jump). */
function Bar({ className }: { className: string }) {
  return <div className={`animate-pulse rounded bg-[#E4E7E3] ${className}`} />;
}

function ChartSkeleton() {
  return (
    <div className="flex h-[200px] items-end gap-6 border-b border-memo-ink pb-0">
      {[30, 55, 70, 100, 40].map((h, i) => (
        <div key={i} className="w-12 animate-pulse bg-[#E4E7E3]" style={{ height: `${h}%` }} />
      ))}
    </div>
  );
}

function RowsSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="border-t border-memo-ink">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="grid grid-cols-[7.5rem_1fr] gap-6 border-b border-memo-rule py-4">
          <Bar className="h-4 w-20" />
          <div className="space-y-2">
            <Bar className="h-4 w-40" />
            <Bar className="h-4 w-full max-w-xl" />
          </div>
        </div>
      ))}
    </div>
  );
}

export function PageSkeleton({ kind }: { kind: "overview" | "issuer" | "alert" }) {
  return (
    <div aria-busy="true" aria-label="Loading" className="space-y-10">
      <span className="sr-only">Loading…</span>
      <div className="space-y-3">
        <Bar className="h-4 w-56" />
        <Bar className="h-9 w-full max-w-2xl" />
        {kind !== "alert" ? <Bar className="h-9 w-2/3 max-w-xl" /> : null}
      </div>
      <ChartSkeleton />
      {kind === "issuer" ? (
        <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }, (_, i) => (
            <div key={i} className="space-y-2 border-t border-memo-rule pt-3">
              <Bar className="h-4 w-28" />
              <Bar className="h-7 w-24" />
            </div>
          ))}
        </div>
      ) : (
        <RowsSkeleton rows={kind === "overview" ? 5 : 3} />
      )}
    </div>
  );
}
