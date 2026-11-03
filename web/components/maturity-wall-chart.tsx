"use client";

import type { MaturityWall } from "@/lib/api";
import { formatDate, formatUsdDisplay, isStaleSchedule, wallAmount } from "@/lib/format";

const DISPLAY_LABELS = ["FY2026", "FY2027", "FY2028", "FY2029"];

const COLORS = {
  named: "#1C232B",
  newsince: "#B4472E",
  unattributed: "#AEB6BA",
} as const;

const SEG_LABEL: Record<string, string> = {
  named: "bonds we track",
  newsince: "added since the last filing",
  unattributed: "rest of the debt in the 10-K",
};

export function MaturityWallChart({
  wall,
  title,
  labels = DISPLAY_LABELS,
  baseline,
  baselineLabel = "1 Jan 2026",
}: {
  wall: MaturityWall | null | undefined;
  title: string;
  labels?: string[];
  /** Earlier wall drawn as a dashed outline, so the year's changes stand out. */
  baseline?: MaturityWall | null;
  baselineLabel?: string;
}) {
  if (!wall || !wall.buckets || Object.keys(wall.buckets).length === 0) {
    return (
      <div>
        <h4 className="mb-2 text-sm font-medium text-memo-muted">{title}</h4>
        <p className="text-sm text-memo-muted">No debt schedule for this date.</p>
      </div>
    );
  }

  if (isStaleSchedule(wall.schedule_as_of, wall.as_of)) {
    return (
      <div>
        <h4 className="mb-2 text-sm font-medium text-memo-muted">{title}</h4>
        <p className="max-w-[60ch] text-sm text-memo-muted">
          The latest debt-maturity schedule in this company&apos;s SEC data is from{" "}
          {formatDate(wall.schedule_as_of)}, too old to show as today&apos;s profile. Kernel leaves it
          out rather than draw years that have already passed.
        </p>
      </div>
    );
  }

  const present = labels.filter((l) => wall.buckets[l]);
  const extras = Object.keys(wall.buckets).filter((l) => !labels.includes(l));
  const show = [...present, ...extras.filter((l) => l.startsWith("FY") && Number(l.slice(2)) <= 2030)];
  const ordered = (show.length > 0 ? show : Object.keys(wall.buckets)).slice(0, 6);

  const series = ordered.map((label) => {
    const b = wall.buckets[label];
    const named = wallAmount(b?.named_active_sum);
    const newsince = wallAmount(b?.new_since_schedule_sum);
    const unattributed = wallAmount(b?.unattributed);
    const total = named + newsince + unattributed;
    const bb = baseline?.buckets?.[label];
    const base = bb
      ? wallAmount(bb.named_active_sum) + wallAmount(bb.new_since_schedule_sum) + wallAmount(bb.unattributed)
      : null;
    return { label, named, newsince, unattributed, total, base, breach: Boolean(b?.breach) };
  });

  const max = Math.max(1, ...series.flatMap((s) => [s.total, s.base ?? 0]));
  const top = 30;
  const chartH = 200;
  const col = 96;
  const barW = 52;
  const width = Math.max(series.length * col, 240);
  const Y = (v: number) => top + chartH - (v / max) * chartH;
  const changed = series.filter(
    (s) => s.base !== null && Math.abs(s.total - s.base) >= Math.max(100_000_000, 0.01 * s.total),
  );

  return (
    <figure>
      <h4 className="mb-3 text-sm font-medium text-memo-muted">{title}</h4>
      <div className="overflow-x-auto">
        <svg
          role="img"
          aria-label={`${title}: ${series.map((s) => `${s.label.replace(/^FY/, "")} ${formatUsdDisplay(s.total)}`).join(", ")}`}
          width="100%"
          preserveAspectRatio="xMinYMin meet"
          style={{ maxWidth: width, height: "auto" }}
          viewBox={`0 0 ${width} ${top + chartH + 40}`}
        >
          <line x1={0} x2={width} y1={top + chartH} y2={top + chartH} stroke="#1C232B" />
          {series.map((s, i) => {
            const x = i * col + (col - barW) / 2;
            let y = top + chartH;
            const segments = [
              { key: "unattributed", color: COLORS.unattributed, value: s.unattributed },
              { key: "newsince", color: COLORS.newsince, value: s.newsince },
              { key: "named", color: COLORS.named, value: s.named },
            ].map((seg) => ({ ...seg, h: (seg.value / max) * chartH }));
            const moved = changed.includes(s);
            return (
              <g key={s.label}>
                {segments.map((seg) => {
                  y -= seg.h;
                  if (seg.h <= 0) return null;
                  return (
                    <rect
                      key={seg.key}
                      x={x}
                      y={y}
                      width={barW}
                      height={seg.h}
                      fill={seg.color}
                      style={{ transition: "y 400ms ease-out, height 400ms ease-out" }}
                    >
                      <title>
                        {s.label.replace(/^FY/, "")} {SEG_LABEL[seg.key] ?? seg.key}: {formatUsdDisplay(seg.value)}
                      </title>
                    </rect>
                  );
                })}
                {s.base !== null && moved ? (
                  <rect
                    x={x - 4}
                    y={Y(s.base)}
                    width={barW + 8}
                    height={top + chartH - Y(s.base)}
                    fill="none"
                    stroke={s.total > s.base ? "#B4472E" : "#2E6B57"}
                    strokeWidth={1.5}
                    strokeDasharray="4 3"
                  >
                    <title>{`${s.label.replace(/^FY/, "")} on ${baselineLabel}: ${formatUsdDisplay(s.base)}`}</title>
                  </rect>
                ) : null}
                <text
                  x={x + barW / 2}
                  y={Math.min(Y(s.total), s.base !== null && moved ? Y(s.base) : Y(s.total)) - 8}
                  textAnchor="middle"
                  fontSize={13}
                  fontWeight={600}
                  fill="#1C232B"
                >
                  {formatUsdDisplay(s.total)}
                </text>
                <text x={x + barW / 2} y={top + chartH + 18} textAnchor="middle" fontSize={13} fill="#1C232B">
                  {s.label.replace(/^FY/, "")}
                </text>
                {moved && s.base !== null ? (
                  <text
                    x={x + barW / 2}
                    y={top + chartH + 34}
                    textAnchor="middle"
                    fontSize={12}
                    fill={s.total > s.base ? "#B4472E" : "#2E6B57"}
                  >
                    {s.total > s.base ? "+" : "−"}
                    {formatUsdDisplay(Math.abs(s.total - s.base))}
                  </text>
                ) : null}
              </g>
            );
          })}
        </svg>
      </div>
      <figcaption className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-memo-muted">
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5" style={{ background: COLORS.named }} aria-hidden />
          Bonds Kernel tracks one by one
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5" style={{ background: COLORS.newsince }} aria-hidden />
          New debt since the last annual filing
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5" style={{ background: COLORS.unattributed }} aria-hidden />
          Rest of the debt in the 10-K schedule
        </span>
        {baseline ? (
          <span className="inline-flex items-center gap-1.5">
            <span className="inline-block h-2.5 w-3 border border-dashed border-memo-muted" aria-hidden />
            Same year on {baselineLabel}
          </span>
        ) : null}
        {series.some((s) => s.breach) ? (
          <span>Some years: tracked bonds exceed the 10-K amount (both kept).</span>
        ) : null}
      </figcaption>
    </figure>
  );
}
