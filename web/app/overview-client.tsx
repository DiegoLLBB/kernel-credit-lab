"use client";

import { SectionBoundary } from "@/components/section-boundary";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ChangeArrow, FlagDot, formatTileValue } from "@/components/credit";
import { ComingDue } from "@/components/coming-due";
import { FeaturedChange } from "@/components/featured-change";
import { ReplayVerdictBadge } from "@/components/replay-verdict-badge";
import { EmptyLine, ErrorLine, PageShell } from "@/components/loading-error";
import { PageSkeleton } from "@/components/skeleton";
import {
  API_URL,
  getJson,
  type BriefSentence,
  type CreditOverview,
  type EvalComparisonRow,
  type EvalSummary,
  type MetricTile,
  type WatchlistRow,
} from "@/lib/api";
import { formatBeforeAfter, formatDate, formatUsdDisplay, liveOutcomeLabel } from "@/lib/format";

const WATCH_COLUMNS = [
  { key: "total_debt", label: "Total debt", help: "Short- and long-term debt from the latest filing" },
  { key: "due_24m", label: "Due in 24 months", help: "Debt maturing in the next two years, from the 10-K schedule" },
  {
    key: "liquidity",
    label: "Liquidity cover",
    help: "Cash plus undrawn credit lines, divided by debt due in 24 months",
  },
  { key: "leverage", label: "Leverage", help: "Total debt divided by EBITDA (last 12 months)" },
  { key: "interest_coverage", label: "Interest cover", help: "EBIT divided by interest expense" },
] as const;

type SortKey = "name" | (typeof WATCH_COLUMNS)[number]["key"];

/** The tile behind a column; liquidity prefers cash + credit lines and falls back to cash only. */
function pickTile(row: WatchlistRow, key: string): { tile: MetricTile | undefined; cashOnly: boolean } {
  if (key !== "liquidity") return { tile: row.metrics.find((t) => t.metric === key), cashOnly: false };
  const total = row.metrics.find((t) => t.metric === "liquidity_coverage_total");
  if (total && total.value !== null) return { tile: total, cashOnly: false };
  const cash = row.metrics.find((t) => t.metric === "liquidity_coverage");
  return { tile: cash ?? total, cashOnly: Boolean(cash && cash.value !== null) };
}

/** One plain sentence from the comparison table; null when the rows don't support it. */
function resultsSentence(rows: EvalComparisonRow[]): string | null {
  const agent = rows.find((r) => r.kind === "agent");
  const base = rows.find((r) => r.kind === "baseline");
  if (!agent || agent.caught == null || agent.expected_material == null || agent.accurate_caught == null)
    return null;
  let out = `Replaying ${agent.expected_material} real debt changes from 2026, Kernel caught ${agent.caught} and got the year and amount right on ${agent.accurate_caught}.`;
  if (base && base.accurate_caught != null) {
    out += ` A one-shot model reading the same filings got ${base.accurate_caught} right${base.keeps_records === "yes" ? "" : " and keeps no records"}.`;
  }
  return out;
}

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

