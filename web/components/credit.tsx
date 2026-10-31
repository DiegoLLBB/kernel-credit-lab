"use client";

import { type MetricTile } from "@/lib/api";
import { formatDate, formatUsdMillions } from "@/lib/format";

/** Absolute dollars or a plain ratio — every number on screen comes from the API. */
export function formatTileValue(tile: MetricTile): string {
  if (tile.value === null || tile.value === undefined) return "—";
  if (tile.unit === "USD") return formatUsdMillions(tile.value);
  const n = Number(tile.value);
  if (!Number.isFinite(n)) return String(tile.value);
  return `${n.toFixed(2)}×`;
}

const FLAG_STYLES: Record<string, { dot: string; word: string }> = {
  green: { dot: "bg-emerald-400", word: "comfortable" },
  amber: { dot: "bg-amber-400", word: "worth watching" },
  red: { dot: "bg-rose-400", word: "stretched" },
  none: { dot: "bg-slate-500", word: "no band" },
};

export function FlagDot({ flag }: { flag: MetricTile["flag"] }) {
  const style = FLAG_STYLES[flag ?? "none"] ?? FLAG_STYLES.none;
  return (
    <span className="inline-flex items-center gap-1.5" title={`Risk band: ${style.word}`}>
      <span aria-hidden="true" className={`inline-block h-2 w-2 rounded-full ${style.dot}`} />
      <span className="sr-only">Risk band: {style.word}</span>
    </span>
  );
}

/** Direction of the year-on-year move (the dot, not the arrow, is the judgement). */
export function ChangeArrow({ changePct }: { changePct: number | null }) {
  if (changePct === null || changePct === undefined || !Number.isFinite(changePct)) {
    return <span className="font-mono text-xs text-slate-500">—</span>;
  }
  const up = changePct > 0.05;
  const down = changePct < -0.05;
  const arrow = up ? "▲" : down ? "▼" : "▬";
  const word = up ? "up" : down ? "down" : "flat";
  return (
    <span
      className="font-mono text-xs text-slate-300"
      title={`Versus one year earlier: ${word}`}
      aria-label={`${word} ${Math.abs(changePct).toFixed(1)} percent versus one year earlier`}
    >
      {arrow} {Math.abs(changePct).toFixed(1)}%
    </span>
  );
}

/** Hand-drawn SVG trend of the last 3 years; null years are skipped, never joined. */
export function Sparkline({ tile }: { tile: MetricTile }) {
  const points = tile.sparkline.filter(
    (p): p is typeof p & { value: string } => p.value !== null,
  );
  if (points.length < 2) {
    return <p className="font-mono text-[11px] text-slate-500">No trend yet</p>;
  }
  const values = points.map((p) => Number(p.value));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || Math.abs(max) || 1;
  const w = 120;
  const h = 36;
  const coords = points.map((p, i) => {
    const x = points.length === 1 ? 0 : (i / (points.length - 1)) * (w - 4) + 2;
    const y = h - 4 - ((Number(p.value) - min) / span) * (h - 8);
    return { x, y, label: p.label, value: p.value };
  });
  const line = coords.map((c) => `${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(" ");
  return (
    <svg
      role="img"
      aria-label={`Trend for ${tile.label}: ${points.map((p) => `${p.label} ${formatTileValue({ ...tile, value: p.value })}`).join(", ")}`}
      width={w}
      height={h}
      viewBox={`0 0 ${w} ${h}`}
      className="mt-2"
    >
      <polyline points={line} fill="none" stroke="#7dd3fc" strokeWidth={1.5} />
      {coords.map((c) => (
        <circle key={c.label} cx={c.x} cy={c.y} r={2} fill="#7dd3fc">
          <title>{`${c.label}: ${formatTileValue({ ...tile, value: c.value })}`}</title>
        </circle>
      ))}
    </svg>
  );
}

export function MetricTileCard({ tile }: { tile: MetricTile }) {
  return (
    <div
      id={`metric-${tile.metric}`}
      className="scroll-mt-24 rounded-lg border border-slate-700/60 bg-ink-soft/40 p-4"
    >
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-xs font-medium uppercase tracking-wide text-slate-400">
          {tile.label}
        </h4>
        <FlagDot flag={tile.flag} />
      </div>
      {tile.value === null ? (
        <p className="mt-2 text-sm text-slate-300">
          Missing{tile.missing_reason ? `: ${tile.missing_reason}` : ""}
        </p>
      ) : (
        <>
          <p className="mt-1 font-mono text-xl text-slate-100">{formatTileValue(tile)}</p>
          <p className="mt-0.5 font-mono text-[11px] text-slate-500">
            {[tile.fiscal_label, tile.filed ? `filed ${formatDate(tile.filed)}` : null]
              .filter(Boolean)
              .join(" · ")}
          </p>
          <div className="mt-1">
            <ChangeArrow changePct={tile.change_pct} />
          </div>
          <Sparkline tile={tile} />
        </>
      )}
      {tile.cash_only ? (
        <p className="mt-2 text-[11px] leading-snug text-slate-500">
          Cash only — undrawn credit lines aren&apos;t in the tagged filing data.
        </p>
      ) : null}
      {tile.wall_disagrees && tile.wall_value !== null ? (
        <p className="mt-2 text-[11px] leading-snug text-amber-300/90">
          Filing total {formatUsdMillions(tile.wall_value)} differs from the bonds we track — both
          shown, see the debt wall below.
        </p>
      ) : null}
    </div>
  );
}
