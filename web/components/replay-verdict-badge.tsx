export function ReplayVerdictBadge({
  verdict,
  reason,
}: {
  verdict: string | null | undefined;
  reason?: string | null;
}) {
  if (!verdict) return null;
  const styles: Record<string, string> = {
    correct: "border-emerald-500/40 bg-emerald-500/10 text-emerald-200",
    "caught, numbers wrong": "border-amber-500/40 bg-amber-500/10 text-amber-200",
    "false alert": "border-rose-500/40 bg-rose-500/10 text-rose-200",
    missed: "border-slate-500/40 bg-slate-500/10 text-slate-300",
    "not yet scored": "border-sky-500/40 bg-sky-500/10 text-sky-200",
  };
  const cls = styles[verdict] ?? "border-slate-500/40 bg-slate-500/10 text-slate-300";
  return (
    <span
      className={`inline-flex items-center rounded border px-2 py-0.5 tabular-nums text-[11px] ${cls}`}
      title={reason ?? undefined}
    >
      {verdict}
    </span>
  );
}
