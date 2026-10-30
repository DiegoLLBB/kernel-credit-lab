"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { ErrorLine, LoadingLine, PageShell } from "@/components/loading-error";
import { MaturityWallChart } from "@/components/maturity-wall-chart";
import { PassFailChip } from "@/components/status-chip";
import {
  chartImageUrl,
  getJson,
  type ChartListOut,
  type InstrumentOut,
  type IssuerDetailOut,
} from "@/lib/api";
import { formatDate, formatUsdMillions } from "@/lib/format";

const WINDOW_START = Date.UTC(2026, 0, 1);
const WINDOW_END = Date.UTC(2026, 9, 31);
const WEEK_MS = 7 * 24 * 60 * 60 * 1000;
const DEFAULT_AS_OF = "2026-10-31";

function toIso(ms: number): string {
  const d = new Date(ms);
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const day = String(d.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function parseIso(iso: string): number {
  const [y, m, d] = iso.split("-").map(Number);
  return Date.UTC(y, m - 1, d);
}

type SortKey = "maturity" | "amount" | "name";

export function IssuerDetailClient() {
  const params = useParams<{ key: string }>();
  const key = decodeURIComponent(params.key);

  const [asOf, setAsOf] = useState(DEFAULT_AS_OF);
  const [issuer, setIssuer] = useState<IssuerDetailOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<"all" | "active" | "repaid">("all");
  const [sortKey, setSortKey] = useState<SortKey>("maturity");
  const [sortAsc, setSortAsc] = useState(true);
  const [charts, setCharts] = useState<ChartListOut | null>(null);
  const [showUnverified, setShowUnverified] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [eventDates, setEventDates] = useState<string[]>([]);
  const hasLoadedRef = useRef(false);
  const keyRef = useRef(key);

  if (keyRef.current !== key) {
    keyRef.current = key;
    hasLoadedRef.current = false;
  }

  useEffect(() => {
    let cancelled = false;
    if (debounceRef.current) clearTimeout(debounceRef.current);

    const immediate = !hasLoadedRef.current;
    const run = () => {
      if (immediate) setLoading(true);
      getJson<IssuerDetailOut>(`/api/issuers/${encodeURIComponent(key)}?as_of=${asOf}`)
        .then((d) => {
          if (cancelled) return;
          setIssuer(d);
          hasLoadedRef.current = true;
          setEventDates([...new Set(d.events.map((e) => e.event_date))].sort());
          setError(null);
        })
        .catch((e: Error) => {
          if (!cancelled) setError(e.message);
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    };

    if (immediate) run();
    else debounceRef.current = setTimeout(run, 250);

    return () => {
      cancelled = true;
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [key, asOf]);

  useEffect(() => {
    let cancelled = false;
    const q = showUnverified ? "false" : "true";
    getJson<ChartListOut>(
      `/api/issuers/${encodeURIComponent(key)}/charts?verified_only=${q}`,
    )
      .then((d) => {
        if (!cancelled) setCharts(d);
      })
      .catch(() => {
        if (!cancelled) setCharts({ items: [], total: 0 });
      });
    return () => {
      cancelled = true;
    };
  }, [key, showUnverified]);

  const sliderMax = Math.floor((WINDOW_END - WINDOW_START) / WEEK_MS);
  const sliderValue = Math.min(
    sliderMax,
    Math.max(0, Math.round((parseIso(asOf) - WINDOW_START) / WEEK_MS)),
  );

  const instruments = useMemo(() => {
    const rows = [...(issuer?.instruments ?? [])];
    const filtered = rows.filter((r) => {
      if (statusFilter === "all") return true;
      if (statusFilter === "active") return r.status === "active";
      return r.status !== "active";
    });
    filtered.sort((a, b) => {
      let cmp = 0;
      if (sortKey === "maturity") cmp = a.maturity.localeCompare(b.maturity);
      else if (sortKey === "amount")
        cmp = Number(a.amount_outstanding) - Number(b.amount_outstanding);
      else cmp = a.name.localeCompare(b.name);
      return sortAsc ? cmp : -cmp;
    });
    return filtered;
  }, [issuer, statusFilter, sortKey, sortAsc]);

  function toggleSort(k: SortKey) {
    if (sortKey === k) setSortAsc((v) => !v);
    else {
      setSortKey(k);
      setSortAsc(true);
    }
  }

  return (
    <PageShell title="Issuer">
      {error && !issuer ? <ErrorLine message={error} /> : null}
      {loading && !issuer ? <LoadingLine label="Loading issuer…" /> : null}

      {issuer ? (
        <div className="space-y-10">
          <header>
            <h2 className="text-2xl font-semibold text-white">{issuer.name}</h2>
            <dl className="mt-3 flex flex-wrap gap-x-4 gap-y-1 font-mono text-xs text-slate-400">
              {issuer.ticker ? (
                <div>
                  <dt className="inline text-slate-500">Ticker </dt>
                  <dd className="inline">{issuer.ticker}</dd>
                </div>
              ) : null}
              {issuer.sector ? (
                <div>
                  <dt className="inline text-slate-500">Sector </dt>
                  <dd className="inline">{issuer.sector}</dd>
                </div>
              ) : null}
              {issuer.cik ? (
                <div>
                  <dt className="inline text-slate-500">CIK </dt>
                  <dd className="inline">{issuer.cik}</dd>
                </div>
              ) : null}
              <div>
                <dt className="inline text-slate-500">Key </dt>
                <dd className="inline">{issuer.key}</dd>
              </div>
            </dl>
            <div className="mt-3 flex flex-wrap gap-3 text-sm">
              {issuer.parent?.key ? (
                <Link
                  href={`/issuers/${encodeURIComponent(issuer.parent.key)}`}
                  className="text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                >
                  Parent: {issuer.parent.name}
                </Link>
              ) : null}
              {issuer.subsidiaries.map((s) =>
                s.key ? (
                  <Link
                    key={s.id}
                    href={`/issuers/${encodeURIComponent(s.key)}`}
                    className="text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                  >
                    {s.name}
                  </Link>
                ) : null,
              )}
            </div>
          </header>

          <section aria-labelledby="wall-heading" className="rounded-lg border border-slate-700/60 bg-ink-soft/40 p-5">
            <h3 id="wall-heading" className="text-sm font-semibold uppercase tracking-wide text-slate-400">
              Maturity wall
            </h3>
            <p className="mt-1 font-mono text-xs text-slate-500">As of {formatDate(asOf)}</p>

            <label className="mt-4 block">
              <span className="sr-only">As-of date</span>
              <input
                type="range"
                min={0}
                max={sliderMax}
                step={1}
                value={sliderValue}
                onChange={(e) => {
                  const ms = WINDOW_START + Number(e.target.value) * WEEK_MS;
                  setAsOf(toIso(Math.min(WINDOW_END, ms)));
                }}
                list="issuer-event-ticks"
                className="w-full accent-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                aria-valuetext={formatDate(asOf)}
              />
              <datalist id="issuer-event-ticks">
                {eventDates.map((d) => {
                  const week = Math.round((parseIso(d) - WINDOW_START) / WEEK_MS);
                  return <option key={d} value={week} label={d} />;
                })}
              </datalist>
            </label>
            <div className="mt-1 flex justify-between font-mono text-[10px] text-slate-500">
              <span>1 Jan 2026</span>
              <span>31 Oct 2026</span>
            </div>
            {eventDates.length > 0 ? (
              <p className="mt-2 font-mono text-[11px] text-slate-500">
                Event dates: {eventDates.map((d) => formatDate(d)).join(" · ")}
              </p>
            ) : null}

            {error ? <div className="mt-3"><ErrorLine message={error} /></div> : null}

            <div className="mt-6 grid gap-8 lg:grid-cols-2">
              <MaturityWallChart wall={issuer.maturity_wall} title="Issuer wall" />
              {issuer.group_maturity_wall ? (
                <MaturityWallChart
                  wall={issuer.group_maturity_wall}
                  title="Group wall (consolidated)"
                />
              ) : null}
            </div>
          </section>

          <section aria-labelledby="instruments-heading">
            <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
              <h3
                id="instruments-heading"
                className="text-sm font-semibold uppercase tracking-wide text-slate-400"
              >
                Instruments
              </h3>
              <label className="text-xs text-slate-400">
                Status{" "}
                <select
                  className="ml-1 rounded border border-slate-700 bg-ink-soft px-2 py-1 text-sm text-slate-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                  value={statusFilter}
                  onChange={(e) =>
                    setStatusFilter(e.target.value as "all" | "active" | "repaid")
                  }
                >
                  <option value="all">all</option>
                  <option value="active">active</option>
                  <option value="repaid">repaid</option>
                </select>
              </label>
            </div>
            {instruments.length === 0 ? (
              <p className="text-sm text-slate-400">No instruments match.</p>
            ) : (
              <div className="overflow-x-auto rounded-lg border border-slate-700/60">
                <table className="min-w-full text-left text-sm">
                  <thead className="bg-ink-soft font-mono text-[11px] uppercase tracking-wide text-slate-500">
                    <tr>
                      <th className="px-3 py-2">
                        <button
                          type="button"
                          className="focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                          onClick={() => toggleSort("name")}
                        >
                          Name{sortKey === "name" ? (sortAsc ? " ↑" : " ↓") : ""}
                        </button>
                      </th>
                      <th className="px-3 py-2">Coupon</th>
                      <th className="px-3 py-2">
                        <button
                          type="button"
                          className="focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                          onClick={() => toggleSort("maturity")}
                        >
                          Maturity{sortKey === "maturity" ? (sortAsc ? " ↑" : " ↓") : ""}
                        </button>
                      </th>
                      <th className="px-3 py-2">
                        <button
                          type="button"
                          className="focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                          onClick={() => toggleSort("amount")}
                        >
                          Amount{sortKey === "amount" ? (sortAsc ? " ↑" : " ↓") : ""}
                        </button>
                      </th>
                      <th className="px-3 py-2">Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800">
                    {instruments.map((inst: InstrumentOut) => (
                      <tr key={inst.id}>
                        <td className="px-3 py-2">
                          <div className="text-slate-200">{inst.name}</div>
                          <div className="font-mono text-[11px] text-slate-500">
                            {inst.key} · {inst.currency}
                          </div>
                        </td>
                        <td className="px-3 py-2 font-mono text-xs text-slate-400">
                          {Number(inst.coupon).toFixed(3)}%
                        </td>
                        <td className="px-3 py-2 font-mono text-xs text-slate-400">
                          {formatDate(inst.maturity)}
                        </td>
                        <td className="px-3 py-2 font-mono text-xs text-slate-200">
                          {formatUsdMillions(inst.amount_outstanding, inst.currency)}
                        </td>
                        <td className="px-3 py-2 font-mono text-xs text-slate-400">
                          {inst.status}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section aria-labelledby="events-heading">
            <h3
              id="events-heading"
              className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400"
            >
              Events
            </h3>
            {issuer.events.length === 0 ? (
              <p className="text-sm text-slate-400">No events for this issuer.</p>
            ) : (
              <ol className="space-y-3 border-l border-slate-700/80 pl-4">
                {issuer.events.map((ev) => (
                  <li key={ev.id} className="text-sm">
                    <p className="font-mono text-[11px] text-slate-500">
                      {formatDate(ev.event_date)} · {ev.event_type} · {ev.status}
                      {ev.run_status ? ` · run ${ev.run_status}` : ""}
                    </p>
                    <p className="text-slate-200">{ev.summary ?? ev.replay_key ?? ev.id}</p>
                    <div className="mt-1 flex flex-wrap gap-3 text-xs">
                      {ev.alert_ids.map((aid) => (
                        <Link
                          key={aid}
                          href={`/alerts/${aid}`}
                          className="text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                        >
                          Alert
                        </Link>
                      ))}
                      {ev.run_id ? (
                        <Link
                          href={`/runs/${ev.run_id}`}
                          className="text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                        >
                          Trace
                        </Link>
                      ) : null}
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </section>

          <section aria-labelledby="charts-heading">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-3">
              <h3
                id="charts-heading"
                className="text-sm font-semibold uppercase tracking-wide text-slate-400"
              >
                Verified charts
              </h3>
              <label className="inline-flex items-center gap-2 text-xs text-slate-400">
                <input
                  type="checkbox"
                  className="rounded border-slate-600 bg-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                  checked={showUnverified}
                  onChange={(e) => setShowUnverified(e.target.checked)}
                />
                Show unverified
              </label>
            </div>
            <p className="mb-4 text-sm text-slate-500">
              Located by SAM 3.1, read by Muse Spark, accepted only if bar heights match the
              printed values.
            </p>
            {!charts ? (
              <LoadingLine label="Loading charts…" />
            ) : charts.items.length === 0 ? (
              <p className="text-sm text-slate-400">
                {showUnverified ? "No charts for this issuer." : "No verified charts."}
              </p>
            ) : (
              <ul className="grid gap-6 sm:grid-cols-2">
                {charts.items.map((c) => {
                  const checks = c.verification_detail?.checks ?? [];
                  return (
                    <li
                      key={c.id}
                      className="overflow-hidden rounded-lg border border-slate-700/60 bg-ink-soft/30"
                    >
                      {c.has_overlay || c.has_crop ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                          src={chartImageUrl(c.id, true)}
                          alt={c.title ?? "Chart"}
                          className="w-full border-b border-slate-800 bg-black/40 object-contain"
                        />
                      ) : (
                        <div className="flex h-32 items-center justify-center text-xs text-slate-500">
                          No image
                        </div>
                      )}
                      <div className="space-y-2 p-4">
                        <div className="flex flex-wrap items-center gap-2">
                          <h4 className="text-sm font-medium text-slate-100">
                            {c.title ?? "Untitled chart"}
                          </h4>
                          <PassFailChip
                            passed={c.verified}
                            label={c.verified ? "verified" : "unverified"}
                          />
                        </div>
                        <p className="font-mono text-[11px] text-slate-500">
                          {[c.filing_title, c.page != null ? `p.${c.page + 1}` : null, c.unit]
                            .filter(Boolean)
                            .join(" · ")}
                        </p>
                        {!c.verified && c.verification_detail?.reason ? (
                          <p className="text-xs text-rose-300" role="status">
                            Refusal: {c.verification_detail.reason}
                          </p>
                        ) : null}
                        {c.categories.length > 0 ? (
                          <table className="mt-2 w-full text-left font-mono text-[11px]">
                            <thead className="text-slate-500">
                              <tr>
                                <th className="py-1 pr-2 font-medium">Category</th>
                                <th className="py-1 pr-2 font-medium">Value</th>
                                <th className="py-1 font-medium">Deviation</th>
                              </tr>
                            </thead>
                            <tbody>
                              {c.categories.map((cat, i) => {
                                const check = checks.find(
                                  (ch) => String(ch.category) === String(cat),
                                ) ?? checks[i];
                                return (
                                  <tr key={`${String(cat)}-${i}`} className="text-slate-300">
                                    <td className="py-0.5 pr-2">{String(cat)}</td>
                                    <td className="py-0.5 pr-2">{String(c.values[i] ?? "—")}</td>
                                    <td className="py-0.5">
                                      {check?.deviation != null
                                        ? `${(check.deviation * 100).toFixed(2)}%`
                                        : "—"}
                                    </td>
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                        ) : null}
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        </div>
      ) : null}
    </PageShell>
  );
}
