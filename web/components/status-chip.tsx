import type { AlertStatus } from "@/lib/api";

const ALERT_STYLES: Record<AlertStatus, string> = {
  new: "border-memo-info/40 text-memo-info",
  confirmed: "border-memo-reduced/50 bg-[#E7F0EB] text-memo-reduced",
  dismissed: "border-memo-rule text-memo-muted",
};

export function AlertStatusChip({ status }: { status: AlertStatus }) {
  return (
    <span
      className={`inline-flex items-center rounded border px-2 py-0.5 text-xs ${ALERT_STYLES[status]}`}
    >
      {status}
    </span>
  );
}

export function MaterialBadge({ material }: { material: boolean }) {
  if (!material) {
    return (
      <span className="inline-flex items-center rounded border border-memo-rule px-2 py-0.5 text-xs text-memo-muted">
        not material
      </span>
    );
  }
  return (
    <span className="inline-flex items-center rounded border border-memo-watch/50 px-2 py-0.5 text-xs text-memo-watch">
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
      className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 text-xs ${
        passed
          ? "border-memo-reduced/50 bg-[#E7F0EB] text-memo-reduced"
          : "border-memo-added/50 bg-[#F6EAE5] text-memo-added"
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
      className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 text-xs ${
        ok
          ? "border-memo-reduced/50 bg-[#E7F0EB] text-memo-reduced"
          : bad
            ? "border-memo-added/50 bg-[#F6EAE5] text-memo-added"
            : "border-memo-rule text-memo-muted"
      }`}
    >
      <span aria-hidden="true">{ok ? "✓" : bad ? "✕" : "·"}</span>
      {status ?? "unknown"}
    </span>
  );
}
