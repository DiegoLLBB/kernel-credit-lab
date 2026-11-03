"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ChangeArrow, FlagDot, formatTileValue } from "@/components/credit";
import { ReplayVerdictBadge } from "@/components/replay-verdict-badge";
import { EmptyLine, ErrorLine, LoadingLine, PageShell } from "@/components/loading-error";
import {
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
      <p className="font-mono text-[11px] uppercase tracking-wide text-slate-500">{title}</p>
      <p className="mt-1 text-sm leading-relaxed text-slate-300">
        {summary ? summary.text : empty}
      </p>
      {summary && (summary.precision != null || summary.recall != null) ? (
        <p className="mt-1 font-mono text-xs text-slate-500">
          share of alerts that were real{" "}
          {summary.precision != null ? summary.precision.toFixed(3) : "n/a"} · share of real
          events caught {summary.recall != null ? summary.recall.toFixed(3) : "n/a"}
          {summary.unsupported_claims != null
            ? ` · numbers not backed by the filing ${summary.unsupported_claims}`
            : ""}
        </p>
      ) : summary && summary.unsupported_claims != null ? (
        <p className="mt-1 font-mono text-xs text-slate-500">
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
              <ul className="mt-1 space-y-0.5 font-mono text-[11px]">
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
              <ul className="mt-1 space-y-0.5 font-mono text-[11px]">
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
    ? `Monitoring ${header.companies} ${header.companies === 1 ? "company" : "companies"} · ${header.filings_read} filings read · ${header.material_changes} material ${header.material_changes === 1 ? "change" : "changes"} found · last check ${formatDate(header.last_check)}`
    : null;

  const metricLabel = (metric: string): string =>
    overview?.watchlist
      .flatMap((r) => r.metrics)
      .find((t) => t.metric === metric)?.label ?? metric;

  return (
    <PageShell title="What changed">
      <header className="mb-10 max-w-3xl">
        <p className="font-mono text-xs uppercase tracking-[0.2em] text-accent">Kernel Credit Lab</p>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight text-white sm:text-4xl">
          What changed
        </h1>
        <p className="mt-4 text-base leading-relaxed text-slate-400">
          Debt, cash and earnings straight from company filings — with a plain-English summary of
          what moved and what to watch. Brief lines and alert citations link to their sources;
          watchlist figures open the issuer page.
        </p>
        {headerLine ? (
          <p className="mt-3 font-mono text-xs text-slate-300" aria-live="polite">
            {headerLine}
          </p>
        ) : null}
      </header>

      {error ? <ErrorLine message={`API unreachable: ${error}`} /> : null}
      {!error && !overview ? <LoadingLine label="Loading dashboard…" /> : null}

      {overview ? (
        <>
          {overview.brief && overview.brief.sentences.length > 0 ? (
            <section
              aria-labelledby="brief-heading"
              className="mb-10 max-w-3xl rounded-lg border border-slate-700/60 bg-ink-soft/40 p-5"
            >
              <h2
                id="brief-heading"
                className="text-sm font-semibold uppercase tracking-wide text-slate-400"
              >
                Today&apos;s brief · {formatDate(overview.brief.as_of)}
              </h2>
              <ul className="mt-3 space-y-3">
                {overview.brief.sentences.map((item, i) => (
                  <li key={`brief-${i}`} className="text-sm leading-relaxed text-slate-200">
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
              <p className="mt-4 font-mono text-[11px] text-slate-500">
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
                className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400"
              >
                Today&apos;s brief
              </h2>
              <EmptyLine>
                No brief yet — written after each live run (and once for the replay&apos;s last day).
              </EmptyLine>
            </section>
          )}

          <section aria-labelledby="feed-heading" className="mb-10">
            <h2 id="feed-heading" className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">
              Material changes, newest first
            </h2>
            {overview.feed.length === 0 ? (
              <EmptyLine>No material changes found yet.</EmptyLine>
            ) : (
              <ul className="divide-y divide-slate-800 overflow-hidden rounded-lg border border-slate-700/60">
                {overview.feed.map((item) => {
                  const change = item.before_after
                    ? formatBeforeAfter(
                        item.before_after.before,
                        item.before_after.after,
                        item.before_after.field,
                        {
                          before: item.before_after.before_display,
                          after: item.before_after.after_display,
                        },
                      )
                    : null;
                  return (
                    <li key={item.alert_id}>
                      <Link
                        href={`/alerts/${item.alert_id}`}
                        className="block px-4 py-3 hover:bg-ink-soft/70 focus-visible:bg-ink-soft focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-accent"
                      >
                        <div className="flex flex-wrap items-center gap-2 font-mono text-xs text-slate-400">
                          <span>{item.issuer.name}</span>
                          <span className="text-slate-500">{formatDate(item.event_date)}</span>
                          {item.kind === "metric" ? (
                            <span className="rounded border border-sky-500/40 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-sky-300">
                              metric
                            </span>
                          ) : null}
                          {item.live ? (
                            <span className="rounded border border-accent/40 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-accent">
                              live
                            </span>
                          ) : null}
                          {item.kind === "metric" ? null : (
                            <ReplayVerdictBadge
                              verdict={item.replay_verdict}
                              reason={item.replay_verdict_reason}
                            />
                          )}
                        </div>
                        <p className="mt-1 text-sm text-slate-200">
                          {item.impact_sentence ?? item.title}
                        </p>
                        {item.impact_pending ? (
                          <p className="mt-1 text-xs text-slate-500">
                            Analyst summary still being written.
                          </p>
                        ) : null}
                        {change ? (
                          <p className="mt-1 font-mono text-xs text-slate-400">
                            {item.before_after?.field ? `${item.before_after.field}: ` : null}
                            {change}
                          </p>
                        ) : null}
                        {item.kind === "metric" && item.filing_url ? (
                          <p className="mt-1 font-mono text-[11px] text-sky-400/90">
                            Filing linked
                          </p>
                        ) : null}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          <section aria-labelledby="watchlist-heading" className="mb-10">
            <h2 id="watchlist-heading" className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">
              Watchlist
            </h2>
            <div className="overflow-x-auto rounded-lg border border-slate-700/60">
              <table className="min-w-full text-left text-sm">
                <thead className="bg-ink-soft font-mono text-[11px] uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="px-3 py-2">
                      <button type="button" onClick={() => toggleSort("name")} className="focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
                        Company{sortKey === "name" ? (sortAsc ? " ↑" : " ↓") : ""}
                      </button>
                    </th>
                    <th className="px-3 py-2 font-mono text-[11px] uppercase tracking-wide text-slate-500">
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
                          <div className="font-mono text-[11px] text-slate-500">
                            {row.ticker ?? key}
                          </div>
                        </td>
                        <td className="px-3 py-2 font-mono text-xs text-slate-400">
                          {row.sector ?? "—"}
                        </td>
                        {WATCH_METRICS.map((m) => {
                          const tile: MetricTile | undefined = row.metrics.find(
                            (t) => t.metric === m,
                          );
                          if (!tile) return <td key={m} className="px-3 py-2 font-mono text-xs text-slate-500">—</td>;
                          return (
                            <td key={m} className="px-3 py-2">
                              <span className="flex items-center gap-1.5 font-mono text-xs text-slate-200">
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
                <ul className="mt-2 space-y-1 font-mono text-[11px] text-slate-500">
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
              <h2 id="live-heading" className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">
                Live since {formatDate(overview.live.since.slice(0, 10))}
              </h2>
              <p className="mb-3 font-mono text-xs text-slate-400">
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
                <ul className="mb-4 divide-y divide-slate-800 overflow-hidden rounded-lg border border-slate-700/60">
                  {overview.live.documents.map((doc) => {
                    const outcome = liveOutcomeLabel(doc.outcome);
                    const body = (
                      <>
                        <div className="flex flex-wrap items-center gap-2 font-mono text-xs text-slate-400">
                          <span>{doc.issuer_name ?? doc.issuer_key ?? "—"}</span>
                          <span className="text-slate-500">{formatDate(doc.event_date)}</span>
                          <span className="rounded border border-accent/40 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-accent">
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
                          <p className="mt-1 font-mono text-xs text-amber-200/80">{doc.reason}</p>
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

          <section aria-labelledby="eval-heading" className="mb-10 max-w-3xl rounded-lg border border-slate-700/60 bg-ink-soft/40 p-5">
            <h2 id="eval-heading" className="text-sm font-semibold uppercase tracking-wide text-slate-400">
              How well does it work?
            </h2>
            {overview.comparison && overview.comparison.rows.length > 0 ? (
              <>
                <div className="mt-3 overflow-x-auto">
                  <table className="w-full min-w-[32rem] border-collapse text-left text-sm text-slate-300">
                    <thead>
                      <tr className="border-b border-slate-700 font-mono text-[11px] uppercase tracking-wide text-slate-500">
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
                          <td className="py-2.5 pr-3 font-mono text-xs">
                            {row.caught != null && row.expected_material != null
                              ? `${row.caught} of ${row.expected_material}`
                              : "—"}
                          </td>
                          <td className="py-2.5 pr-3 font-mono text-xs">
                            {row.accurate_caught != null && row.accurate_total != null
                              ? `${row.accurate_caught} of ${row.accurate_total}`
                              : "—"}
                          </td>
                          <td className="py-2.5 pr-3 font-mono text-xs">
                            {row.false_alert_count != null ? row.false_alert_count : "—"}
                          </td>
                          <td className="py-2.5 pr-3 font-mono text-xs">
                            {row.unsupported_claims != null ? row.unsupported_claims : "—"}
                          </td>
                          <td className="py-2.5 pr-3 font-mono text-xs">
                            {row.keeps_records === "yes"
                              ? "yes"
                              : row.keeps_records === "no"
                                ? "no"
                                : (row.keeps_records ?? "—")}
                          </td>
                          <td className="py-2.5 font-mono text-xs">
                            {row.cost_usd != null ? `$${row.cost_usd.toFixed(2)}` : "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="mt-3 font-mono text-[11px] text-slate-500">
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
