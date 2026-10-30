"use client";

import type { MaturityWall, WallBucketDelta } from "@/lib/api";
import { formatUsdMillions, formatUsdMillionsDelta, wallAmount } from "@/lib/format";

const DISPLAY_LABELS = ["FY2026", "FY2027", "FY2028", "FY2029", "FY2030"];

function bucketTotal(wall: MaturityWall | null | undefined, label: string): number {
  const b = wall?.buckets?.[label];
  if (!b) return 0;
  return wallAmount(b.total_outstanding);
}

export function WallCompare({
  beforeWall,
  afterWall,
  affected,
}: {
  beforeWall: MaturityWall | null;
  afterWall: MaturityWall | null;
  affected: WallBucketDelta[];
}) {
  const affectedMap = new Map(affected.map((a) => [a.label, a]));
  const labels = (() => {
    const fromWalls = new Set<string>([
      ...Object.keys(beforeWall?.buckets ?? {}),
      ...Object.keys(afterWall?.buckets ?? {}),
    ]);
    const ordered = [
      ...DISPLAY_LABELS.filter((l) => fromWalls.has(l)),
      ...[...fromWalls].filter((l) => !DISPLAY_LABELS.includes(l)),
    ];
    return ordered.length > 0 ? ordered : DISPLAY_LABELS;
  })();

  const max = Math.max(
    1,
    ...labels.flatMap((l) => [bucketTotal(beforeWall, l), bucketTotal(afterWall, l)]),
  );

  if (!beforeWall && !afterWall) {
    return <p className="text-sm text-slate-400">No before/after wall on this alert.</p>;
  }

  const chartH = 120;
  const barW = 18;
  const gap = 10;
  const pairGap = 28;
  const width = labels.length * (barW * 2 + gap + pairGap);

  return (
    <div className="space-y-4">
      <div className="overflow-x-auto">
        <svg
          role="img"
          aria-label="Before and after maturity wall by fiscal year"
          width={width}
          height={chartH + 36}
          className="min-w-full"
          viewBox={`0 0 ${width} ${chartH + 36}`}
        >
          {labels.map((label, i) => {
            const x0 = i * (barW * 2 + gap + pairGap);
            const before = bucketTotal(beforeWall, label);
            const after = bucketTotal(afterWall, label);
            const hBefore = (before / max) * chartH;
            const hAfter = (after / max) * chartH;
            const isAffected = affectedMap.has(label);
            return (
              <g key={label}>
                <rect
                  x={x0}
                  y={chartH - hBefore}
                  width={barW}
                  height={hBefore}
                  fill={isAffected ? "#94a3b8" : "#475569"}
                >
                  <title>{`${label} before ${formatUsdMillions(before)}`}</title>
                </rect>
                <rect
                  x={x0 + barW + gap}
                  y={chartH - hAfter}
                  width={barW}
                  height={hAfter}
                  fill={isAffected ? "#2f6fed" : "#64748b"}
                >
                  <title>{`${label} after ${formatUsdMillions(after)}`}</title>
                </rect>
                <text
                  x={x0 + barW + gap / 2}
                  y={chartH + 16}
                  textAnchor="middle"
                  className="fill-slate-400"
                  fontSize={10}
                  fontFamily="ui-monospace, monospace"
                >
                  {label.replace("FY", "")}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
      <div className="flex flex-wrap gap-4 font-mono text-[11px] text-slate-400">
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 bg-slate-500" aria-hidden /> before
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 bg-accent" aria-hidden /> after (affected)
        </span>
      </div>
      {affected.length > 0 ? (
        <ul className="space-y-1.5 text-sm">
          {affected.map((a) => {
            const delta = formatUsdMillionsDelta(a.before, a.after);
            return (
              <li key={a.label} className="font-mono text-slate-200">
                <span className="text-accent">{a.label}</span>{" "}
                {formatUsdMillions(a.before)} → {formatUsdMillions(a.after)}
                {delta ? <span className="text-slate-400"> ({delta})</span> : null}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="text-sm text-slate-400">No affected buckets listed.</p>
      )}
    </div>
  );
}
