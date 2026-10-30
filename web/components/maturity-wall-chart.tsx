"use client";

import type { MaturityWall } from "@/lib/api";
import { formatUsdMillions, wallAmount } from "@/lib/format";

const DISPLAY_LABELS = ["FY2026", "FY2027", "FY2028", "FY2029"];

const COLORS = {
  named: "#2f6fed",
  newsince: "#38bdf8",
  unattributed: "#64748b",
} as const;

export function MaturityWallChart({
  wall,
  title,
  labels = DISPLAY_LABELS,
}: {
  wall: MaturityWall | null | undefined;
  title: string;
  labels?: string[];
}) {
  if (!wall || !wall.buckets || Object.keys(wall.buckets).length === 0) {
    return (
      <div>
        <h3 className="mb-2 text-sm font-semibold text-slate-300">{title}</h3>
        <p className="text-sm text-slate-400">No maturity wall for this date.</p>
      </div>
    );
  }

  const present = labels.filter((l) => wall.buckets[l]);
  const extras = Object.keys(wall.buckets).filter((l) => !labels.includes(l));
  const show = [...present, ...extras.filter((l) => l.startsWith("FY") && Number(l.slice(2)) <= 2030)];
  // Prefer requested labels; if wall only has those years, use them.
  const ordered = (show.length > 0 ? show : Object.keys(wall.buckets)).slice(0, 6);

  const series = ordered.map((label) => {
    const b = wall.buckets[label];
    const named = wallAmount(b?.named_active_sum);
    const newsince = wallAmount(b?.new_since_schedule_sum);
    const unattributed = wallAmount(b?.unattributed);
    const total = named + newsince + unattributed;
    return {
      label,
      named,
      newsince,
      unattributed,
      total,
      breach: Boolean(b?.breach),
    };
  });

  const max = Math.max(1, ...series.map((s) => s.total));
  const chartH = 160;
  const barW = 36;
  const gap = 28;
  const width = Math.max(series.length * (barW + gap), 200);

  return (
    <div>
      <h3 className="mb-3 text-sm font-semibold text-slate-300">{title}</h3>
      <div className="overflow-x-auto">
        <svg
          role="img"
          aria-label={title}
          width={width}
          height={chartH + 48}
          viewBox={`0 0 ${width} ${chartH + 48}`}
          className="transition-all duration-500"
        >
          {series.map((s, i) => {
            const x = i * (barW + gap) + 8;
            let y = chartH;
            const segments: { h: number; color: string; key: string; value: number }[] = [
              { key: "unattributed", color: COLORS.unattributed, value: s.unattributed, h: 0 },
              { key: "newsince", color: COLORS.newsince, value: s.newsince, h: 0 },
              { key: "named", color: COLORS.named, value: s.named, h: 0 },
            ].map((seg) => ({ ...seg, h: (seg.value / max) * chartH }));

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
                      className="transition-all duration-500 ease-out"
                    >
                      <title>
                        {s.label} {seg.key}: {formatUsdMillions(seg.value)}
                      </title>
                    </rect>
                  );
                })}
                {s.breach ? (
                  <text
                    x={x + barW / 2}
                    y={12}
                    textAnchor="middle"
                    fontSize={9}
                    className="fill-rose-300"
                    fontFamily="ui-monospace, monospace"
                  >
                    breach
                  </text>
                ) : null}
                <text
                  x={x + barW / 2}
                  y={chartH + 16}
                  textAnchor="middle"
                  fontSize={10}
                  className="fill-slate-400"
                  fontFamily="ui-monospace, monospace"
                >
                  {s.label.replace(/^FY/, "")}
                </text>
                <text
                  x={x + barW / 2}
                  y={chartH + 32}
                  textAnchor="middle"
                  fontSize={9}
                  className="fill-slate-500"
                  fontFamily="ui-monospace, monospace"
                >
                  {formatUsdMillions(s.total)}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
      <ul className="mt-3 flex flex-wrap gap-4 font-mono text-[11px] text-slate-400">
        <li className="inline-flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5" style={{ background: COLORS.named }} aria-hidden />
          named active
        </li>
        <li className="inline-flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5" style={{ background: COLORS.newsince }} aria-hidden />
          new since schedule
        </li>
        <li className="inline-flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5" style={{ background: COLORS.unattributed }} aria-hidden />
          unattributed
        </li>
        <li className="inline-flex items-center gap-1.5 text-rose-300">
          <span aria-hidden>⚑</span> breach when named schedule set exceeds schedule amount
        </li>
      </ul>
    </div>
  );
}
