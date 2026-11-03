"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { ErrorLine, LoadingLine, PageShell } from "@/components/loading-error";
import { MaturityWallChart } from "@/components/maturity-wall-chart";
import { VerifiedCharts } from "@/components/verified-charts";
import {
  getJson,
  type ChartListOut,
  type InstrumentOut,
  type IssuerDetailOut,
  type IssuerSnapshot,
  type RefinancingPanel,
} from "@/lib/api";
import { formatBps, formatDate, formatUsdDisplay, formatUsdMillions } from "@/lib/format";
import { CreditSnapshot } from "./credit-snapshot";

const WINDOW_START = Date.UTC(2026, 0, 1);
const WEEK_MS = 7 * 24 * 60 * 60 * 1000;
// The slider runs from 1 Jan 2026 to today, so the page opens on the latest figures and snapshot.
const TODAY_MS = (() => {
  const n = new Date();
  return Date.UTC(n.getUTCFullYear(), n.getUTCMonth(), n.getUTCDate());
})();
const WINDOW_END = Math.max(Date.UTC(2026, 9, 31), TODAY_MS);

function toIso(ms: number): string {
  const d = new Date(ms);
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const day = String(d.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

const DEFAULT_AS_OF = toIso(WINDOW_END);

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
  const [updating, setUpdating] = useState(false);
  const [statusFilter, setStatusFilter] = useState<"all" | "active" | "repaid">("all");
  const [sortKey, setSortKey] = useState<SortKey>("maturity");
  const [sortAsc, setSortAsc] = useState(true);
  const [charts, setCharts] = useState<ChartListOut | null>(null);
  const [showUnverified, setShowUnverified] = useState(false);
  const [snapshot, setSnapshot] = useState<IssuerSnapshot | null>(null);
  const [playing, setPlaying] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [eventDates, setEventDates] = useState<string[]>([]);
  const hasLoadedRef = useRef(false);
  const keyRef = useRef(key);
  const [shockBps, setShockBps] = useState(0);
  const [tenorYears, setTenorYears] = useState(5);
  const [refinancing, setRefinancing] = useState<RefinancingPanel | null>(null);
  const [refiError, setRefiError] = useState<string | null>(null);
  const [baseline, setBaseline] = useState<IssuerDetailOut | null>(null);

  useEffect(() => {
    let cancelled = false;
    getJson<IssuerDetailOut>(`/api/issuers/${encodeURIComponent(key)}?as_of=2026-01-01`)
      .then((d) => {
        if (!cancelled) setBaseline(d);
      })
      .catch(() => {
        /* the outline is optional */
      });
    return () => {
      cancelled = true;
    };
  }, [key]);

  if (keyRef.current !== key) {
    keyRef.current = key;
    hasLoadedRef.current = false;
  }

  useEffect(() => {
    let cancelled = false;
    if (debounceRef.current) clearTimeout(debounceRef.current);

    const immediate = !hasLoadedRef.current;
    const run = () => {
      if (immediate) {
        setLoading(true);
        setSnapshot(null);
      } else {
        setUpdating(true);
      }
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
          if (!cancelled) {
            setLoading(false);
            setUpdating(false);
          }
        });
      getJson<IssuerSnapshot>(`/api/issuers/${encodeURIComponent(key)}/snapshot?as_of=${asOf}`)
        .then((d) => {
          if (!cancelled) setSnapshot(d);
        })
        .catch(() => {
          if (!cancelled) setSnapshot(null);
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
    const timer = setTimeout(() => {
      getJson<RefinancingPanel>(
        `/api/issuers/${encodeURIComponent(key)}/refinancing?as_of=${asOf}&shock_bps=${shockBps}&tenor_years=${tenorYears}`,
      )
        .then((d) => {
          if (cancelled) return;
          setRefinancing(d);
          setRefiError(null);
        })
        .catch((e: Error) => {
          if (cancelled) return;
          setRefinancing(null);
          setRefiError(e.message);
        });
    }, shockBps === 0 ? 0 : 150);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [key, asOf, shockBps, tenorYears]);

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

  useEffect(() => {
    if (!playing) return undefined;
    if (sliderValue >= sliderMax) {
      setPlaying(false);
      return undefined;
    }
    const timer = setTimeout(() => {
      const ms = WINDOW_START + (sliderValue + 1) * WEEK_MS;
      setAsOf(toIso(Math.min(WINDOW_END, ms)));
    }, 700);
    return () => clearTimeout(timer);
  }, [playing, sliderValue, sliderMax]);

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
            <dl className="mt-3 flex flex-wrap gap-x-4 gap-y-1 tabular-nums text-xs text-slate-400">
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

          <div className={updating ? "opacity-70 transition-opacity" : undefined}>
            <CreditSnapshot
              snapshot={snapshot}
              loading={loading || (updating && !snapshot)}
              asOf={asOf}
            />
            {updating && snapshot ? (
              <p className="mt-2 tabular-nums text-[11px] text-slate-500" aria-live="polite">
                Updating figures for {formatDate(asOf)}…
              </p>
            ) : null}
          </div>

          <section aria-labelledby="wall-heading" className="border-t border-memo-rule pt-8">
            <div className="flex flex-wrap items-baseline justify-between gap-3">
              <h3 id="wall-heading" className="text-lg font-semibold text-memo-ink">
                Debt due by year, as of {formatDate(asOf)}
              </h3>
              <button
                type="button"
                onClick={() => {
                  if (!playing && sliderValue >= sliderMax) setAsOf(toIso(WINDOW_START));
                  setPlaying((v) => !v);
                }}
                aria-pressed={playing}
                className="rounded-md border border-memo-ink px-3 py-1.5 text-sm text-memo-ink"
              >
                {playing ? "Pause" : "Play 2026"}
              </button>
            </div>
            <p className="mt-1 max-w-[70ch] text-sm text-memo-muted">
              Move through the year to see each filing change the profile. Dashed outlines show the
              same year on 1 Jan 2026.
            </p>

            <div className="mt-5">
              <label className="block">
                <span className="sr-only">Date</span>
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
                  className="w-full accent-[#1C232B]"
                  aria-valuetext={formatDate(asOf)}
                />
              </label>
              <div className="relative mt-1 h-8">
                {eventDates.map((d) => {
                  const pct = Math.min(100, Math.max(0, ((parseIso(d) - WINDOW_START) / (WINDOW_END - WINDOW_START)) * 100));
                  return (
                    <button
                      key={d}
                      type="button"
                      onClick={() => {
                        setPlaying(false);
                        setAsOf(d);
                      }}
                      className="absolute -translate-x-1/2 rounded px-1 text-xs text-memo-added hover:bg-[#EEF0EC]"
                      style={{ left: `${pct}%` }}
                      title={`Jump to ${formatDate(d)}`}
                      aria-label={`Jump to ${formatDate(d)}`}
                    >
                      ▲
                    </button>
                  );
                })}
              </div>
              <div className="flex justify-between text-xs text-memo-muted">
                <span>1 Jan 2026</span>
                <span>Filings that changed the profile are marked ▲</span>
                <span>{formatDate(toIso(WINDOW_END))}</span>
              </div>
            </div>

            {error ? <div className="mt-3"><ErrorLine message={error} /></div> : null}

            <div className="mt-8 grid gap-10 lg:grid-cols-2">
              <MaturityWallChart
                wall={issuer.maturity_wall}
                title="This company"
                baseline={baseline?.maturity_wall ?? null}
              />
              {issuer.group_maturity_wall ? (
                <MaturityWallChart
                  wall={issuer.group_maturity_wall}
                  title="Whole group, combined"
                  baseline={baseline?.group_maturity_wall ?? null}
                />
              ) : null}
            </div>

            {snapshot?.wall && snapshot.wall.changes.length > 0 ? (
              <div className="mt-8">
                <h4 className="text-sm font-medium text-memo-muted">What moved it this year</h4>
                <ul className="mt-2 border-t border-memo-rule">
                  {snapshot.wall.changes
                    .filter((c) => /^FY20(2[6-9]|30)$/.test(c.label))
                    .map((c, i) => (
                    <li key={`${c.label}-${i}`} className="border-b border-memo-rule py-2 text-sm">
                      <span className="font-medium text-memo-ink">
                        {c.label.replace(/^FY/, "").replace(/^after /, "after ")}:{" "}
                        {c.before !== null ? formatUsdDisplay(c.before) : "—"} →{" "}
                        {c.after !== null ? formatUsdDisplay(c.after) : "—"}
                      </span>
                      {c.alert_id ? (
                        <>
                          {"  "}
                          <Link
                            href={`/alerts/${c.alert_id}`}
                            className="ml-2 text-memo-info underline underline-offset-2"
                          >
                            {c.alert_title ?? "See the alert"}
                          </Link>
                        </>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            <div className="mt-10 border-t border-memo-rule pt-8">
              <p className="text-xs font-medium text-slate-500">
                Refinancing shock
              </p>
              <p className="mt-2 text-sm text-slate-300">
                Incremental annual interest if 2026–2029 debt is refinanced at one selectable
                Treasury tenor plus the issuer’s filing-derived spread. Display only; not used
                by alerts.
              </p>
              {refinancing?.shock_bps === 0 && refinancing?.base_scenario_line ? (
                <p className="mt-2 text-sm text-slate-400">{refinancing.base_scenario_line}</p>
              ) : null}
              {refinancing?.derivation_line ? (
                <p className="mt-2 text-sm text-slate-200">{refinancing.derivation_line}</p>
              ) : refinancing?.missing ? (
                <p className="mt-2 text-sm text-amber-400">{refinancing.missing}</p>
              ) : null}
              <div className="mt-4 flex flex-wrap items-center gap-4 text-xs text-slate-400">
                <label className="flex items-center gap-2">
                  <span className="tabular-nums text-slate-500">Tenor</span>
                  <select
                    className="rounded border border-slate-700 bg-ink-soft px-2 py-1 text-sm text-slate-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                    value={tenorYears}
                    onChange={(e) => setTenorYears(Number(e.target.value))}
                    aria-label="Refinancing Treasury tenor"
                  >
                    {(refinancing?.tenor_choices ?? [2, 5, 7, 10]).map((y) => (
                      <option key={y} value={y}>
                        {y}-year
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex flex-wrap items-center gap-3">
                  <span className="tabular-nums text-slate-500">
                    Treasury shock
                  </span>
                  <input
                    type="range"
                    min={refinancing?.shock_bps_min ?? 0}
                    max={refinancing?.shock_bps_max ?? 300}
                    step={10}
                    value={shockBps}
                    onChange={(e) => setShockBps(Number(e.target.value))}
                    className="h-2 w-48 accent-accent"
                    aria-valuemin={0}
                    aria-valuemax={300}
                    aria-valuenow={shockBps}
                    aria-label="Treasury shock in basis points"
                  />
                  <span className="tabular-nums text-slate-200">{formatBps(shockBps)}</span>
                </label>
              </div>
              <p className="mt-2 text-xs text-slate-500">
                Illustrative annual interest if near-term maturities reprice off the selected
                Treasury (plus the company&apos;s spread). Coverage is EBIT ÷ interest (×).
              </p>
              {refiError ? (
                <p className="mt-3 text-xs text-amber-400">{refiError}</p>
              ) : null}
              {refinancing ? (
                <>
                  {refinancing.insight_sentence ? (
                    <p className="mt-3 text-sm text-slate-200">{refinancing.insight_sentence}</p>
                  ) : null}
                  <dl className="mt-3 grid gap-3 sm:grid-cols-3 tabular-nums text-sm">
                    <div>
                      <dt className="text-[11px] text-slate-500">
                        Base (+0 bp)
                      </dt>
                      <dd className="text-slate-100">
                        {refinancing.base_total != null
                          ? `${formatUsdMillions(refinancing.base_total)}/yr`
                          : "—"}
                      </dd>
                      <dd className="mt-1 text-[11px] text-slate-500">
                        named{" "}
                        {refinancing.named_base != null
                          ? formatUsdMillions(refinancing.named_base)
                          : "—"}
                        {" · "}est{" "}
                        {refinancing.estimated_base != null
                          ? formatUsdMillions(refinancing.estimated_base)
                          : "—"}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-[11px] text-slate-500">
                        Shocked
                      </dt>
                      <dd className="text-slate-100">
                        {refinancing.shocked_total != null
                          ? `${formatUsdMillions(refinancing.shocked_total)}/yr`
                          : "—"}
                      </dd>
                      <dd className="mt-1 text-[11px] text-slate-500">
                        Δ{" "}
                        {refinancing.delta != null
                          ? formatUsdMillions(refinancing.delta)
                          : "—"}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-[11px] text-slate-500">
                        Interest coverage (×)
                      </dt>
                      <dd className="text-slate-100">
                        {refinancing.coverage_before != null
                          ? `${Number(refinancing.coverage_before).toFixed(1)}×`
                          : "—"}
                        {" → "}
                        {refinancing.coverage_after_base != null
                          ? `${Number(refinancing.coverage_after_base).toFixed(1)}×`
                          : "—"}
                        {refinancing.coverage_after_shock != null && shockBps > 0
                          ? ` (shock ${Number(refinancing.coverage_after_shock).toFixed(1)}×)`
                          : ""}
                      </dd>
                      <dd className="mt-1 text-[11px] text-slate-500">
                        {refinancing.coverage_missing
                          ? refinancing.coverage_missing
                          : refinancing.pressure_rank != null
                            ? `refinance-pressure rank #${refinancing.pressure_rank} among watchlist names`
                            : "—"}
                      </dd>
                    </div>
                  </dl>
                  {(refinancing.buckets?.length ?? 0) > 0 ? (
                    <ul className="mt-3 space-y-1 tabular-nums text-[11px] text-slate-500">
                      {refinancing.buckets.map((b) => (
                        <li key={b.label}>
                          {b.label}: coupon{" "}
                          {b.maturing_coupon_pct != null
                            ? `${Number(b.maturing_coupon_pct).toFixed(2)}%`
                            : "—"}{" "}
                          ({b.maturing_coupon_source ?? "—"})
                          {b.unattributed_amount != null &&
                          Number(b.unattributed_amount) > 0
                            ? `; remainder ${b.remainder_coupon_source ?? "—"}`
                            : ""}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                  {Object.keys(refinancing.weights).length > 0 ? (
                    <details className="mt-3 text-[11px] text-slate-500">
                      <summary className="cursor-pointer text-slate-400">
                        How pressure rank is weighted
                      </summary>
                      <p className="mt-1 tabular-nums">
                        {Object.entries(refinancing.weights)
                          .map(([k, v]) => `${k.replaceAll("_", " ")} ${v}`)
                          .join(" · ")}
                      </p>
                    </details>
                  ) : null}
                  <p className="mt-3 text-[11px] leading-relaxed text-slate-500">
                    {refinancing.footnote}
                  </p>
                </>
              ) : (
                <p className="mt-3 text-xs text-slate-500">Loading refinancing…</p>
              )}
            </div>
          </section>

          <section aria-labelledby="instruments-heading">
            <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
              <h3
                id="instruments-heading"
                className="text-lg font-semibold text-memo-ink"
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
              <div className="overflow-x-auto border-t border-memo-ink">
                <table className="min-w-full text-left text-sm">
                  <thead className="bg-ink-soft tabular-nums text-[11px] text-slate-500">
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
                          <div className="tabular-nums text-[11px] text-slate-500">
                            {inst.key} · {inst.currency}
                          </div>
                        </td>
                        <td className="px-3 py-2 tabular-nums text-xs text-slate-400">
                          {Number(inst.coupon).toFixed(3)}%
                        </td>
                        <td className="px-3 py-2 tabular-nums text-xs text-slate-400">
                          {formatDate(inst.maturity)}
                        </td>
                        <td
                          className="px-3 py-2 tabular-nums text-xs text-slate-200"
                          title={
                            inst.original_amount &&
                            inst.original_amount !== inst.amount_outstanding
                              ? `Original par ${formatUsdMillions(inst.original_amount, inst.currency)} before reductions`
                              : undefined
                          }
                        >
                          {formatUsdMillions(inst.amount_outstanding, inst.currency)}
                        </td>
                        <td className="px-3 py-2 tabular-nums text-xs text-slate-400">
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
              className="mb-3 text-lg font-semibold text-memo-ink"
            >
              Events
            </h3>
            {issuer.events.length === 0 ? (
              <p className="text-sm text-slate-400">No events for this issuer.</p>
            ) : (
              <ol className="space-y-3 border-l border-slate-700/80 pl-4">
                {issuer.events.map((ev) => (
                  <li key={ev.id} className="text-sm">
                    <p className="tabular-nums text-[11px] text-slate-500">
                      {formatDate(ev.event_date)}
                      {ev.event_type ? ` · ${ev.event_type.replaceAll("_", " ")}` : ""}
                    </p>
                    <p className="text-slate-200">
                      {ev.summary ??
                        (ev.event_type
                          ? ev.event_type.replaceAll("_", " ")
                          : "Processing event")}
                    </p>
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

          <VerifiedCharts
            charts={charts}
            showUnverified={showUnverified}
            onToggleUnverified={setShowUnverified}
          />
        </div>
      ) : null}
    </PageShell>
  );
}
