export function ReplayVerdictBadge({
  verdict,
  reason,
}: {
  verdict: string | null | undefined;
  reason?: string | null;
}) {
  // Live alerts are never scored against the replay test set; "not yet scored" only confuses.
  if (!verdict || verdict === "not yet scored") return null;
  const styles: Record<string, string> = {
    correct: "border-memo-reduced/50 bg-[#E7F0EB] text-memo-reduced",
    "caught, numbers wrong": "border-memo-watch/50 bg-[#F4EEDF] text-memo-watch",
    "false alert": "border-memo-added/50 bg-[#F6EAE5] text-memo-added",
    missed: "border-memo-rule text-memo-muted",
    "not yet scored": "border-memo-info/40 text-memo-info",
  };
  const cls = styles[verdict] ?? "border-memo-rule text-memo-muted";
  return (
    <span
      className={`inline-flex items-center rounded border px-2 py-0.5 text-xs ${cls}`}
      title={reason ?? undefined}
    >
      {verdict}
    </span>
  );
}