/** Right-hand column of a feed row: the year and the signed size of the move. */
function FeedDelta({ item }: { item: CreditOverview["feed"][number] }) {
  const ba = item.before_after;
  if (!ba || ba.before == null || ba.after == null || item.kind === "metric") return <span aria-hidden />;
  const b = Number(ba.before);
  const a = Number(ba.after);
  if (!Number.isFinite(a) || !Number.isFinite(b) || a === b) return <span aria-hidden />;
  const d = a - b;
  const year = ba.field ? ba.field.replace(/^FY/, "").replace(/^after /, "after ") : "";
  return (
    <span className="text-left sm:text-right">
      <span className={`block text-lg font-semibold ${d > 0 ? "text-memo-added" : "text-memo-reduced"}`}>
        {d > 0 ? "+" : "−"}
        {formatUsdDisplay(Math.abs(d))}
      </span>
      {year ? <span className="block text-xs text-memo-muted">due {year}</span> : null}
    </span>
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
  const tile = pickTile(row, metric).tile;
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
    const baBefore = Number(item.before_after?.before);
    const baAfter = Number(item.before_after?.after);
    const change = item.before_after
      ? item.kind !== "metric" &&
        item.before_after.before != null &&
        item.before_after.after != null &&
        Number.isFinite(baBefore) &&
        Number.isFinite(baAfter)
        ? `${formatUsdDisplay(baBefore)} → ${formatUsdDisplay(baAfter)}`
        : formatBeforeAfter(item.before_after.before, item.before_after.after, item.before_after.field, {
          before: item.before_after.before_display,
          after: item.before_after.after_display,
        })
      : null;
    return (
      <li key={item.alert_id} className="border-b border-memo-rule">
        <Link
          href={`/alerts/${item.alert_id}`}
          className="grid grid-cols-1 gap-x-6 gap-y-1 py-4 hover:bg-[#EEF0EC] sm:grid-cols-[7.5rem_minmax(0,1fr)_9rem]"
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
          <FeedDelta item={item} />
        </Link>
      </li>
    );
  }

  return (
    <PageShell title="What changed">
      <header className="mb-12 grid gap-x-12 gap-y-6 md:grid-cols-[minmax(0,1fr)_16rem] md:items-end">
        <div>
          <h1 className="text-3xl font-semibold text-memo-ink sm:text-[46px] sm:leading-[52px]">
            What changed
          </h1>
          <p className="mt-4 max-w-[58ch] text-lg text-memo-ink">
            Kernel reads company filings as they are published and tells you when a company&apos;s
            debt picture changes. Every number links to the sentence in the filing it came from.
          </p>
        </div>
        {header ? (
          <dl className="border-t border-memo-ink text-sm" aria-live="polite" aria-label={headerLine ?? undefined}>
            {[
              ["Last checked", formatDate(header.last_check)],
              ["Companies watched", String(header.companies)],
              ["Filings read", String(header.filings_read)],
              ["Material changes", String(header.material_changes)],
            ].map(([k, v]) => (
              <div key={k} className="flex items-baseline justify-between border-b border-memo-rule py-1.5">
                <dt className="text-memo-muted">{k}</dt>
                <dd className="font-medium text-memo-ink">{v}</dd>
              </div>
            ))}
          </dl>
        ) : null}
      </header>

      {error ? <ErrorLine message={`API unreachable: ${error}`} /> : null}
      {!error && !overview ? <PageSkeleton kind="overview" /> : null}

      {overview ? (
        <>
          <SectionBoundary name="latest change">
          {agentFeed[0] ? <FeaturedChange alertId={agentFeed[0].alert_id} /> : null}
          </SectionBoundary>

          <div className="mb-16 grid gap-x-12 gap-y-10 md:grid-cols-[minmax(0,7fr)_minmax(0,4fr)]">
            {overview.brief && overview.brief.sentences.length > 0 ? (
              <section aria-labelledby="brief-heading" className="border-l-[3px] border-memo-ink pl-5">
                <h2 id="brief-heading" className="text-lg font-semibold text-memo-ink">
                  Today&apos;s brief, {formatDate(overview.brief.as_of)}
                </h2>
                <ul className="mt-3 space-y-3">
                  {overview.brief.sentences.map((item, i) => (
                    <li key={`brief-${i}`} className="text-[17px] leading-7 text-memo-ink">
                      <BriefSentenceText sentence={item.sentence} refs={item.refs} />
                    </li>
                  ))}
                </ul>
                {(overview.brief.refused?.length ?? 0) > 0 ? (
                  <details className="mt-4 text-sm text-memo-muted">
                    <summary className="cursor-pointer">
                      {overview.brief.refused_count} sentence
                      {overview.brief.refused_count === 1 ? " was" : "s were"} held back by the check
                    </summary>
                    <ul className="mt-2 space-y-2">
                      {overview.brief.refused!.map((r, i) => (
                        <li key={`refused-${i}`}>
                          {r.sentence ? <span className="line-through">{r.sentence}</span> : null}
                          {r.reason ? <span className="mt-0.5 block text-memo-watch">Why: {r.reason}</span> : null}
                        </li>
                      ))}
                    </ul>
                  </details>
                ) : null}
                <p className="mt-4 text-xs text-memo-muted">
                  {overview.brief.attribution}
                  {overview.brief.cost_label && !overview.brief.attribution.includes(overview.brief.cost_label)
                    ? `, ${overview.brief.cost_label}`
                    : null}
                </p>
              </section>
            ) : (
              <section aria-labelledby="brief-empty" className="border-l-[3px] border-memo-rule pl-5">
                <h2 id="brief-empty" className="text-lg font-semibold text-memo-ink">
                  Today&apos;s brief
                </h2>
                <p className="mt-2 text-sm text-memo-muted">
                  Written after each live run. The next one will appear here.
                </p>
              </section>
            )}

            {overview.live ? (
              <SectionBoundary name="live monitoring">
                <section aria-labelledby="live-heading">
                  <h2 id="live-heading" className="text-lg font-semibold text-memo-ink">
                    Live since {formatDate(overview.live.since.slice(0, 10))}
                  </h2>
                  <p className="mt-1 text-sm text-memo-muted">
                    New SEC filings are checked twice a day, at 07:00 and 19:00 Madrid time.
                  </p>
                  <dl className="mt-3 border-t border-memo-ink text-sm">
                    {(
                      [
                        ["Documents read", overview.live.documents_read, null],
                        ["Raised an alert", overview.live.alerted, "text-memo-added"],
                        ["Read, nothing material", overview.live.logged, null],
                        ["Left for an analyst", overview.live.refused, overview.live.refused ? "text-memo-watch" : null],
                        ["Paused at a limit", overview.live.paused, overview.live.paused ? "text-memo-watch" : null],
                        ["Failed", overview.live.failed, overview.live.failed ? "text-memo-added" : null],
                      ] as [string, number, string | null][]
                    )
                      .filter(([k, v]) => v > 0 || k === "Documents read" || k === "Raised an alert")
                      .map(([k, v, cls]) => (
                        <div key={k} className="flex items-baseline justify-between border-b border-memo-rule py-1.5">
                          <dt className="text-memo-muted">{k}</dt>
                          <dd className={`font-medium ${cls ?? "text-memo-ink"}`}>{v}</dd>
                        </div>
                      ))}
                  </dl>
                  {(overview.live.documents?.length ?? 0) > 0 ? (
                    <details className="mt-3 text-sm">
                      <summary className="cursor-pointer text-memo-info underline underline-offset-2">
                        Every document read live ({overview.live.documents.length})
                      </summary>
                      <ul className="mt-2">
                        {overview.live.documents.map((doc) => {
                          const outcome = liveOutcomeLabel(doc.outcome);
                          const tone =
                            outcome === "raised alert"
                              ? "text-memo-added"
                              : outcome === "paused" || outcome === "needs an analyst"
                                ? "text-memo-watch"
                                : outcome === "failed"
                                  ? "text-memo-added"
                                  : "text-memo-muted";
                          const body = (
                            <>
                              <span className="block text-memo-ink">
                                {doc.issuer_name ?? doc.issuer_key ?? "Unknown company"}
                                <span className="text-memo-muted">, {formatDate(doc.event_date)}</span>
                              </span>
                              <span className="block text-xs text-memo-muted">{doc.title}</span>
                              <span className={`block text-xs ${tone}`}>
                                {outcome}
                                {doc.reason ? `: ${doc.reason}` : ""}
                              </span>
                            </>
                          );
                          return (
                            <li key={doc.event_id} className="border-b border-memo-rule">
                              {doc.alert_id ? (
                                <Link href={`/alerts/${doc.alert_id}`} className="block py-2 hover:bg-[#EEF0EC]">
                                  {body}
                                </Link>
                              ) : (
                                <div className="py-2">{body}</div>
                              )}
                            </li>
                          );
                        })}
                      </ul>
                    </details>
                  ) : null}
                </section>
              </SectionBoundary>
            ) : null}
          </div>

          <SectionBoundary name="agent feed">
          <section aria-labelledby="feed-heading" className="mb-16">
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
                  Ratio and maturity moves computed from the filings ({metricFeed.length})
                </summary>
                <p className="mt-2 max-w-[70ch] text-sm text-memo-muted">
                  Raised in code when a filing moves a ratio across a band or lifts debt due within a
                  year. Not part of the agent&apos;s score.
                </p>
                <ol className="mt-3 border-t border-memo-rule">{metricFeed.map(renderFeedItem)}</ol>
              </details>
            ) : null}
          </section>
          </SectionBoundary>

          <SectionBoundary name="coming due">
          <ComingDue />
          </SectionBoundary>

          <SectionBoundary name="watchlist">
          <section aria-labelledby="watchlist-heading" className="mb-16">
            <div className="flex flex-wrap items-baseline justify-between gap-3">
              <h2 id="watchlist-heading" className="text-lg font-semibold text-memo-ink">
                Watchlist
              </h2>
              <a href={`${API_URL}/api/watchlist.csv`} className="text-sm text-memo-info underline underline-offset-2">
                Download as CSV
              </a>
            </div>
            <p className="mt-1 max-w-[70ch] text-sm text-memo-muted">
              Latest figures from each company&apos;s SEC filings. Under each figure, the change from a year
              earlier. Click a column to sort.
            </p>
            <div className="mt-3 overflow-x-auto border-t border-memo-ink">
              <table className="min-w-full border-collapse text-left text-sm">
                <thead>
                  <tr className="border-b border-memo-ink text-xs text-memo-muted">
                    <th scope="col" className="py-2 pr-4 font-medium">
                      <button type="button" onClick={() => toggleSort("name")} aria-label="Sort by company">
                        Company{sortKey === "name" ? (sortAsc ? " ↑" : " ↓") : ""}
                      </button>
                    </th>
                    <th scope="col" className="py-2 pr-4 font-medium">Sector</th>
                    {WATCH_COLUMNS.map((c) => (
                      <th key={c.key} scope="col" className="py-2 pl-4 text-right font-medium" title={c.help}>
                        <button type="button" onClick={() => toggleSort(c.key)} aria-label={`Sort by ${c.label}`}>
                          {c.label}
                          {sortKey === c.key ? (sortAsc ? " ↑" : " ↓") : ""}
                        </button>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => {
                    const key = row.issuer.key ?? row.issuer.id;
                    return (
                      <tr key={row.issuer.id} className="border-b border-memo-rule align-top hover:bg-[#EEF0EC]">
                        <td className="py-3 pr-4">
                          <Link
                            href={`/issuers/${encodeURIComponent(key)}`}
                            className="font-medium text-memo-ink underline-offset-2 hover:underline"
                          >
                            {row.issuer.name}
                          </Link>
                          <div className="text-xs text-memo-muted">{row.ticker ?? key}</div>
                        </td>
                        <td className="py-3 pr-4 text-memo-muted">{row.sector ?? ""}</td>
                        {WATCH_COLUMNS.map((c) => {
                          const { tile, cashOnly } = pickTile(row, c.key);
                          if (!tile) {
                            return (
                              <td key={c.key} className="py-3 pl-4 text-right text-memo-muted">
                                –
                              </td>
                            );
                          }
                          return (
                            <td key={c.key} className="whitespace-nowrap py-3 pl-4 text-right">
                              <span className="inline-flex items-center gap-1.5 text-[15px] font-medium text-memo-ink">
                                <FlagDot flag={tile.flag} />
                                {tile.value === null ? (
                                  <span className="text-sm font-normal text-memo-muted" title={tile.missing_reason ?? "No data yet"}>
                                    {tile.missing_reason === "not meaningful for banks" ? "n/a for banks" : "no data"}
                                  </span>
                                ) : (
                                  formatTileValue(tile)
                                )}
                              </span>
                              <div className="text-xs text-memo-muted">
                                {cashOnly ? "cash only" : <ChangeArrow tile={tile} />}
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
            <details className="mt-3 max-w-3xl text-sm text-memo-muted">
              <summary className="cursor-pointer">How to read this table</summary>
              <p className="mt-2">
                Dots are screening bands, not ratings: green comfortable, amber worth watching, red
                stretched. Ratios show last year → now; dollar figures show the percent move. Liquidity
                cover is cash plus undrawn credit lines against debt due in 24 months; where a company&apos;s
                credit lines aren&apos;t known it is cash only and says so.
              </p>
              {overview.thresholds.length > 0 ? (
                <ul className="mt-2 space-y-1 text-xs">
                  {overview.thresholds.map((t) => (
                    <li key={t.label}>
                      {t.label} ({t.unit}):{" "}
                      {Object.entries(t.bands)
                        .map(([k, v]) => `${k} ${String(v)}`)
                        .join(", ")}
                    </li>
                  ))}
                </ul>
              ) : null}
            </details>
          </section>
          </SectionBoundary>

          <SectionBoundary name="results">
          <section aria-labelledby="eval-heading" className="mb-10 border-t-[3px] border-memo-ink pt-6">
            <h2 id="eval-heading" className="text-lg font-semibold text-memo-ink">
              How well does it work?
            </h2>
            {overview.comparison && overview.comparison.rows.length > 0 ? (
              <>
                {resultsSentence(overview.comparison.rows) ? (
                  <p className="mt-2 max-w-[62ch] text-xl leading-8 text-memo-ink">
                    {resultsSentence(overview.comparison.rows)}
                  </p>
                ) : null}
                <div className="mt-5 overflow-x-auto">
                  <table className="w-full min-w-[40rem] border-collapse text-left text-sm">
                    <thead>
                      <tr className="border-b border-memo-ink text-xs text-memo-muted">
                        <th scope="col" className="py-2 pr-4 font-medium">Tested on 2026 filings</th>
                        <th scope="col" className="py-2 pl-4 text-right font-medium">Caught</th>
                        <th scope="col" className="py-2 pl-4 text-right font-medium">Right year and amount</th>
                        <th scope="col" className="py-2 pl-4 text-right font-medium">False alerts</th>
                        <th scope="col" className="py-2 pl-4 text-right font-medium" title="Numeric claims the checker could not find in the filing">
                          Numbers not in the filing
                        </th>
                        <th scope="col" className="py-2 pl-4 text-right font-medium">Keeps records</th>
                        <th scope="col" className="py-2 pl-4 text-right font-medium">Cost</th>
                      </tr>
                    </thead>
                    <tbody>
                      {overview.comparison.rows.map((row) => (
                        <tr key={row.kind ?? row.label} className="border-b border-memo-rule">
                          <th scope="row" className="py-3 pr-4 text-left font-medium text-memo-ink">{row.label}</th>
                          <td className="py-3 pl-4 text-right text-memo-ink">
                            {row.caught != null && row.expected_material != null
                              ? `${row.caught} of ${row.expected_material}`
                              : "–"}
                          </td>
                          <td className="py-3 pl-4 text-right font-semibold text-memo-ink">
                            {row.accurate_caught != null && row.accurate_total != null
                              ? `${row.accurate_caught} of ${row.accurate_total}`
                              : "–"}
                          </td>
                          <td className="py-3 pl-4 text-right text-memo-ink">{row.false_alert_count ?? "–"}</td>
                          <td className="py-3 pl-4 text-right text-memo-ink">{row.unsupported_claims ?? "–"}</td>
                          <td className="py-3 pl-4 text-right text-memo-ink">
                            {row.keeps_records === "yes" ? "yes" : "no"}
                          </td>
                          <td className="py-3 pl-4 text-right text-memo-ink">
                            {row.cost_usd != null ? `$${row.cost_usd.toFixed(2)}` : "–"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="mt-3 max-w-[80ch] text-xs text-memo-muted">
                  Caught: real material changes flagged. Right year and amount: flags with the correct
                  maturity year and size (within 1%). Each replayed alert in the feed shows its result.
                </p>
                {(overview.evaluation?.missed_events?.length ?? 0) > 0 ||
                (overview.evaluation?.false_events?.length ?? 0) > 0 ? (
                  <details className="mt-3 text-sm text-memo-muted">
                    <summary className="cursor-pointer text-memo-info underline underline-offset-2">
                      What Kernel missed or got wrong
                    </summary>
                    <ul className="mt-2 border-t border-memo-rule">
                      {overview.evaluation?.missed_events?.map((e) => (
                        <li key={`miss-${e.name}`} className="border-b border-memo-rule py-1.5">
                          <span className="text-memo-ink">Missed:</span> {e.name.replace(/_/g, " ")}, {formatDate(e.date)}
                        </li>
                      ))}
                      {overview.evaluation?.false_events?.map((e) => (
                        <li key={`false-${e.name}`} className="border-b border-memo-rule py-1.5">
                          <span className="text-memo-ink">False alert:</span> {e.name.replace(/_/g, " ")}, {formatDate(e.date)}
                        </li>
                      ))}
                    </ul>
                  </details>
                ) : null}
                {overview.comparison.chart_crosscheck?.line ? (
                  <p className="mt-3 max-w-[70ch] text-sm text-memo-ink">
                    {overview.comparison.chart_crosscheck.line}
                  </p>
                ) : null}
              </>
            ) : (
              <div className="mt-3 grid gap-6 sm:grid-cols-2">
                <EvalBlock title="Kernel agent" summary={overview.evaluation} empty="Not tested yet." />
                <EvalBlock title="One-shot model" summary={overview.baseline} empty="Baseline not run yet." />
              </div>
            )}
          </section>
          </SectionBoundary>
        </>
      ) : null}
    </PageShell>
  );
}
