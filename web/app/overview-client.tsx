"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ChangeArrow, FlagDot, formatTileValue } from "@/components/credit";
import { ComingDue } from "@/components/coming-due";
import { FeaturedChange } from "@/components/featured-change";
import { ReplayVerdictBadge } from "@/components/replay-verdict-badge";
import { EmptyLine, ErrorLine, LoadingLine, PageShell } from "@/components/loading-error";
import {
  API_URL,
  getJson,
  type BriefSentence,
  type CreditOverview,
  type EvalSummary,
  type MetricTile,
  type WatchlistRow,
} from "@/lib/api";
import { formatBeforeAfter, formatDate, liveOutcomeLabel } from "@/lib/format";

const WATCH_METRICS = [
  "total_debt",
  "due_24m",
  "liquidity_coverage",
  "leverage",
  "interest_coverage",
] as const;

type SortKey = "name" | (typeof WATCH_METRICS)[number];

function BriefSentenceText({
  sentence,
  refs,
}: {
  sentence: string;
  refs: BriefSentence["refs"];
}) {
  const primary = refs[0];
  if (!primary?.href) {
    return <>{sentence}</>;
  }
  // Link the first matching token; fall back to linking the whole sentence.
  const match = primary.match && sentence.includes(primary.match) ? primary.match : null;
  if (!match) {
    return (
      <Link href={primary.href} className="underline decoration-slate-600 underline-offset-2 hover:text-white">
        {sentence}
      </Link>
    );
  }
  const idx = sentence.indexOf(match);
  return (
    <>
      {sentence.slice(0, idx)}
      <Link
        href={primary.href}
        className="underline decoration-slate-600 underline-offset-2 hover:text-white"
      >
        {match}
      </Link>
      {sentence.slice(idx + match.length)}
    </>
  );
}

