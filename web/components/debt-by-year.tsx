"use client";

import Link from "next/link";
import type { CitedFactOut, FindingOut, MaturityWall, WallBucketDelta } from "@/lib/api";
import { formatUsdMillions, formatUsdMillionsDelta, wallAmount } from "@/lib/format";

const ORDER = ["FY2026", "FY2027", "FY2028", "FY2029", "FY2030"];

export function yearLabel(label: string): string {
  const m = /^after\s*FY(\d{4})$/i.exec(label.trim());
  if (m) return `after ${m[1]}`;
  return label.replace(/^FY/i, "");
}

function total(wall: MaturityWall | null | undefined, label: string): number {
  const b = wall?.buckets?.[label];
  return b ? wallAmount(b.total_outstanding) : 0;
}

/** The quote that best explains a changed year: a finding that names the year first. */
export function quoteForBucket(findings: FindingOut[], label: string): CitedFactOut | null {
  const year = yearLabel(label).replace("after ", "");
  const withQuotes = findings.filter((f) => f.citations.length > 0);
  const named = withQuotes.find(
    (f) => f.statement.includes(year) || f.citations.some((c) => c.exact_quote.includes(year)),
  );
  const pick = named ?? withQuotes.find((f) => /\$|billion|million/i.test(f.statement)) ?? withQuotes[0];
  if (!pick) return null;
  return pick.citations.find((c) => c.exact_quote.includes(year)) ?? pick.citations[0];
}

type Bar = { label: string; before: number; after: number; changed: boolean };

/**
 * Debt due by year with the change marked: the before value as an outline, the after value solid,
 * debt added in brick and debt removed in green, and the main change tied to the quote behind it.
 */
export function DebtByYear({
  beforeWall,
  afterWall,
  affected,
  findings = [],
  animate = false,
}: {
  beforeWall: MaturityWall | null;
  afterWall: MaturityWall | null;
  affected: WallBucketDelta[];
  findings?: FindingOut[];
  animate?: boolean;
}) {
  const affectedMap = new Map(affected.map((a) => [a.label, a]));
  const present = new Set<string>([
    ...Object.keys(afterWall?.buckets ?? {}),
    ...Object.keys(beforeWall?.buckets ?? {}),
    ...affected.map((a) => a.label),
  ]);
  const labels = [
    ...ORDER.filter((l) => present.has(l)),
    ...[...present].filter((l) => !ORDER.includes(l)).sort(),
  ];
  if (labels.length === 0) {
    return <p className="text-sm text-memo-muted">No debt-by-year figures on this alert.</p>;
  }

  // Changed years use the recomputed before/after; other years are unchanged by this alert.
  const bars: Bar[] = labels.map((label) => {
    const a = affectedMap.get(label);
    if (a) return { label, before: wallAmount(a.before), after: wallAmount(a.after), changed: true };
    const v = total(afterWall, label) || total(beforeWall, label);
    return { label, before: v, after: v, changed: false };
  });

  const main = [...bars]
    .filter((b) => b.changed)
    .sort((x, y) => Math.abs(y.after - y.before) - Math.abs(x.after - x.before))[0];
  const quote = main ? quoteForBucket(findings, main.label) : null;

  const max = Math.max(1, ...bars.flatMap((b) => [b.before, b.after]));
  const H = 180;
  const top = 26;
  const col = 92;
  const barW = 44;
  const W = bars.length * col;
  const y = (v: number) => top + H - (v / max) * H;

  return (
    <figure className="space-y-5">
      <div className="overflow-x-auto">
        <svg
          role="img"
          aria-label={
            main
              ? `Debt due by year. ${yearLabel(main.label)} moves from ${formatUsdMillions(main.before)} to ${formatUsdMillions(main.after)}.`
              : "Debt due by year"
          }
          viewBox={`0 0 ${W} ${top + H + 34}`}
          width={W}
          height={top + H + 34}
          className="min-w-full"
        >
          <line x1={0} x2={W} y1={top + H} y2={top + H} stroke="#1C232B" strokeWidth={1} />
          {bars.map((b, i) => {
            const x = i * col + (col - barW) / 2;
            const lo = Math.min(b.before, b.after);
            const hi = Math.max(b.before, b.after);
            const added = b.after > b.before;
            const isMain = main?.label === b.label;
            return (
              <g key={b.label}>
                {/* unchanged part, solid */}
                <rect x={x} y={y(lo)} width={barW} height={top + H - y(lo)} fill="#1C232B" />
                {b.changed && hi > lo ? (
                  added ? (
                    <rect
                      x={x}
                      y={y(hi)}
                      width={barW}
                      height={y(lo) - y(hi)}
                      fill="#B4472E"
                      className={animate ? "grow-up" : undefined}
                    />
                  ) : (
                    <rect
                      x={x + 0.75}
                      y={y(hi)}
                      width={barW - 1.5}
                      height={y(lo) - y(hi)}
                      fill="none"
                      stroke="#2E6B57"
                      strokeWidth={1.5}
                      strokeDasharray="4 3"
                    />
                  )
                ) : null}
                {b.changed ? (
                  <text
                    x={x + barW / 2}
                    y={y(hi) - 8}
                    textAnchor="middle"
                    fontSize={13}
                    fontWeight={isMain ? 600 : 500}
                    fill={added ? "#B4472E" : "#2E6B57"}
                  >
                    {formatUsdMillionsDelta(b.before, b.after)}
                  </text>
                ) : null}
                <text x={x + barW / 2} y={top + H + 18} textAnchor="middle" fontSize={13} fill="#1C232B">
                  {yearLabel(b.label)}
                </text>
                <text x={x + barW / 2} y={top + H + 32} textAnchor="middle" fontSize={11} fill="#5E6A72">
                  {formatUsdMillions(b.after)}
                </text>
                <title>{`${yearLabel(b.label)}: ${formatUsdMillions(b.before)} → ${formatUsdMillions(b.after)}`}</title>
              </g>
            );
          })}
        </svg>
      </div>
      <figcaption className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-memo-muted">
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 bg-memo-ink" aria-hidden /> debt due that year
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 bg-memo-added" aria-hidden /> added by this change
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 border border-dashed border-memo-reduced" aria-hidden />{" "}
          removed by this change
        </span>
      </figcaption>
      {main && quote ? (
        <div
          className={`border-l-[3px] pl-4 ${main.after > main.before ? "border-memo-added" : "border-memo-reduced"}`}
        >
          <p className="text-sm font-semibold text-memo-ink">
            {yearLabel(main.label)}: {formatUsdMillions(main.before)} → {formatUsdMillions(main.after)}{" "}
            <span className={main.after > main.before ? "text-memo-added" : "text-memo-reduced"}>
              ({formatUsdMillionsDelta(main.before, main.after)})
            </span>
            . The filing says:
          </p>
          <blockquote className="mt-2 max-w-[68ch] leading-8">
            <span className="quote-mark">{quote.exact_quote}</span>
          </blockquote>
          <p className="mt-2 text-xs text-memo-muted">
            {[quote.document_title, quote.location].filter(Boolean).join(", ")}
            {quote.filing_id ? (
              <>
                {" "}
                <Link
                  className="text-memo-info underline underline-offset-2"
                  href={`/documents/${quote.filing_id}?fact=${quote.id}${quote.page != null ? `&page=${quote.page}` : ""}`}
                >
                  Open in the document
                </Link>
              </>
            ) : null}
          </p>
        </div>
      ) : null}
    </figure>
  );
}
