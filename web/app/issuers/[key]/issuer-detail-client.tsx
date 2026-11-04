"use client";

import { SectionBoundary } from "@/components/section-boundary";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { ErrorLine, PageShell } from "@/components/loading-error";
import { PageSkeleton } from "@/components/skeleton";
import { MaturityWallChart } from "@/components/maturity-wall-chart";
import { VerifiedCharts } from "@/components/verified-charts";
import { LiquidityLine } from "@/components/liquidity-line";
import { WatchList, watchLines } from "@/components/watch-lines";
import {
  getJson,
  type ChartListOut,
  type InstrumentOut,
  type IssuerDetailOut,
  type IssuerSnapshot,
  type RefinancingPanel,
} from "@/lib/api";
import { formatDate, formatUsdDisplay, formatUsdMillions } from "@/lib/format";
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

function eventKind(t: string | null | undefined): string {
  const v = (t ?? "").replaceAll("_", " ").trim();
  if (!v) return "Filing";
  if (/^\d|^[A-Z0-9-]+$/.test(v)) return v; // form types such as 10-Q, 8-K, 424B2
  return v.charAt(0).toUpperCase() + v.slice(1);
}

const SHOWN_EVENTS = 8;

/** Filings read for one company, newest first; the older ones fold away. */
function EventList({ events }: { events: IssuerDetailOut["events"] }) {
  const sorted = [...events].sort((a, b) => b.event_date.localeCompare(a.event_date));
  const row = (ev: IssuerDetailOut["events"][number]) => (
    <li
      key={ev.id}
      className="grid grid-cols-1 gap-x-6 gap-y-0.5 border-b border-memo-rule py-3 sm:grid-cols-[7.5rem_minmax(0,1fr)_auto]"
    >
      <span className="text-sm text-memo-muted">{formatDate(ev.event_date)}</span>
      <span>
        <span className="block text-xs text-memo-muted">{eventKind(ev.event_type)}</span>
        <span className="block text-base text-memo-ink">{ev.summary ?? eventKind(ev.event_type)}</span>
      </span>
      <span className="flex gap-4 text-sm sm:justify-end">
        {ev.alert_ids.map((aid) => (
          <Link key={aid} href={`/alerts/${aid}`} className="font-medium text-memo-added underline underline-offset-2">
            Alert
          </Link>
        ))}
        {ev.run_id ? (
          <Link href={`/runs/${ev.run_id}`} className="text-memo-info underline underline-offset-2">
            Agent&apos;s record
          </Link>
        ) : null}
      </span>
    </li>
  );
  return (
    <>
      <ol className="mt-3 border-t border-memo-ink">{sorted.slice(0, SHOWN_EVENTS).map(row)}</ol>
      {sorted.length > SHOWN_EVENTS ? (
        <details className="mt-2">
          <summary className="cursor-pointer text-sm text-memo-info underline underline-offset-2">
            {sorted.length - SHOWN_EVENTS} earlier
          </summary>
          <ol>{sorted.slice(SHOWN_EVENTS).map(row)}</ol>
        </details>
      ) : null}
    </>
  );
}

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
    <PageShell title={issuer?.name ?? "Company"}>
      {error && !issuer ? <ErrorLine message={error} /> : null}
      {loading && !issuer ? <PageSkeleton kind="issuer" /> : null}

      {issuer ? (
        <div className="space-y-10">
          <header className="grid gap-x-12 gap-y-6 md:grid-cols-[minmax(0,1fr)_17rem] md:items-start">
            <div>
              <p className="no-print text-sm text-memo-muted">
                <Link href="/issuers" className="underline-offset-2 hover:underline">
                  Companies
                </Link>
                {issuer.sector ? <span>, {issuer.sector}</span> : null}
              </p>
              <h2 className="mt-2 text-3xl font-semibold leading-tight text-memo-ink sm:text-[42px] sm:leading-[48px]">
                {issuer.name}
              </h2>
              {issuer.parent?.key ? (
                <p className="mt-3 text-base text-memo-ink">
                  Part of{" "}
                  <Link
                    href={`/issuers/${encodeURIComponent(issuer.parent.key)}`}
                    className="text-memo-info underline underline-offset-2"
                  >
                    {issuer.parent.name}
                  </Link>
                  . Its own bonds and loans are shown here; the group&apos;s combined debt is on the
                  parent&apos;s page.
                </p>
              ) : null}
              {issuer.subsidiaries.some((sub) => sub.key) ? (
                <p className="mt-3 text-base text-memo-ink">
                  Group companies that issue their own debt:{" "}
                  {issuer.subsidiaries
                    .filter((sub) => sub.key)
                    .map((sub, i, arr) => (
                      <span key={sub.id}>
                        <Link
                          href={`/issuers/${encodeURIComponent(sub.key ?? "")}`}
                          className="text-memo-info underline underline-offset-2"
                        >
                          {sub.name}
                        </Link>
                        {i < arr.length - 2 ? ", " : i === arr.length - 2 ? " and " : "."}
                      </span>
                    ))}
                </p>
              ) : null}
              <p className="print-only mt-2 text-sm text-memo-muted">
                Kernel tear sheet, figures as of {formatDate(asOf)}, printed{" "}
                {formatDate(new Date().toISOString().slice(0, 10))}. Every figure is computed from SEC
                filings; sources at kernel-credit-lab.web.app/issuers/{issuer.key ?? ""}.
              </p>
            </div>
            <div>
              <dl className="border-t border-memo-ink text-sm">
                {(
                  [
                    ["Ticker", issuer.ticker],
                    ["Sector", issuer.sector],
                    ["Figures as of", formatDate(asOf)],
                  ] as [string, string | null][]
                )
                  .filter(([, v]) => v)
                  .map(([k, v]) => (
                    <div key={k} className="flex items-baseline justify-between gap-4 border-b border-memo-rule py-1.5">
                      <dt className="text-memo-muted">{k}</dt>
                      <dd className="text-right font-medium text-memo-ink">{v}</dd>
                    </div>
                  ))}
                {issuer.cik ? (
                  <div className="flex items-baseline justify-between gap-4 border-b border-memo-rule py-1.5">
                    <dt className="text-memo-muted">SEC filings</dt>
                    <dd>
                      <a
                        href={`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${encodeURIComponent(issuer.cik)}&type=&dateb=&owner=include&count=40`}
                        className="font-medium text-memo-info underline underline-offset-2"
                        target="_blank"
                        rel="noreferrer"
                      >
                        EDGAR, CIK {issuer.cik}
                      </a>
                    </dd>
                  </div>
                ) : null}
              </dl>
              <button
                type="button"
                onClick={() => window.print()}
                className="no-print mt-3 rounded-md border border-memo-ink px-3 py-1.5 text-sm text-memo-ink hover:bg-[#EEF0EC]"
              >
                Print tear sheet
              </button>
            </div>
          </header>

          <div className={updating ? "opacity-70 transition-opacity" : undefined}>
            <SectionBoundary name="snapshot">
            <CreditSnapshot
              snapshot={snapshot}
              loading={loading || (updating && !snapshot)}
              asOf={asOf}
              watch={<WatchList lines={watchLines(snapshot?.metrics ?? [], issuer.maturity_wall)} />}
              parent={issuer.parent ? { key: issuer.parent.key, name: issuer.parent.name } : null}
            />
            </SectionBoundary>
            {updating && snapshot ? (
              <p className="mt-2 text-xs text-memo-muted" aria-live="polite">
                Updating figures for {formatDate(asOf)}…
              </p>
            ) : null}
          </div>

          <SectionBoundary name="debt by year">
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
                className="no-print rounded-md border border-memo-ink px-3 py-1.5 text-sm text-memo-ink"
              >
                {playing ? "Pause" : "Play 2026"}
              </button>
            </div>
            <p className="mt-1 max-w-[70ch] text-sm text-memo-muted">
              Move through the year to see each filing change the profile. Dashed outlines show the
              same year on 1 Jan 2026.
            </p>

            <div className="no-print mt-5">
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

            <LiquidityLine metrics={snapshot?.metrics} />

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

            <div className="mt-12 border-t-[3px] border-memo-ink pt-6">
              <h4 className="text-lg font-semibold text-memo-ink">What refinancing would cost</h4>
              <p className="mt-1 max-w-[70ch] text-sm text-memo-muted">
                Extra interest a year if the debt due 2026–2029 is refinanced at a Treasury yield plus the
                company&apos;s own borrowing premium (interest expense ÷ average debt, minus Treasury). A
                figure from its filings, not a market spread. Display only; alerts don&apos;t use it.
              </p>

              <div className="no-print mt-5 flex flex-wrap items-end gap-x-8 gap-y-4">
                <label className="text-sm text-memo-muted">
                  Refinance at the
                  <select
                    className="ml-2 rounded-md border border-memo-rule bg-[#FBFCFA] px-2 py-1.5 text-sm text-memo-ink"
                    value={tenorYears}
                    onChange={(e) => setTenorYears(Number(e.target.value))}
                    aria-label="Treasury tenor used for refinancing"
                  >
                    {(refinancing?.tenor_choices ?? [2, 5, 7, 10]).map((y) => (
                      <option key={y} value={y}>
                        {y}-year Treasury
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex flex-wrap items-center gap-3 text-sm text-memo-muted">
                  Rates rise by
                  <input
                    type="range"
                    min={refinancing?.shock_bps_min ?? 0}
                    max={refinancing?.shock_bps_max ?? 300}
                    step={10}
                    value={shockBps}
                    onChange={(e) => setShockBps(Number(e.target.value))}
                    className="w-48 accent-[#1C232B]"
                    aria-valuemin={0}
                    aria-valuemax={300}
                    aria-valuenow={shockBps}
                    aria-valuetext={`${shockBps} basis points`}
                    aria-label="Rise in Treasury yields, in basis points"
                  />
                  <span className="w-28 font-medium text-memo-ink">
                    {shockBps === 0 ? "nothing (today)" : `${shockBps} bp (${(shockBps / 100).toFixed(2)}%)`}
                  </span>
                </label>
              </div>

              {refiError ? <p className="mt-3 text-sm text-memo-watch">{refiError}</p> : null}
              {refinancing?.missing ? (
                <p className="mt-3 text-sm text-memo-watch">Can&apos;t be worked out: {refinancing.missing}.</p>
              ) : null}

              {refinancing ? (
                <>
                  {refinancing.insight_sentence ? (
                    <p className="mt-5 max-w-[62ch] text-lg leading-7 text-memo-ink">{refinancing.insight_sentence}</p>
                  ) : null}
                  <dl className="mt-5 grid gap-x-8 gap-y-5 sm:grid-cols-3">
                    <div className="border-t border-memo-rule pt-3">
                      <dt className="text-sm text-memo-muted">
                        {Number(refinancing.base_total) < 0
                          ? "Interest saved a year, at today's rates"
                          : "Extra interest a year, at today's rates"}
                      </dt>
                      <dd className="mt-1 text-2xl font-medium text-memo-ink">
                        {refinancing.base_total != null ? formatUsdDisplay(refinancing.base_total) : "–"}
                      </dd>
                      {refinancing.named_base != null || refinancing.estimated_base != null ? (
                        <dd className="mt-0.5 text-xs text-memo-muted">
                          {formatUsdDisplay(refinancing.named_base)} from bonds tracked one by one,{" "}
                          {formatUsdDisplay(refinancing.estimated_base)} from the rest of the 10-K schedule
                        </dd>
                      ) : null}
                    </div>
                    <div className="border-t border-memo-rule pt-3">
                      <dt className="text-sm text-memo-muted">
                        {shockBps === 0 ? "If rates rise" : `If rates rise ${shockBps} bp`}
                      </dt>
                      {shockBps > 0 ? (
                        <dd className="mt-1 text-2xl font-medium text-memo-added">
                          {refinancing.shocked_total != null ? formatUsdDisplay(refinancing.shocked_total) : "–"}
                        </dd>
                      ) : (
                        <dd className="mt-2 text-sm text-memo-muted">
                          Move &ldquo;Rates rise by&rdquo; above to test a rise of up to 300 bp.
                        </dd>
                      )}
                      {shockBps > 0 && refinancing.delta != null ? (
                        <dd className="mt-0.5 text-xs text-memo-muted">
                          {formatUsdDisplay(refinancing.delta)} a year more than at today&apos;s rates
                        </dd>
                      ) : null}
                    </div>
                    <div className="border-t border-memo-rule pt-3">
                      <dt className="text-sm text-memo-muted">Interest cover, now → after refinancing</dt>
                      <dd className="mt-1 text-2xl font-medium text-memo-ink">
                        {refinancing.coverage_before != null ? `${Number(refinancing.coverage_before).toFixed(1)}×` : "–"}
                        {" → "}
                        {shockBps > 0 && refinancing.coverage_after_shock != null
                          ? `${Number(refinancing.coverage_after_shock).toFixed(1)}×`
                          : refinancing.coverage_after_base != null
                            ? `${Number(refinancing.coverage_after_base).toFixed(1)}×`
                            : "–"}
                      </dd>
                      <dd className="mt-0.5 text-xs text-memo-muted">
                        {refinancing.coverage_missing
                          ? refinancing.coverage_missing
                          : refinancing.pressure_rank != null
                            ? `Operating profit ÷ interest. Refinancing pressure ranks #${refinancing.pressure_rank} on the watchlist.`
                            : "Operating profit ÷ interest."}
                      </dd>
                    </div>
                  </dl>
                  <details className="mt-5 text-sm text-memo-muted">
                    <summary className="cursor-pointer text-memo-info underline underline-offset-2">
                      How it&apos;s worked out
                    </summary>
                    <div className="mt-2 max-w-[80ch] space-y-2">
                      {refinancing.derivation_line ? <p className="text-memo-ink">{refinancing.derivation_line}</p> : null}
                      {(refinancing.buckets?.length ?? 0) > 0 ? (
                        <ul className="border-t border-memo-rule">
                          {refinancing.buckets.map((bk) => (
                            <li key={bk.label} className="border-b border-memo-rule py-1.5">
                              <span className="text-memo-ink">{bk.label.replace(/^FY/, "")}</span>: maturing coupon{" "}
                              {bk.maturing_coupon_pct != null ? `${Number(bk.maturing_coupon_pct).toFixed(2)}%` : "unknown"}
                              {bk.maturing_coupon_source ? ` (${bk.maturing_coupon_source})` : ""}
                              {bk.unattributed_amount != null && Number(bk.unattributed_amount) > 0
                                ? `; the rest of that year at ${bk.remainder_coupon_source ?? "the average cost of debt"}`
                                : ""}
                            </li>
                          ))}
                        </ul>
                      ) : null}
                      {Object.keys(refinancing.weights).length > 0 ? (
                        <p>
                          Pressure rank weights:{" "}
                          {Object.entries(refinancing.weights)
                            .map(([k, v]) => `${k.replaceAll("_", " ")} ${v}`)
                            .join(", ")}
                          .
                        </p>
                      ) : null}
                      <p>{refinancing.footnote}</p>
                    </div>
                  </details>
                </>
              ) : !refiError ? (
                <p className="mt-4 text-sm text-memo-muted">Working out refinancing costs…</p>
              ) : null}
            </div>
          </section>
          </SectionBoundary>

          <SectionBoundary name="bonds and loans">
          <section aria-labelledby="instruments-heading" className="border-t border-memo-rule pt-8">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div>
                <h3 id="instruments-heading" className="text-lg font-semibold text-memo-ink">
                  Bonds and loans tracked one by one
                </h3>
                <p className="mt-1 max-w-[70ch] text-sm text-memo-muted">
                  Each recorded from the company&apos;s filings and updated when a filing changes it. Amounts as
                  of {formatDate(asOf)}.
                </p>
              </div>
              {(issuer.instruments?.length ?? 0) > 0 ? (
                <div role="group" aria-label="Show" className="no-print flex rounded-md border border-memo-rule text-sm">
                  {(
                    [
                      ["all", "All"],
                      ["active", "Outstanding"],
                      ["repaid", "Repaid or redeemed"],
                    ] as const
                  ).map(([v, label]) => (
                    <button
                      key={v}
                      type="button"
                      aria-pressed={statusFilter === v}
                      onClick={() => setStatusFilter(v)}
                      className={`px-3 py-1.5 first:rounded-l-md last:rounded-r-md ${
                        statusFilter === v ? "bg-memo-ink text-paper" : "text-memo-ink hover:bg-[#EEF0EC]"
                      }`}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
            {(issuer.instruments?.length ?? 0) === 0 ? (
              <p className="mt-3 max-w-[70ch] text-sm text-memo-muted">
                Kernel doesn&apos;t record this company&apos;s bonds one by one yet. Its debt by year above comes
                from the maturity schedule in its 10-K.
              </p>
            ) : instruments.length === 0 ? (
              <p className="mt-3 text-sm text-memo-muted">None in this group.</p>
            ) : (
              <div className="mt-3 overflow-x-auto border-t border-memo-ink">
                <table className="min-w-full border-collapse text-left text-sm">
                  <thead>
                    <tr className="border-b border-memo-ink text-xs text-memo-muted">
                      <th scope="col" className="py-2 pr-4 font-medium">
                        <button type="button" onClick={() => toggleSort("name")}>
                          Bond or loan{sortKey === "name" ? (sortAsc ? " ↑" : " ↓") : ""}
                        </button>
                      </th>
                      <th scope="col" className="py-2 pl-4 text-right font-medium">Coupon</th>
                      <th scope="col" className="py-2 pl-4 font-medium">
                        <button type="button" onClick={() => toggleSort("maturity")}>
                          Matures{sortKey === "maturity" ? (sortAsc ? " ↑" : " ↓") : ""}
                        </button>
                      </th>
                      <th scope="col" className="py-2 pl-4 text-right font-medium">
                        <button type="button" onClick={() => toggleSort("amount")}>
                          Outstanding{sortKey === "amount" ? (sortAsc ? " ↑" : " ↓") : ""}
                        </button>
                      </th>
                      <th scope="col" className="py-2 pl-4 font-medium">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {instruments.map((inst: InstrumentOut) => {
                      const live = inst.status === "active";
                      const reduced =
                        inst.original_amount != null && Number(inst.original_amount) !== Number(inst.amount_outstanding);
                      return (
                        <tr key={inst.id} className={`border-b border-memo-rule align-top ${live ? "" : "text-memo-muted"}`}>
                          <td className="py-2.5 pr-4">
                            <span className={live ? "text-memo-ink" : ""}>{inst.name}</span>
                            <span className="block text-xs text-memo-muted">
                              {[
                                inst.instrument_type ? inst.instrument_type.replaceAll("_", " ") : null,
                                inst.rate_type && inst.rate_type !== "fixed" ? inst.rate_type.replaceAll("_", " ") : null,
                                inst.cusip ? `CUSIP ${inst.cusip}` : null,
                                inst.currency !== "USD" ? inst.currency : null,
                              ]
                                .filter(Boolean)
                                .join(", ")}
                            </span>
                          </td>
                          <td className="whitespace-nowrap py-2.5 pl-4 text-right">{Number(inst.coupon).toFixed(3)}%</td>
                          <td className="whitespace-nowrap py-2.5 pl-4">{formatDate(inst.maturity)}</td>
                          <td className="whitespace-nowrap py-2.5 pl-4 text-right">
                            <span className={live ? "font-medium text-memo-ink" : ""}>
                              {inst.currency === "USD"
                                ? formatUsdDisplay(inst.amount_outstanding)
                                : formatUsdMillions(inst.amount_outstanding, inst.currency)}
                            </span>
                            {reduced ? (
                              <span className="block text-xs text-memo-reduced">
                                was{" "}
                                {inst.currency === "USD"
                                  ? formatUsdDisplay(inst.original_amount)
                                  : formatUsdMillions(inst.original_amount, inst.currency)}
                              </span>
                            ) : null}
                          </td>
                          <td className="whitespace-nowrap py-2.5 pl-4">
                            {live ? "outstanding" : inst.status.replaceAll("_", " ")}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>
          </SectionBoundary>

          <SectionBoundary name="timeline">
          <section aria-labelledby="events-heading" className="border-t border-memo-rule pt-8">
            <h3 id="events-heading" className="text-lg font-semibold text-memo-ink">
              Every filing Kernel has read for this company
            </h3>
            <p className="mt-1 max-w-[70ch] text-sm text-memo-muted">
              Newest first. Each links to the alert it raised, if any, and to the agent&apos;s step-by-step
              record.
            </p>
            {issuer.events.length === 0 ? (
              <p className="mt-3 text-sm text-memo-muted">No filings read yet.</p>
            ) : (
              <EventList events={issuer.events} />
            )}
          </section>
          </SectionBoundary>

          <SectionBoundary name="investor-deck charts">
          <VerifiedCharts
            charts={charts}
            showUnverified={showUnverified}
            onToggleUnverified={setShowUnverified}
          />
          </SectionBoundary>
        </div>
      ) : null}
    </PageShell>
  );
}
