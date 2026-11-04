"use client";

import { useState } from "react";
import { chartImageUrl, type ChartListOut, type ChartReadingOut } from "@/lib/api";
import { formatUsdDisplay } from "@/lib/format";

/** Plain-English reasons for charts the reader refused. */
function refusalText(reason: string | null | undefined): string {
  const r = (reason ?? "").toLowerCase();
  if (r.includes("fewer than two")) return "fewer than two printed values, so there is nothing to compare";
  if (r.includes("bar count")) return "the number of bars found differs from the values printed";
  if (r.includes("baseline")) return "the bars don't share a baseline (stacked or offset chart)";
  if (r.includes("no bars")) return "no bars were found in this region";
  if (r.includes("non-positive")) return "negative or zero values, which this check can't verify";
  return reason ? reason : "the bar heights didn't match the printed values";
}

function filingValue(v: { xbrl_value: number | null; xbrl_unit: string | null }): string {
  if (v.xbrl_value == null) return "—";
  if ((v.xbrl_unit ?? "").toUpperCase().includes("USD")) return formatUsdDisplay(v.xbrl_value);
  return `${v.xbrl_value}${v.xbrl_unit ? ` ${v.xbrl_unit}` : ""}`;
}

function crossCheckLine(c: ChartReadingOut): string | null {
  const x = c.filing_crosscheck;
  if (!c.verified || !x) return null;
  const metric = x.metric_label ? x.metric_label.toLowerCase() : "this figure";
  if (x.summary_status === "agree") return `Matches the company's SEC filings for ${metric}.`;
  if (x.summary_status === "differ") return `Differs from the company's SEC filings for ${metric}; both values are shown below.`;
  if (x.values?.some((v) => v.reason === "segment"))
    return "Not compared with SEC filings: this chart shows a business segment, which the filings don't report on the same basis.";
  return "Not compared with SEC filings: no matching figure for the same period.";
}

/** The verified values redrawn in Kernel's own style; filing matches marked under each bar. */
function NativeBars({
  c,
  cross,
}: {
  c: ChartReadingOut;
  cross: Map<string, { status: string }>;
}) {
  const vals = c.categories.map((cat, i) => ({
    cat: String(cat),
    v: c.values[i] == null ? null : Number(c.values[i]),
  }));
  const nums = vals.map((x) => x.v).filter((x): x is number => x != null && Number.isFinite(x));
  if (nums.length === 0) return null;
  const max = Math.max(...nums.map(Math.abs), 1e-9);
  const col = 84;
  const barW = 46;
  const H = 150;
  const top = 26;
  const W = Math.max(vals.length * col, 200);
  const unit = c.unit ?? "";
  const label = (v: number) => {
    const money = /\$|usd|bn|b\b/i.test(unit);
    const suffix = /b/i.test(unit) && money ? "bn" : /m/i.test(unit) && money ? "M" : "";
    return money ? `$${v.toLocaleString("en-US")}${suffix}` : `${v.toLocaleString("en-US")}${unit && !money ? ` ${unit}` : ""}`;
  };
  return (
    <svg
      role="img"
      aria-label={`${c.title ?? "Chart"}: ${vals.map((x) => `${x.cat} ${x.v ?? "not printed"}`).join(", ")}`}
      viewBox={`0 0 ${W} ${top + H + 40}`}
      width="100%"
      preserveAspectRatio="xMinYMin meet"
      style={{ maxWidth: W, height: "auto" }}
    >
      <line x1={0} x2={W} y1={top + H} y2={top + H} stroke="#1C232B" />
      {vals.map((x, i) => {
        const cx = i * col + col / 2;
        const h = x.v == null ? 0 : (Math.abs(x.v) / max) * H;
        const st = cross.get(x.cat)?.status;
        return (
          <g key={`${x.cat}-${i}`}>
            {x.v != null ? (
              <rect x={cx - barW / 2} y={top + H - h} width={barW} height={h} fill="#1C232B" />
            ) : (
              <rect
                x={cx - barW / 2 + 0.75}
                y={top + H - 20}
                width={barW - 1.5}
                height={20}
                fill="none"
                stroke="#AEB6BA"
                strokeDasharray="3 3"
              />
            )}
            <text x={cx} y={top + H - h - 8} textAnchor="middle" fontSize={13} fontWeight={600} fill="#1C232B">
              {x.v != null ? label(x.v) : "not printed"}
            </text>
            <text x={cx} y={top + H + 18} textAnchor="middle" fontSize={13} fill="#1C232B">
              {x.cat}
            </text>
            {st === "agree" ? (
              <text x={cx} y={top + H + 34} textAnchor="middle" fontSize={12} fill="#2E6B57">
                ✓ filing
              </text>
            ) : st === "differ" ? (
              <text x={cx} y={top + H + 34} textAnchor="middle" fontSize={12} fill="#B4472E">
                ≠ filing
              </text>
            ) : null}
          </g>
        );
      })}
    </svg>
  );
}

