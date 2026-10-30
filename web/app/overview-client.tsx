"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { AlertStatusChip, MaterialBadge } from "@/components/status-chip";
import { EmptyLine, ErrorLine, LoadingLine, PageShell } from "@/components/loading-error";
import {
  getJson,
  type AlertListOut,
  type IssuerListOut,
  type Readiness,
  type ReplayStatusOut,
} from "@/lib/api";
import { formatCost, formatDate, formatPct } from "@/lib/format";

export function OverviewClient() {
  const [replay, setReplay] = useState<ReplayStatusOut | null>(null);
  const [alerts, setAlerts] = useState<AlertListOut | null>(null);
  const [issuers, setIssuers] = useState<IssuerListOut | null>(null);
  const [ready, setReady] = useState<Readiness | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      getJson<ReplayStatusOut>("/api/replay"),
      getJson<AlertListOut>("/api/alerts?limit=8&offset=0"),
      getJson<IssuerListOut>("/api/issuers?limit=50"),
      getJson<Readiness>("/health/ready"),
    ])
      .then(([r, a, i, h]) => {
        if (cancelled) return;
        setReplay(r);
        setAlerts(a);
        setIssuers(i);
        setReady(h);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const alertCounts = useMemo(() => {
    const map = new Map<string, number>();
    for (const a of alerts?.items ?? []) {
      const key = a.issuer.key ?? a.issuer.id;
      map.set(key, (map.get(key) ?? 0) + 1);
    }
    return map;
  }, [alerts]);

  // Watchlist counts need full alert list — fetch all for counting when issuers load.
  const [allAlerts, setAllAlerts] = useState<AlertListOut | null>(null);
  useEffect(() => {
    let cancelled = false;
    getJson<AlertListOut>("/api/alerts?limit=200&offset=0")
      .then((a) => {
        if (!cancelled) setAllAlerts(a);
      })
      .catch(() => {
        /* overview still works with latest-8 counts */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const watchCounts = useMemo(() => {
    const map = new Map<string, number>();
    const source = allAlerts?.items ?? alerts?.items ?? [];
    for (const a of source) {
      const key = a.issuer.key ?? a.issuer.id;
      map.set(key, (map.get(key) ?? 0) + 1);
    }
    return map;
  }, [allAlerts, alerts]);

  return (
    <PageShell title="Overview">
      <header className="mb-10 max-w-3xl">
        <p className="font-mono text-xs uppercase tracking-[0.2em] text-accent">Kernel Credit Lab</p>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight text-white sm:text-4xl">
          Credit monitoring you can audit.
        </h1>
        <p className="mt-4 text-base leading-relaxed text-slate-400">
          Kernel watches USD investment-grade issuers and alerts when their debt maturity wall
          changes. Every alert carries the exact quotes, the recomputed wall and an independent
          model&apos;s check.
        </p>
      </header>

      {error ? <ErrorLine message={`API unreachable: ${error}`} /> : null}
      {!error && !replay ? <LoadingLine label="Loading dashboard…" /> : null}

      {replay ? (
        <section aria-labelledby="replay-heading" className="mb-10 rounded-lg border border-slate-700/60 bg-ink-soft p-5">
          <h2 id="replay-heading" className="text-sm font-semibold uppercase tracking-wide text-slate-400">
            Replay
          </h2>
          <dl className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <dt className="text-xs text-slate-500">Window</dt>
              <dd className="mt-1 font-mono text-sm text-slate-200">
                {formatDate(replay.window_from)} – {formatDate(replay.window_until)}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-slate-500">Events by status</dt>
              <dd className="mt-1 space-y-0.5 font-mono text-sm text-slate-200">
                {Object.keys(replay.event_counts_by_status).length === 0 ? (
                  <span className="text-slate-400">None</span>
                ) : (
                  Object.entries(replay.event_counts_by_status).map(([status, count]) => (
                    <div key={status}>
                      {status}: {count}
                    </div>
                  ))
                )}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-slate-500">Precision / recall</dt>
              <dd className="mt-1 font-mono text-sm text-slate-200">
                {replay.latest_eval ? (
                  <>
                    {formatPct(replay.latest_eval.precision)} / {formatPct(replay.latest_eval.recall)}
                  </>
                ) : (
                  <span className="text-slate-400">not evaluated yet</span>
                )}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-slate-500">Eval cost</dt>
              <dd className="mt-1 font-mono text-sm text-slate-200">
                {replay.latest_eval ? formatCost(replay.latest_eval.cost_usd) : (
                  <span className="text-slate-400">not evaluated yet</span>
                )}
              </dd>
            </div>
          </dl>
        </section>
      ) : null}

      <section aria-labelledby="alerts-heading" className="mb-10">
        <div className="mb-3 flex items-baseline justify-between gap-3">
          <h2 id="alerts-heading" className="text-sm font-semibold uppercase tracking-wide text-slate-400">
            Latest alerts
          </h2>
          <Link
            href="/alerts"
            className="text-xs text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            View all
          </Link>
        </div>
        {!alerts ? (
          error ? null : <LoadingLine />
        ) : alerts.items.length === 0 ? (
          <EmptyLine>No alerts yet. Run the replay.</EmptyLine>
        ) : (
          <ul className="divide-y divide-slate-800 overflow-hidden rounded-lg border border-slate-700/60">
            {alerts.items.map((a) => (
              <li key={a.id}>
                <Link
                  href={`/alerts/${a.id}`}
                  className="flex flex-col gap-2 px-4 py-3 hover:bg-ink-soft/70 focus-visible:bg-ink-soft focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-accent sm:flex-row sm:items-center sm:justify-between"
                >
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-xs text-slate-400">
                        {a.issuer.key ?? a.issuer.name}
                      </span>
                      <span className="font-mono text-xs text-slate-500">
                        {formatDate(a.event_date)}
                      </span>
                      <MaterialBadge material={a.material} />
                      <AlertStatusChip status={a.status} />
                    </div>
                    <p className="mt-1 truncate text-sm text-slate-200">{a.title}</p>
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="watchlist-heading" className="mb-10">
        <h2 id="watchlist-heading" className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">
          Watchlist
        </h2>
        {!issuers ? (
          error ? null : <LoadingLine />
        ) : issuers.items.length === 0 ? (
          <EmptyLine>No issuers loaded.</EmptyLine>
        ) : (
          <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {issuers.items.map((iss) => {
              const key = iss.key ?? iss.id;
              const count = watchCounts.get(key) ?? alertCounts.get(key) ?? 0;
              return (
                <li key={iss.id}>
                  <Link
                    href={`/issuers/${encodeURIComponent(key)}`}
                    className="flex items-center justify-between gap-3 rounded-lg border border-slate-700/60 bg-ink-soft/40 px-4 py-3 hover:border-slate-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-sm text-slate-100">{iss.name}</span>
                      <span className="font-mono text-[11px] text-slate-500">
                        {iss.ticker ?? key}
                      </span>
                    </span>
                    <span className="shrink-0 font-mono text-xs text-slate-400">
                      {count === 1 ? "1 alert" : `${count} alerts`}
                    </span>
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <p className="font-mono text-[11px] text-slate-500" aria-live="polite">
        System:{" "}
        {ready ? (
          <>
            {ready.status}
            {" · "}
            {Object.entries(ready.checks)
              .map(([k, v]) => `${k} ${v ? "ok" : "down"}`)
              .join(", ")}
            {" · "}
            {Object.entries(ready.providers)
              .map(([k, v]) => `${k} ${v ? "configured" : "missing"}`)
              .join(", ")}
          </>
        ) : (
          "checking…"
        )}
      </p>
    </PageShell>
  );
}
