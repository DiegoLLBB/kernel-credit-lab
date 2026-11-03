"use client";

import Link from "next/link";
import type { CitedFactOut, FindingOut, MaturityWall, WallBucketDelta } from "@/lib/api";
import { formatUsdDisplay, wallAmount } from "@/lib/format";

const money = (v: number) => formatUsdDisplay(v);
const delta = (before: number, after: number) => {
  const d = after - before;
  return `${d > 0 ? "+" : d < 0 ? "−" : ""}${formatUsdDisplay(Math.abs(d))}`;
};
/** Changes under $100M or 1% of the year are FX or rounding, not news. */
const isNoise = (before: number, after: number) =>
  Math.abs(after - before) < Math.max(100_000_000, 0.01 * Math.max(before, after));

/** Readable source line: "amzn-20260930.htm, page 12" instead of raw offsets. */
export function sourceLine(c: CitedFactOut): string {
  const doc = (c.document_title ?? "").replace(/\.html?$/i, "");
  const page = c.page != null ? `page ${c.page + 1}` : /page=(\d+)/.exec(c.location ?? "")?.[1];
  const pageText = typeof page === "string" && /^\d+$/.test(page) ? `page ${Number(page) + 1}` : page;
  return [doc, pageText].filter(Boolean).join(", ");
}

const ORDER = ["FY2026", "FY2027", "FY2028", "FY2029", "FY2030"];
const SHOWN = new Set(ORDER);

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
  const labels = ORDER.filter((l) => present.has(l));
  const later = [...present].filter((l) => !SHOWN.has(l));
  if (labels.length === 0) {
    return <p className="text-sm text-memo-muted">No debt-by-year figures on this alert.</p>;
  }

  // Changed years use the recomputed before/after; other years are unchanged by this alert.
  const bars: Bar[] = labels.map((label) => {
    const a = affectedMap.get(label);
    if (a) {
      const before = wallAmount(a.before);
      const after = wallAmount(a.after);
      return { label, before, after, changed: !isNoise(before, after) };
    }
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
              ? `Debt due by year. ${yearLabel(main.label)} moves from ${money(main.before)} to ${money(main.after)}.`
              : "Debt due by year"
          }
          viewBox={`0 0 ${W} ${top + H + 34}`}
          width="100%"
          preserveAspectRatio="xMinYMin meet"
          style={{ maxWidth: W, height: "auto" }}
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
                    {delta(b.before, b.after)}
                  </text>
                ) : null}
                <text x={x + barW / 2} y={top + H + 18} textAnchor="middle" fontSize={13} fill="#1C232B">
                  {yearLabel(b.label)}
                </text>
                <text x={x + barW / 2} y={top + H + 32} textAnchor="middle" fontSize={11} fill="#5E6A72">
                  {money(b.after)}
                </text>
                <title>{`${yearLabel(b.label)}: ${money(b.before)} → ${money(b.after)}`}</title>
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
        {later.length > 0 ? (
          <span>
            Not drawn: debt due after 2030,{" "}
            {later
              .map((l) => {
                const a = affectedMap.get(l);
                const v = a ? wallAmount(a.after) : total(afterWall, l) || total(beforeWall, l);
                return money(v);
              })
              .join(", ")}
            .
          </span>
        ) : null}
      </figcaption>
      {main && quote ? (
        <div
          className={`border-l-[3px] pl-4 ${main.after > main.before ? "border-memo-added" : "border-memo-reduced"}`}
        >
          <p className="text-sm font-semibold text-memo-ink">
            {yearLabel(main.label)}: {money(main.before)} → {money(main.after)}{" "}
            <span className={main.after > main.before ? "text-memo-added" : "text-memo-reduced"}>
              ({delta(main.before, main.after)})
            </span>
            . The filing says:
          </p>
          <blockquote className="mt-2 max-w-[68ch] leading-8">
            <span className="quote-mark">{quote.exact_quote}</span>
          </blockquote>
          <p className="mt-2 text-xs text-memo-muted">
            {sourceLine(quote)}
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
