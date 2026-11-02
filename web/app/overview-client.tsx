"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ChangeArrow, FlagDot, formatTileValue } from "@/components/credit";
import { EmptyLine, ErrorLine, LoadingLine, PageShell } from "@/components/loading-error";
import {
  getJson,
  type CreditOverview,
  type MetricTile,
  type WatchlistRow,
} from "@/lib/api";
import { formatDate } from "@/lib/format";

const WATCH_METRICS = [
  "total_debt",
  "due_24m",
  "liquidity_coverage",
  "leverage",
  "interest_coverage",
] as const;

type SortKey = "name" | (typeof WATCH_METRICS)[number];

function tileValue(row: WatchlistRow, metric: string): number | null {
  const tile = row.metrics.find((t) => t.metric === metric);
  if (!tile || tile.value === null) return null;
  const n = Number(tile.value);
  return Number.isFinite(n) ? n : null;
}

function beforeAfterText(before: unknown, after: unknown): string | null {
  if (before === null || before === undefined || after === null || after === undefined) {
    return null;
  }
  return `${String(before)} → ${String(after)}`;
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
          what moved and what to watch. Every number links back to its source.
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
                    ? beforeAfterText(item.before_after.before, item.before_after.after)
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
                          {item.live ? (
                            <span className="rounded border border-accent/40 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-accent">
                              live
                            </span>
                          ) : null}
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
                                  <span title={tile.missing_reason ?? "No data yet"}>Missing</span>
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
                {overview.live.documents_read} documents read · {overview.live.alerted} alerts ·{" "}
                {overview.live.logged} logged · {overview.live.refused} refused
                {overview.live.failed ? ` · ${overview.live.failed} failed` : ""}
              </p>
              {overview.live.feed.length === 0 ? (
                <EmptyLine>No live alerts yet.</EmptyLine>
              ) : (
                <ul className="divide-y divide-slate-800 overflow-hidden rounded-lg border border-slate-700/60">
                  {overview.live.feed.map((item) => {
                    const change = item.before_after
                      ? beforeAfterText(item.before_after.before, item.before_after.after)
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
                            <span className="rounded border border-accent/40 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-accent">
                              live
                            </span>
                          </div>
                          <p className="mt-1 text-sm text-slate-200">
                            {item.impact_sentence ?? item.title}
                          </p>
                          {change ? (
                            <p className="mt-1 font-mono text-xs text-slate-400">
                              {item.before_after?.field ? `${item.before_after.field}: ` : null}
                              {change}
                            </p>
                          ) : null}
                        </Link>
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>
          ) : null}

          <section aria-labelledby="eval-heading" className="mb-10 max-w-3xl rounded-lg border border-slate-700/60 bg-ink-soft/40 p-5">
            <h2 id="eval-heading" className="text-sm font-semibold uppercase tracking-wide text-slate-400">
              How well does it work?
            </h2>
            <p className="mt-2 text-sm leading-relaxed text-slate-300">
              {overview.evaluation ? overview.evaluation.text : "Not tested yet."}
            </p>
            {overview.evaluation &&
            (overview.evaluation.precision != null || overview.evaluation.recall != null) ? (
              <p className="mt-1 font-mono text-xs text-slate-500">
                precision{" "}
                {overview.evaluation.precision != null
                  ? overview.evaluation.precision.toFixed(3)
                  : "n/a"}{" "}
                · recall{" "}
                {overview.evaluation.recall != null
                  ? overview.evaluation.recall.toFixed(3)
                  : "n/a"}
              </p>
            ) : null}
            {overview.evaluation &&
            ((overview.evaluation.missed_events?.length ?? 0) > 0 ||
              (overview.evaluation.false_events?.length ?? 0) > 0) ? (
              <details className="mt-3 text-xs text-slate-400">
                <summary className="cursor-pointer text-slate-300">Details</summary>
                {(overview.evaluation.missed_events?.length ?? 0) > 0 ? (
                  <div className="mt-2">
                    <p className="font-medium text-slate-300">Missed</p>
                    <ul className="mt-1 space-y-0.5 font-mono text-[11px]">
                      {overview.evaluation.missed_events?.map((e) => (
                        <li key={`m-${e.name}`}>
                          {e.date} · {e.name}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
                {(overview.evaluation.false_events?.length ?? 0) > 0 ? (
                  <div className="mt-2">
                    <p className="font-medium text-slate-300">False alerts</p>
                    <ul className="mt-1 space-y-0.5 font-mono text-[11px]">
                      {overview.evaluation.false_events?.map((e) => (
                        <li key={`f-${e.name}`}>
                          {e.date} · {e.name}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </details>
            ) : null}
          </section>
        </>
      ) : null}
    </PageShell>
  );
}
