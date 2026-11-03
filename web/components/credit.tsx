"use client";

import { type MetricTile } from "@/lib/api";
import { formatDate, formatUsdDisplay } from "@/lib/format";

/** Absolute dollars or a plain ratio — every number on screen comes from the API. */
export function formatTileValue(tile: MetricTile): string {
  if (tile.value === null || tile.value === undefined) return "—";
  if (tile.unit === "USD") return formatUsdDisplay(tile.value);
  const n = Number(tile.value);
  if (!Number.isFinite(n)) return String(tile.value);
  return `${n.toFixed(2)}×`;
}

const FLAG_STYLES: Record<string, { dot: string; word: string }> = {
  green: { dot: "bg-emerald-400", word: "comfortable" },
  amber: { dot: "bg-amber-400", word: "worth watching" },
  red: { dot: "bg-rose-400", word: "stretched" },
};

const RATIO_METRICS = new Set([
  "leverage",
  "net_leverage",
  "interest_coverage",
  "liquidity_coverage",
  "fcf_to_debt",
]);

export function FlagDot({ flag }: { flag: MetricTile["flag"] }) {
  if (!flag) return null;
  const style = FLAG_STYLES[flag];
  if (!style) return null;
  return (
    <span className="relative inline-flex items-center gap-1.5" title={`Risk band: ${style.word}`}>
      <span aria-hidden="true" className={`inline-block h-2 w-2 rounded-full ${style.dot}`} />
      <span className="sr-only">Risk band: {style.word}</span>
    </span>
  );
}

function formatChangeValue(tile: MetricTile, raw: string | null): string {
  if (raw === null || raw === undefined) return "—";
  return formatTileValue({ ...tile, value: raw });
}

/** Year-on-year move vs the same point one year earlier (prior_period_end).
 *  Ratios show prior → current; dollar metrics show a % arrow.
 *  Suppressed when the API omits prior_value / change_pct (prior ≤ 0 or near zero). */
export function ChangeArrow({ tile }: { tile: MetricTile }) {
  const vs = tile.prior_period_end
    ? `vs ${formatDate(tile.prior_period_end)}`
    : "Versus same point one year earlier";
  if (RATIO_METRICS.has(tile.metric)) {
    if (tile.prior_value === null || tile.value === null) return null;
    return (
      <span
        className="tabular-nums text-xs text-slate-300"
        title={vs}
        aria-label={`${vs}: ${formatChangeValue(tile, tile.prior_value)} to ${formatTileValue(tile)}`}
      >
        {formatChangeValue(tile, tile.prior_value)} → {formatTileValue(tile)}
      </span>
    );
  }
  const changePct = tile.change_pct;
  if (changePct === null || changePct === undefined || !Number.isFinite(changePct)) return null;
  const up = changePct > 0.05;
  const down = changePct < -0.05;
  const arrow = up ? "▲" : down ? "▼" : "▬";
  const word = up ? "up" : down ? "down" : "flat";
  return (
    <span
      className="tabular-nums text-xs text-slate-300"
      title={`${vs}: ${word}`}
      aria-label={`${word} ${Math.abs(changePct).toFixed(1)} percent ${vs}`}
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
    return <p className="text-xs text-memo-muted">No trend yet</p>;
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
      <polyline points={line} fill="none" stroke="#1C232B" strokeWidth={1.5} />
      {coords.map((c) => (
        <circle key={c.label} cx={c.x} cy={c.y} r={2} fill="#1C232B">
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
      className="scroll-mt-24 border-t border-memo-rule pt-3 pb-1"
    >
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-sm font-medium text-memo-muted">
          {tile.label}
        </h4>
        <FlagDot flag={tile.flag} />
      </div>
      {tile.value === null ? (
        <p className="mt-2 text-sm text-memo-muted">
          {tile.missing_reason === "not meaningful for banks"
            ? "Not meaningful for banks"
            : `Missing${tile.missing_reason ? `: ${tile.missing_reason}` : ""}`}
        </p>
      ) : (
        <>
          <p className="mt-1 text-2xl font-medium text-memo-ink">{formatTileValue(tile)}</p>
          {tile.peer?.watchlist_line ? (
            <p className="mt-0.5 text-xs text-memo-muted">{tile.peer.watchlist_line}</p>
          ) : null}
          {tile.peer?.line && !/fewer than 3/i.test(tile.peer.line) ? (
            <p className="mt-0.5 text-xs text-memo-muted">{tile.peer.line}</p>
          ) : null}
          <p className="mt-0.5 text-xs text-memo-muted">
            {[
              tile.fiscal_label,
              tile.source_note,
              tile.filed ? `filed ${formatDate(tile.filed)}` : null,
            ]
              .filter(Boolean)
              .join(", ")}
          </p>
          <div className="mt-1">
            <ChangeArrow tile={tile} />
          </div>
          <Sparkline tile={tile} />
        </>
      )}
      {tile.metric === "liquidity_coverage" || tile.cash_only ? (
        <p className="mt-2 text-xs leading-snug text-memo-muted">
          Cash only, without undrawn credit lines.
        </p>
      ) : null}
      {tile.wall_disagrees && tile.wall_value !== null ? (
        <p className="mt-2 text-xs leading-snug text-memo-watch">
          The filing&apos;s total, {formatUsdDisplay(tile.wall_value)}, differs from the bonds Kernel tracks;
          both are shown in the debt chart below.
        </p>
      ) : null}
    </div>
  );
}
