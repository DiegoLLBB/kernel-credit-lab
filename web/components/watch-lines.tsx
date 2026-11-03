import type { MaturityWall, MetricTile } from "@/lib/api";
import { formatUsdDisplay, isStaleSchedule, wallAmount } from "@/lib/format";

/**
 * "What to watch": a few plain sentences computed in code from figures already on the page.
 * No model writes them; each appears only when its inputs exist and the move is large enough
 * to matter. Thresholds are deliberately simple and stated here.
 */

export type WatchTone = "risk" | "watch" | "ok";
export type WatchLine = { text: string; tone: WatchTone; anchor: string | null };

const YEARS = ["FY2026", "FY2027", "FY2028", "FY2029", "FY2030"];
const LEVERAGE_MOVE = 0.2; // turns of EBITDA
const COVER_MOVE = 0.1; // 10% relative move in interest cover
const DEBT_MOVE = 10; // percent
const CONCENTRATION = 0.35; // one year's share of 2026–2030 maturities

function num(v: string | number | null | undefined): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

const x = (v: number) => `${v.toFixed(1)}×`;

function toneFromFlag(flag: MetricTile["flag"]): WatchTone {
  return flag === "red" ? "risk" : flag === "amber" ? "watch" : "ok";
}

export function watchLines(metrics: MetricTile[], wall: MaturityWall | null | undefined): WatchLine[] {
  if (wall && isStaleSchedule(wall.schedule_as_of, wall.as_of)) wall = null;
  const by = new Map(metrics.map((m) => [m.metric, m]));
  const out: WatchLine[] = [];

  // 1. Can it pay what comes due? Uses the cover ratio and the 24-month amount only when
  //    both come from the same period, so the sentence never mixes a 10-K with a later 10-Q.
  const dueT = by.get("due_24m");
  const due24 = num(dueT?.value);
  const totalT = by.get("liquidity_coverage_total");
  const cashT = by.get("liquidity_coverage");
  const coverT = num(totalT?.value) !== null ? totalT : num(cashT?.value) !== null ? cashT : undefined;
  const cover = num(coverT?.value);
  if (due24 !== null && due24 > 0 && cover !== null && coverT && coverT.period_end === dueT?.period_end) {
    const period = coverT.fiscal_label ?? "the latest filing";
    const withLines = coverT === totalT;
    const what = withLines ? "cash and undrawn credit lines" : "cash alone";
    out.push({
      text:
        `At ${period}, ${what} covered the ${formatUsdDisplay(due24)} due over the next 24 months ` +
        `${cover.toFixed(2)}×${cover < 1 ? ", less than the amount coming due" : ""}.` +
        (withLines ? "" : " Its undrawn credit lines aren't in a form Kernel can read."),
      tone: cover < 1 ? "risk" : withLines ? toneFromFlag(totalT?.flag ?? null) : cover < 1.5 ? "watch" : "ok",
      anchor: withLines ? "metric-liquidity_coverage_total" : "metric-liquidity_coverage",
    });
  }

  // 2. Leverage over the year.
  const levT = by.get("leverage");
  const lev = num(levT?.value);
  const levPrior = num(levT?.prior_value);
  if (lev !== null && levPrior !== null && Math.abs(lev - levPrior) >= LEVERAGE_MOVE) {
    const up = lev > levPrior;
    out.push({
      text: `Leverage ${up ? "rose" : "fell"} from ${x(levPrior)} to ${x(lev)} debt to EBITDA in a year.`,
      tone: up ? (levT?.flag === "green" ? "watch" : "risk") : "ok",
      anchor: "metric-leverage",
    });
  }

  // 3. Interest cover over the year.
  const icT = by.get("interest_coverage");
  const ic = num(icT?.value);
  const icPrior = num(icT?.prior_value);
  if (ic !== null && icPrior !== null && icPrior > 0 && Math.abs(ic - icPrior) / icPrior >= COVER_MOVE) {
    const down = ic < icPrior;
    out.push({
      text: `Interest cover ${down ? "fell" : "rose"} from ${x(icPrior)} to ${x(ic)} (operating profit over interest) in a year.`,
      tone: down ? (icT?.flag === "green" ? "watch" : "risk") : "ok",
      anchor: "metric-interest_coverage",
    });
  }

  // 4. Total debt over the year.
  const debtT = by.get("total_debt");
  const debt = num(debtT?.value);
  const debtPct = debtT?.change_pct ?? null;
  if (debt !== null && debtPct !== null && Number.isFinite(debtPct) && Math.abs(debtPct) >= DEBT_MOVE) {
    out.push({
      text: `Total debt is ${debtPct > 0 ? "up" : "down"} ${Math.abs(debtPct).toFixed(0)}% on a year earlier, at ${formatUsdDisplay(debt)}.`,
      tone: debtPct > 0 ? "watch" : "ok",
      anchor: "metric-total_debt",
    });
  }

  // 5. One heavy maturity year.
  const amounts = YEARS.map((y) => ({ y, v: wallAmount(wall?.buckets?.[y]?.total_outstanding) }));
  const window = amounts.reduce((s, a) => s + a.v, 0);
  const top = [...amounts].sort((a, b) => b.v - a.v)[0];
  if (window > 0 && top && top.v / window >= CONCENTRATION) {
    out.push({
      text: `${top.y.replace("FY", "")} is the heaviest year: ${formatUsdDisplay(top.v)}, ${Math.round((top.v / window) * 100)}% of everything due 2026–2030.`,
      tone: "watch",
      anchor: "wall-heading",
    });
  }

  const order: Record<WatchTone, number> = { risk: 0, watch: 1, ok: 2 };
  return out.sort((a, b) => order[a.tone] - order[b.tone]).slice(0, 4);
}

const TONE: Record<WatchTone, { bar: string; word: string }> = {
  risk: { bar: "bg-memo-added", word: "Pressure" },
  watch: { bar: "bg-memo-watch", word: "Watch" },
  ok: { bar: "bg-memo-reduced", word: "Easing" },
};

export function WatchList({ lines }: { lines: WatchLine[] }) {
  if (lines.length === 0) return null;
  return (
    <section aria-labelledby="watch-heading" className="mt-8">
      <h4 id="watch-heading" className="text-sm font-medium text-memo-muted">
        What to watch
      </h4>
      <ul className="mt-2 border-t border-memo-ink">
        {lines.map((l) => (
          <li key={l.text} className="grid grid-cols-[4px_4.5rem_minmax(0,1fr)] items-start gap-x-3 border-b border-memo-rule py-2.5">
            <span aria-hidden className={`mt-1 h-4 w-1 ${TONE[l.tone].bar}`} />
            <span className="pt-0.5 text-xs leading-5 text-memo-muted">{TONE[l.tone].word}</span>
            <span className="text-base text-memo-ink">
              {l.text}{" "}
              {l.anchor ? (
                <a href={`#${l.anchor}`} className="no-print text-sm text-memo-info underline underline-offset-2">
                  See the figure
                </a>
              ) : null}
            </span>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-xs text-memo-muted">
        Computed from the figures on this page, not written by a model.
      </p>
    </section>
  );
}