function EvalBlock({
  title,
  summary,
  empty,
}: {
  title: string;
  summary: EvalSummary | null | undefined;
  empty: string;
}) {
  return (
    <div>
      <p className="tabular-nums text-[11px] text-slate-500">{title}</p>
      <p className="mt-1 text-sm leading-relaxed text-slate-300">
        {summary ? summary.text : empty}
      </p>
      {summary && (summary.precision != null || summary.recall != null) ? (
        <p className="mt-1 tabular-nums text-xs text-slate-500">
          share of alerts that were real{" "}
          {summary.precision != null ? summary.precision.toFixed(3) : "n/a"} · share of real
          events caught {summary.recall != null ? summary.recall.toFixed(3) : "n/a"}
          {summary.unsupported_claims != null
            ? ` · numbers not backed by the filing ${summary.unsupported_claims}`
            : ""}
        </p>
      ) : summary && summary.unsupported_claims != null ? (
        <p className="mt-1 tabular-nums text-xs text-slate-500">
          numbers not backed by the filing {summary.unsupported_claims}
        </p>
      ) : null}
      {summary &&
      ((summary.missed_events?.length ?? 0) > 0 || (summary.false_events?.length ?? 0) > 0) ? (
        <details className="mt-2 text-xs text-slate-400">
          <summary className="cursor-pointer text-slate-300">Details</summary>
          {(summary.missed_events?.length ?? 0) > 0 ? (
            <div className="mt-2">
              <p className="font-medium text-slate-300">Missed</p>
              <ul className="mt-1 space-y-0.5 tabular-nums text-[11px]">
                {summary.missed_events?.map((e) => (
                  <li key={`m-${title}-${e.name}`}>
                    {e.date} · {e.name}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {(summary.false_events?.length ?? 0) > 0 ? (
            <div className="mt-2">
              <p className="font-medium text-slate-300">False alerts</p>
              <ul className="mt-1 space-y-0.5 tabular-nums text-[11px]">
                {summary.false_events?.map((e) => (
                  <li key={`f-${title}-${e.name}`}>
                    {e.date} · {e.name}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </details>
      ) : null}
    </div>
  );
}

function tileValue(row: WatchlistRow, metric: string): number | null {
  const tile = row.metrics.find((t) => t.metric === metric);
  if (!tile || tile.value === null) return null;
  const n = Number(tile.value);
  return Number.isFinite(n) ? n : null;
}

export function OverviewClient() {
  const [overview, setOverview] = useState<CreditOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("name");
  const [sortAsc, setSortAsc] = useState(true);

  useEffect(() => {
    let cancelled = false;
    getJson<CreditOverview>("/api/credit/overview")
      .then((d) => {
        if (!cancelled) setOverview(d);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const rows = useMemo(() => {
    const list = [...(overview?.watchlist ?? [])];
    list.sort((a, b) => {
      if (sortKey === "name") {
        const cmp = a.issuer.name.localeCompare(b.issuer.name);
        return sortAsc ? cmp : -cmp;
      }
      const va = tileValue(a, sortKey);
      const vb = tileValue(b, sortKey);
      if (va === null && vb === null) return 0;
      if (va === null) return 1;
      if (vb === null) return -1;
      return sortAsc ? va - vb : vb - va;
    });
    return list;
  }, [overview, sortKey, sortAsc]);

  function toggleSort(k: SortKey) {
    if (sortKey === k) setSortAsc((v) => !v);
    else {
      setSortKey(k);
      setSortAsc(true);
    }
  }

  const header = overview?.header;
  const headerLine = header
    ? `Last checked ${formatDate(header.last_check)}. ${header.filings_read} filings read across ${header.companies} ${header.companies === 1 ? "company" : "companies"}, ${header.material_changes} material ${header.material_changes === 1 ? "change" : "changes"} found.`
    : null;

  const agentFeed = overview ? overview.feed.filter((i) => i.kind !== "metric") : [];
  const metricFeed = overview ? overview.feed.filter((i) => i.kind === "metric") : [];

  function renderFeedItem(item: CreditOverview["feed"][number]) {
    const change = item.before_after
      ? formatBeforeAfter(item.before_after.before, item.before_after.after, item.before_after.field, {
          before: item.before_after.before_display,
          after: item.before_after.after_display,
        })
      : null;
    return (
      <li key={item.alert_id} className="border-b border-memo-rule">
        <Link
          href={`/alerts/${item.alert_id}`}
          className="grid grid-cols-1 gap-x-6 gap-y-1 py-4 hover:bg-[#EEF0EC] sm:grid-cols-[7.5rem_1fr]"
        >
          <span className="text-sm text-memo-muted">{formatDate(item.event_date)}</span>
          <span>
            <span className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
              <span className="font-semibold text-memo-ink">{item.issuer.name}</span>
              {item.live ? (
                <span className="rounded border border-memo-info/50 px-1.5 py-0.5 text-xs text-memo-info">
                  found live
                </span>
              ) : null}
              {item.kind === "metric" ? null : (
                <ReplayVerdictBadge verdict={item.replay_verdict} reason={item.replay_verdict_reason} />
              )}
            </span>
            <span className="mt-1 block max-w-[70ch] text-base text-memo-ink">
              {item.impact_sentence ?? item.title}
            </span>
            {change ? (
              <span className="mt-1 block text-sm text-memo-muted">
                {item.before_after?.field ? `${item.before_after.field.replace(/^FY/, "")}: ` : null}
                {change}
              </span>
            ) : null}
            {item.impact_pending ? (
              <span className="mt-1 block text-xs text-memo-muted">Summary still being written.</span>
            ) : null}
          </span>
        </Link>
      </li>
    );
  }

  const metricLabel = (metric: string): string =>
    overview?.watchlist
      .flatMap((r) => r.metrics)
      .find((t) => t.metric === metric)?.label ?? metric;

  return (
    <PageShell title="What changed">
      <header className="mb-12 max-w-3xl">
        <h1 className="text-3xl font-semibold text-memo-ink sm:text-[46px] sm:leading-[52px]">
          What changed
        </h1>
        <p className="mt-4 max-w-[62ch] text-lg text-memo-ink">
          Kernel reads company filings as they are published and tells you when a company&apos;s
          debt picture changes. Every number links to the sentence in the filing it came from.
        </p>
        {headerLine ? (
          <p className="mt-3 text-sm text-memo-muted" aria-live="polite">
            {headerLine}
          </p>
        ) : null}
      </header>

      {error ? <ErrorLine message={`API unreachable: ${error}`} /> : null}
      {!error && !overview ? <LoadingLine label="Loading dashboard…" /> : null}

      {overview ? (
        <>
          {agentFeed[0] ? <FeaturedChange alertId={agentFeed[0].alert_id} /> : null}

          {overview.brief && overview.brief.sentences.length > 0 ? (
            <section
              aria-labelledby="brief-heading"
              className="mb-12 max-w-3xl border-l-[3px] border-memo-ink pl-5"
            >
              <h2 id="brief-heading" className="text-lg font-semibold text-memo-ink">
                Today&apos;s brief, {formatDate(overview.brief.as_of)}
              </h2>
              <ul className="mt-3 space-y-2">
                {overview.brief.sentences.map((item, i) => (
                  <li key={`brief-${i}`} className="text-base leading-7 text-memo-ink">
                    <BriefSentenceText sentence={item.sentence} refs={item.refs} />
                  </li>
                ))}
              </ul>
              {(overview.brief.refused?.length ?? 0) > 0 ? (
                <details className="mt-4 text-xs text-slate-500">
                  <summary className="cursor-pointer text-slate-400">
                    {overview.brief.refused_count} sentence
                    {overview.brief.refused_count === 1 ? "" : "s"} refused
                  </summary>
                  <ul className="mt-2 list-disc space-y-2 pl-5">
                    {overview.brief.refused!.map((r, i) => (
                      <li key={`refused-${i}`}>
                        {r.sentence ? (
                          <span className="text-slate-400 line-through">{r.sentence}</span>
                        ) : (
                          <span className="text-slate-500">(no sentence)</span>
                        )}
                        {r.reason ? (
                          <span className="mt-0.5 block text-amber-200/70">
                            Refused: {r.reason}
                          </span>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                </details>
              ) : null}
              <p className="mt-4 text-xs text-memo-muted">
                {overview.brief.attribution}
                {overview.brief.cost_label && !overview.brief.attribution.includes(overview.brief.cost_label)
                  ? ` · ${overview.brief.cost_label}`
                  : null}
              </p>
            </section>
          ) : (
            <section className="mb-10 max-w-3xl" aria-labelledby="brief-empty">
              <h2
                id="brief-empty"
                className="mb-2 text-lg font-semibold text-memo-ink"
              >
                Today&apos;s brief
              </h2>
              <EmptyLine>
                No brief yet — written after each live run (and once for the replay&apos;s last day).
              </EmptyLine>
            </section>
          )}

          <section aria-labelledby="feed-heading" className="mb-12">
            <div className="flex flex-wrap items-baseline justify-between gap-3">
              <h2 id="feed-heading" className="text-lg font-semibold text-memo-ink">
                Changes found by the agent, newest first
              </h2>
              <span className="flex gap-4 text-sm">
                <a href={`${API_URL}/api/alerts.csv`} className="text-memo-info underline underline-offset-2">
                  Download as CSV
                </a>
                <a href={`${API_URL}/api/alerts.rss`} className="text-memo-info underline underline-offset-2">
                  RSS feed
                </a>
              </span>
            </div>
            {agentFeed.length === 0 ? (
              <EmptyLine>No material changes found yet.</EmptyLine>
            ) : (
              <ol className="mt-3 border-t border-memo-ink">{agentFeed.map(renderFeedItem)}</ol>
            )}
            {metricFeed.length > 0 ? (
              <details className="mt-6">
                <summary className="cursor-pointer text-sm font-medium text-memo-muted">
                  Metric changes ({metricFeed.length})
                </summary>
                <p className="mt-2 max-w-[70ch] text-sm text-memo-muted">
                  Computed in code from XBRL data when a filing moves a ratio across a band or lifts
                  near-term maturities. Not part of the agent&apos;s score.
                </p>
                <ol className="mt-3 border-t border-memo-rule">{metricFeed.map(renderFeedItem)}</ol>
              </details>
            ) : null}
          </section>

          <ComingDue />

          <section aria-labelledby="watchlist-heading" className="mb-10">
            <div className="mb-3 flex flex-wrap items-baseline justify-between gap-3">
              <h2 id="watchlist-heading" className="text-lg font-semibold text-memo-ink">
                Watchlist
              </h2>
              <a href={`${API_URL}/api/watchlist.csv`} className="text-sm text-memo-info underline underline-offset-2">
                Download as CSV
              </a>
            </div>
            <div className="overflow-x-auto border-t border-memo-ink">
              <table className="min-w-full text-left text-sm">
                <thead className="bg-ink-soft tabular-nums text-[11px] text-slate-500">
                  <tr>
                    <th className="px-3 py-2">
                      <button type="button" onClick={() => toggleSort("name")} className="focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
                        Company{sortKey === "name" ? (sortAsc ? " ↑" : " ↓") : ""}
                      </button>
                    </th>
                    <th className="px-3 py-2 tabular-nums text-[11px] text-slate-500">
                      Sector
                    </th>
                    {WATCH_METRICS.map((m) => (
                      <th key={m} className="px-3 py-2">
                        <button type="button" onClick={() => toggleSort(m)} className="focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
                          {metricLabel(m)}{sortKey === m ? (sortAsc ? " ↑" : " ↓") : ""}
                        </button>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800">
                  {rows.map((row) => {
                    const key = row.issuer.key ?? row.issuer.id;
                    return (
                      <tr key={row.issuer.id} className="align-top">
                        <td className="px-3 py-2">
                          <Link
                            href={`/issuers/${encodeURIComponent(key)}`}
                            className="text-slate-100 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                          >
                            {row.issuer.name}
                          </Link>
                          <div className="tabular-nums text-[11px] text-slate-500">
                            {row.ticker ?? key}
                          </div>
                        </td>
                        <td className="px-3 py-2 tabular-nums text-xs text-slate-400">
                          {row.sector ?? "—"}
                        </td>
                        {WATCH_METRICS.map((m) => {
                          const tile: MetricTile | undefined = row.metrics.find(
                            (t) => t.metric === m,
                          );
                          if (!tile) return <td key={m} className="px-3 py-2 tabular-nums text-xs text-slate-500">—</td>;
                          return (
                            <td key={m} className="px-3 py-2">
                              <span className="flex items-center gap-1.5 tabular-nums text-xs text-slate-200">
                                <FlagDot flag={tile.flag} />
                                {tile.value === null ? (
                                  <span title={tile.missing_reason ?? "No data yet"}>
                                    {tile.missing_reason === "not meaningful for banks"
                                      ? "n/a (banks)"
                                      : "Missing"}
                                  </span>
                                ) : (
                                  formatTileValue(tile)
                                )}
                              </span>
                              <div className="mt-0.5">
                                <ChangeArrow tile={tile} />
                              </div>
                            </td>
                          );
                        })}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <details className="mt-3 max-w-3xl text-xs leading-relaxed text-slate-400">
              <summary className="cursor-pointer text-slate-300">How to read this table</summary>
              <p className="mt-2">
                Changes compare to the same point one year earlier (hover a cell for the
                comparison date). Ratio columns show prior → current; dollar columns show a
                percent move. Missing prior → —. Coloured dots are screening bands, not ratings:
                green comfortable, amber worth watching, red stretched. Liquidity has no band —
                cash only, excludes undrawn credit lines.
              </p>
              {overview.thresholds.length > 0 ? (
                <ul className="mt-2 space-y-1 tabular-nums text-[11px] text-slate-500">
                  {overview.thresholds.map((t) => (
                    <li key={t.label}>
                      {t.label} ({t.unit}):{" "}
                      {Object.entries(t.bands)
                        .map(([k, v]) => `${k} ${String(v)}`)
                        .join(" · ")}
                    </li>
                  ))}
                </ul>
              ) : null}
            </details>
          </section>

          {overview.live ? (
            <section aria-labelledby="live-heading" className="mb-10">
              <h2 id="live-heading" className="mb-3 text-lg font-semibold text-memo-ink">
                Live since {formatDate(overview.live.since.slice(0, 10))}
              </h2>
              <p className="mb-3 tabular-nums text-xs text-slate-400">
                {overview.live.documents_read} documents read · {overview.live.alerted} raised
                alert
                {overview.live.alerted === 1 ? "" : "s"} · {overview.live.logged} reviewed, no
                alert · {overview.live.refused} need
                {overview.live.refused === 1 ? "s" : ""} an analyst
                {overview.live.paused ? ` · ${overview.live.paused} paused` : ""}
                {overview.live.failed ? ` · ${overview.live.failed} failed` : ""}
              </p>
              <details className="mb-3 text-xs text-slate-500">
                <summary className="cursor-pointer text-slate-400">What these outcomes mean</summary>
                <ul className="mt-2 list-disc space-y-1 pl-5">
                  <li>
                    <span className="text-slate-300">raised alert</span> — material debt change
                    found
                  </li>
                  <li>
                    <span className="text-slate-300">reviewed, no alert</span> — filing read; no
                    material change
                  </li>
                  <li>
                    <span className="text-slate-300">needs an analyst</span> — Kernel could not
                    confirm the change; open the document
                  </li>
                  <li>
                    <span className="text-slate-300">paused</span> — hit a turn or cost limit;
                    can resume
                  </li>
                </ul>
              </details>
              {(overview.live.documents?.length ?? 0) > 0 ? (
                <ul className="mb-4 divide-y divide-slate-800 border-t border-memo-ink">
                  {overview.live.documents.map((doc) => {
                    const outcome = liveOutcomeLabel(doc.outcome);
                    const body = (
                      <>
                        <div className="flex flex-wrap items-center gap-2 tabular-nums text-xs text-slate-400">
                          <span>{doc.issuer_name ?? doc.issuer_key ?? "—"}</span>
                          <span className="text-slate-500">{formatDate(doc.event_date)}</span>
                          <span className="rounded border border-accent/40 px-1.5 py-0.5 text-[10px] text-accent">
                            live
                          </span>
                          <span
                            className={
                              outcome === "raised alert"
                                ? "text-emerald-400"
                                : outcome === "paused" || outcome === "needs an analyst"
                                  ? "text-amber-400"
                                  : outcome === "failed"
                                    ? "text-rose-400"
                                    : "text-slate-400"
                            }
                          >
                            {outcome}
                          </span>
                        </div>
                        <p className="mt-1 text-sm text-slate-200">{doc.title}</p>
                        {doc.reason ? (
                          <p className="mt-1 tabular-nums text-xs text-amber-200/80">{doc.reason}</p>
                        ) : null}
                      </>
                    );
                    return (
                      <li key={doc.event_id}>
                        {doc.alert_id ? (
                          <Link
                            href={`/alerts/${doc.alert_id}`}
                            className="block px-4 py-3 hover:bg-ink-soft/70 focus-visible:bg-ink-soft focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-accent"
                          >
                            {body}
                          </Link>
                        ) : (
                          <div className="px-4 py-3">{body}</div>
                        )}
                      </li>
                    );
                  })}
                </ul>
              ) : overview.live.feed.length === 0 ? (
                <EmptyLine>No live documents yet.</EmptyLine>
              ) : null}
            </section>
          ) : null}

          <section aria-labelledby="eval-heading" className="mb-10 max-w-3xl border-t border-memo-rule pt-8">
            <h2 id="eval-heading" className="text-lg font-semibold text-memo-ink">
              How well does it work?
            </h2>
            {overview.comparison && overview.comparison.rows.length > 0 ? (
              <>
                <div className="mt-3 overflow-x-auto">
                  <table className="w-full min-w-[32rem] border-collapse text-left text-sm text-slate-300">
                    <thead>
                      <tr className="border-b border-slate-700 tabular-nums text-[11px] text-slate-500">
                        <th className="py-2 pr-3 font-medium"> </th>
                        <th className="py-2 pr-3 font-medium">Caught</th>
                        <th className="py-2 pr-3 font-medium">Right year and amount</th>
                        <th className="py-2 pr-3 font-medium">False alerts</th>
                        <th className="py-2 pr-3 font-medium" title="Numeric claims not grounded in the filing">
                          Ungrounded numbers
                        </th>
                        <th className="py-2 pr-3 font-medium" title="Stores a maturity wall and audit trail">
                          Keeps a wall
                        </th>
                        <th className="py-2 font-medium">Cost</th>
                      </tr>
                    </thead>
                    <tbody>
                      {overview.comparison.rows.map((row) => (
                        <tr key={row.kind ?? row.label} className="border-b border-slate-800/80">
                          <td className="py-2.5 pr-3 font-medium text-slate-200">{row.label}</td>
                          <td className="py-2.5 pr-3 tabular-nums text-xs">
                            {row.caught != null && row.expected_material != null
                              ? `${row.caught} of ${row.expected_material}`
                              : "—"}
                          </td>
                          <td className="py-2.5 pr-3 tabular-nums text-xs">
                            {row.accurate_caught != null && row.accurate_total != null
                              ? `${row.accurate_caught} of ${row.accurate_total}`
                              : "—"}
                          </td>
                          <td className="py-2.5 pr-3 tabular-nums text-xs">
                            {row.false_alert_count != null ? row.false_alert_count : "—"}
                          </td>
                          <td className="py-2.5 pr-3 tabular-nums text-xs">
                            {row.unsupported_claims != null ? row.unsupported_claims : "—"}
                          </td>
                          <td className="py-2.5 pr-3 tabular-nums text-xs">
                            {row.keeps_records === "yes"
                              ? "yes"
                              : row.keeps_records === "no"
                                ? "no"
                                : (row.keeps_records ?? "—")}
                          </td>
                          <td className="py-2.5 tabular-nums text-xs">
                            {row.cost_usd != null ? `$${row.cost_usd.toFixed(2)}` : "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="mt-3 tabular-nums text-[11px] text-slate-500">
                  Caught = material events flagged. Right year and amount = those flags with the
                  correct maturity year and size (±1%). Ungrounded numbers = claims the checker
                  could not find in the filing. Keeps a wall = stores before/after maturity records.
                </p>
                {overview.comparison.chart_crosscheck?.line ? (
                  <p className="mt-3 text-sm leading-relaxed text-slate-300">
                    {overview.comparison.chart_crosscheck.line}
                  </p>
                ) : null}
              </>
            ) : (
              <div className="mt-3 grid gap-4 sm:grid-cols-2">
                <EvalBlock
                  title="Kernel agent"
                  summary={overview.evaluation}
                  empty="Not tested yet."
                />
                <EvalBlock
                  title="One-shot model"
                  summary={overview.baseline}
                  empty="Baseline not run yet."
                />
              </div>
            )}
          </section>
        </>
      ) : null}
    </PageShell>
  );
}