function ChartCard({ c }: { c: ChartReadingOut }) {
  const [overlay, setOverlay] = useState(false);
  const checks = c.verification_detail?.checks ?? [];
  const maxGap = checks.reduce(
    (m: number, ch: { deviation?: number | null }) => Math.max(m, Math.abs(ch.deviation ?? 0)),
    0,
  );
  const cross = new Map((c.filing_crosscheck?.values ?? []).map((v) => [String(v.category), v]));
  const showFiling = c.verified && (c.filing_crosscheck?.values ?? []).some((v) => v.xbrl_value != null);
  const xline = crossCheckLine(c);

  return (
    <li className="grid gap-8 border-t border-memo-rule py-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <div>
        <NativeBars c={c} cross={cross} />
        {c.has_overlay || c.has_crop ? (
          <details className="mt-4 text-sm">
            <summary className="cursor-pointer text-memo-info underline underline-offset-2">
              See the original slide
            </summary>
            <div className="mt-3 max-w-xs">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={chartImageUrl(c.id, overlay && c.has_overlay)}
                alt={`Original slide: ${c.title ?? "chart"}`}
                className="w-full border border-memo-rule bg-white"
              />
              {c.has_overlay ? (
                <button
                  type="button"
                  onClick={() => setOverlay((v) => !v)}
                  aria-pressed={overlay}
                  className="mt-2 text-xs text-memo-info underline underline-offset-2"
                >
                  {overlay ? "Hide the check" : "Show the bars SAM 3.1 found"}
                </button>
              ) : null}
              <p className="mt-1 text-xs text-memo-muted">
                The source image, kept as evidence. The chart beside it is redrawn from the verified values.
              </p>
            </div>
          </details>
        ) : null}
      </div>

      <div>
        <h4 className="text-base font-semibold text-memo-ink">{c.title ?? "Untitled chart"}</h4>
        <p className="mt-0.5 text-sm text-memo-muted">
          {[c.filing_title, c.page != null ? `slide ${c.page + 1}` : null].filter(Boolean).join(", ")}
        </p>
        <p className={`mt-3 text-sm ${c.verified ? "text-memo-reduced" : "text-memo-added"}`}>
          {c.verified
            ? `Verified: bar heights match the printed values${checks.length > 0 ? ` (largest gap ${(maxGap * 100).toFixed(1)}%)` : ""}.`
            : `Not verified: ${refusalText(c.verification_detail?.reason)}.`}
        </p>
        {xline ? <p className="mt-1 text-sm text-memo-ink">{xline}</p> : null}

        {c.categories.length > 0 ? (
          <table className="mt-4 w-full border-collapse text-left text-sm">
            <thead>
              <tr className="border-b border-memo-ink text-xs text-memo-muted">
                <th className="py-1.5 pr-3 font-medium">Period</th>
                <th className="py-1.5 pr-3 font-medium">On the slide{c.unit ? ` (${c.unit})` : ""}</th>
                {showFiling ? <th className="py-1.5 pr-3 font-medium">In SEC filings</th> : null}
              </tr>
            </thead>
            <tbody>
              {c.categories.map((cat, i) => {
                const v = cross.get(String(cat));
                const ok = v?.status === "agree";
                const differ = v?.status === "differ";
                return (
                  <tr key={`${String(cat)}-${i}`} className="border-b border-memo-rule">
                    <td className="py-1.5 pr-3 text-memo-muted">{String(cat)}</td>
                    <td className="py-1.5 pr-3 font-medium text-memo-ink">
                      {c.values[i] != null ? String(c.values[i]) : "—"}
                    </td>
                    {showFiling ? (
                      <td className="py-1.5 pr-3">
                        {v ? (
                          <span className={ok ? "text-memo-reduced" : differ ? "text-memo-added" : "text-memo-muted"}>
                            {filingValue(v)}
                            {ok ? " ✓" : differ ? " ≠" : ""}
                          </span>
                        ) : (
                          <span className="text-memo-muted">—</span>
                        )}
                      </td>
                    ) : null}
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : null}
      </div>
    </li>
  );
}

export function VerifiedCharts({
  charts,
  showUnverified,
  onToggleUnverified,
}: {
  charts: ChartListOut | null;
  showUnverified: boolean;
  onToggleUnverified: (v: boolean) => void;
}) {
  // Charts compared with the SEC filings come first. Segment slides repeat titles like
  // "Net Sales" for one part of the business, so on their own they read as duplicates of the
  // company-wide chart; they sit in a fold below with the reason they weren't compared.
  const items = charts?.items ?? [];
  const isChecked = (c: ChartReadingOut) =>
    c.verified && (c.filing_crosscheck?.summary_status === "agree" || c.filing_crosscheck?.summary_status === "differ");
  const checked = items.filter(isChecked);
  const others = items.filter((c) => !isChecked(c));

  return (
    <section aria-labelledby="charts-heading" className="no-print border-t border-memo-rule pt-8">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h3 id="charts-heading" className="text-lg font-semibold text-memo-ink">
          Charts read from investor presentations
        </h3>
        <label className="inline-flex items-center gap-2 text-sm text-memo-muted">
          <input
            type="checkbox"
            checked={showUnverified}
            onChange={(e) => onToggleUnverified(e.target.checked)}
          />
          Include charts Kernel couldn&apos;t verify
        </label>
      </div>
      <p className="mt-1 max-w-[70ch] text-sm text-memo-muted">
        SAM 3.1 finds each chart and its bars, Muse Spark reads the printed values, and a value is kept only
        if the bar heights agree. Kept values are then compared with the company&apos;s SEC filings.
      </p>
      {!charts ? (
        <p className="mt-4 text-sm text-memo-muted">Loading charts…</p>
      ) : charts.items.length === 0 ? (
        <p className="mt-4 text-sm text-memo-muted">
          {showUnverified
            ? "No investor-presentation charts have been read for this company."
            : "No verified charts for this company yet."}
        </p>
      ) : (
        <>
          {checked.length > 0 ? (
            <ul className="mt-2">
              {checked.map((c) => (
                <ChartCard key={c.id} c={c} />
              ))}
            </ul>
          ) : null}
          {others.length > 0 ? (
            <details className="mt-4 border-t border-memo-rule pt-4" open={checked.length === 0}>
              <summary className="cursor-pointer text-sm text-memo-info underline underline-offset-2">
                {checked.length > 0 ? "More charts from the same presentations" : "Charts from the presentations"} (
                {others.length}): business segments and figures with no matching SEC number
              </summary>
              <ul className="mt-2">
                {others.map((c) => (
                  <ChartCard key={c.id} c={c} />
                ))}
              </ul>
            </details>
          ) : null}
        </>
      )}
    </section>
  );
}
