import type { MetricTile } from "@/lib/api";
import { formatDate, formatUsdDisplay } from "@/lib/format";

function num(t: MetricTile | undefined): number | null {
  if (!t || t.value === null || t.value === undefined) return null;
  const n = Number(t.value);
  return Number.isFinite(n) ? n : null;
}

const FLAG_WORD: Record<string, string> = {
  green: "comfortable",
  amber: "worth watching",
  red: "stretched",
};

/**
 * Cash plus undrawn committed credit lines against debt due in the next 24 months, read from the
 * snapshot's metric tiles. Shown only when every input exists for the same period end.
 */
export function LiquidityLine({ metrics }: { metrics: MetricTile[] | null | undefined }) {
  const by = new Map((metrics ?? []).map((m) => [m.metric, m]));
  const cashT = by.get("cash");
  const undrawnT = by.get("undrawn_facilities");
  const dueT = by.get("due_24m");
  const totalT = by.get("liquidity_coverage_total");
  const cash = num(cashT);
  const undrawn = num(undrawnT);
  const due = num(dueT);
  const ratio = num(totalT);
  if (cash === null || undrawn === null || due === null || ratio === null || due <= 0) return null;
  const periods = new Set([cashT?.period_end, undrawnT?.period_end, dueT?.period_end].filter(Boolean));
  if (periods.size > 1) return null;

  const available = cash + undrawn;
  const max = Math.max(available, due);
  const W = 560;
  const labelW = 150;
  const barMax = W - labelW - 90;
  const w = (v: number) => Math.max(1, (v / max) * barMax);
  const flag = totalT?.flag ? FLAG_WORD[totalT.flag] : null;
  const period = cashT?.fiscal_label ?? (cashT?.period_end ? formatDate(cashT.period_end) : null);

  return (
    <div className="mt-8 border-t border-memo-rule pt-5">
      <h4 className="text-sm font-medium text-memo-muted">Can it pay what comes due?</h4>
      <p className="mt-1 max-w-[70ch] text-base text-memo-ink">
        Cash {formatUsdDisplay(cash)} + undrawn credit lines {formatUsdDisplay(undrawn)} ={" "}
        <span className="font-semibold">{formatUsdDisplay(available)}</span> available, against{" "}
        {formatUsdDisplay(due)} due in the next 24 months:{" "}
        <span className="font-semibold">{ratio.toFixed(2)}×</span>
        {flag ? `, ${flag}` : ""}.
      </p>
      <svg
        role="img"
        aria-label={`Available ${formatUsdDisplay(available)} versus due ${formatUsdDisplay(due)}`}
        viewBox={`0 0 ${W} 64`}
        width="100%"
        preserveAspectRatio="xMinYMin meet"
        style={{ maxWidth: W, height: "auto" }}
        className="mt-3"
      >
        <text x={0} y={18} fontSize={13} fill="#5E6A72">
          Available
        </text>
        <rect x={labelW} y={6} width={w(cash)} height={16} fill="#1C232B" />
        <rect x={labelW + w(cash)} y={6} width={w(undrawn)} height={16} fill="#AEB6BA" />
        <text x={labelW + w(available) + 8} y={18} fontSize={13} fontWeight={600} fill="#1C232B">
          {formatUsdDisplay(available)}
        </text>
        <text x={0} y={50} fontSize={13} fill="#5E6A72">
          Due in 24 months
        </text>
        <rect x={labelW} y={38} width={w(due)} height={16} fill="none" stroke="#1C232B" strokeWidth={1.5} />
        <text x={labelW + w(due) + 8} y={50} fontSize={13} fontWeight={600} fill="#1C232B">
          {formatUsdDisplay(due)}
        </text>
      </svg>
      <p className="mt-1 text-xs text-memo-muted">
        Dark: cash. Grey: committed bank lines not yet drawn.
        {period ? ` ${period} figures from SEC filings.` : " From SEC filings."}
      </p>
    </div>
  );
}
