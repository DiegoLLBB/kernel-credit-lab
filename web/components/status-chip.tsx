import type { AlertStatus } from "@/lib/api";

const ALERT_STYLES: Record<AlertStatus, string> = {
  new: "border-sky-500/40 bg-sky-500/10 text-sky-200",
  confirmed: "border-emerald-500/40 bg-emerald-500/10 text-emerald-200",
  dismissed: "border-slate-500/40 bg-slate-500/10 text-slate-300",
};

export function AlertStatusChip({ status }: { status: AlertStatus }) {
  return (
    <span
      className={`inline-flex items-center rounded border px-2 py-0.5 font-mono text-[11px] uppercase tracking-wide ${ALERT_STYLES[status]}`}
    >
      {status}
    </span>
  );
}

export function MaterialBadge({ material }: { material: boolean }) {
  if (!material) {
    return (
      <span className="inline-flex items-center rounded border border-slate-600/50 bg-slate-800/40 px-2 py-0.5 font-mono text-[11px] uppercase tracking-wide text-slate-400">
        not material
      </span>
    );
  }
  return (
    <span className="inline-flex items-center rounded border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 font-mono text-[11px] uppercase tracking-wide text-amber-200">
      material
    </span>
  );
}

export function PassFailChip({
  passed,
  label,
}: {
  passed: boolean;
  label?: string;
}) {
  const text = label ?? (passed ? "pass" : "fail");
  return (
    <span
      className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 font-mono text-[11px] uppercase tracking-wide ${
        passed
          ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-200"
          : "border-rose-500/40 bg-rose-500/10 text-rose-200"
      }`}
      role="status"
    >
      <span aria-hidden="true">{passed ? "✓" : "✕"}</span>
      {text}
    </span>
  );
}

export function ClaimsStatusChip({ status }: { status: string | null }) {
  const s = (status ?? "unknown").toLowerCase();
  const ok = s === "pass" || s === "supported" || s === "ok";
  const bad = s === "fail" || s === "failed" || s === "unsupported" || s === "reject";
  return (
    <span
      className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 font-mono text-[11px] uppercase tracking-wide ${
        ok
          ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-200"
          : bad
            ? "border-rose-500/40 bg-rose-500/10 text-rose-200"
            : "border-slate-500/40 bg-slate-500/10 text-slate-300"
      }`}
    >
      <span aria-hidden="true">{ok ? "✓" : bad ? "✕" : "·"}</span>
      {status ?? "unknown"}
    </span>
  );
}
