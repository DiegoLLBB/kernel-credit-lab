"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ReplayVerdictBadge } from "@/components/replay-verdict-badge";
import { EmptyLine, ErrorLine, LoadingLine } from "@/components/loading-error";
import {
  getJson,
  type AlertListOut,
  type AlertStatus,
  type IssuerListOut,
} from "@/lib/api";
import { formatDate } from "@/lib/format";

const PAGE_SIZE = 20;
const STATUSES: Array<AlertStatus | ""> = ["", "new", "confirmed", "dismissed"];
const SIZES = [
  { value: "", label: "Any size" },
  { value: "500000000", label: "$500M or more" },
  { value: "1000000000", label: "$1bn or more" },
] as const;

export function AlertsClient() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const status = (searchParams.get("status") ?? "") as AlertStatus | "";
  const issuer = searchParams.get("issuer") ?? "";
  const offset = Math.max(0, Number(searchParams.get("offset") ?? "0") || 0);
  const minSize = SIZES.some((x) => x.value === (searchParams.get("min") ?? ""))
    ? (searchParams.get("min") ?? "")
    : "";

  const [data, setData] = useState<AlertListOut | null>(null);
  const [issuers, setIssuers] = useState<IssuerListOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getJson<IssuerListOut>("/api/issuers?limit=50")
      .then(setIssuers)
      .catch(() => setIssuers(null));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const qs = new URLSearchParams();
    qs.set("limit", String(PAGE_SIZE));
    qs.set("offset", String(offset));
    if (status) qs.set("status", status);
    if (issuer) qs.set("issuer", issuer);
    if (minSize) qs.set("min_change_usd", minSize);
    getJson<AlertListOut>(`/api/alerts?${qs.toString()}`)
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [status, issuer, offset, minSize]);

  function updateQuery(next: { status?: string; issuer?: string; min?: string; offset?: number }) {
    const qs = new URLSearchParams();
    const s = next.status !== undefined ? next.status : status;
    const i = next.issuer !== undefined ? next.issuer : issuer;
    const m = next.min !== undefined ? next.min : minSize;
    const o = next.offset !== undefined ? next.offset : 0;
    if (s) qs.set("status", s);
    if (i) qs.set("issuer", i);
    if (m) qs.set("min", m);
    if (o > 0) qs.set("offset", String(o));
    const q = qs.toString();
    router.push(q ? `/alerts?${q}` : "/alerts");
  }

  return (
    <div>
      <header className="mb-8">
        <h2 className="text-3xl font-semibold text-memo-ink sm:text-[40px] sm:leading-[46px]">Alerts</h2>
        <p className="mt-3 max-w-[62ch] text-lg text-memo-ink">
          Every material change to what these companies owe: from the replay of real 2026 filings, and
          from new filings since live monitoring began on 2 Nov.
        </p>
      </header>

      <form
        className="mb-6 flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-end"
        onSubmit={(e) => e.preventDefault()}
      >
        <label className="block text-sm text-memo-muted">
          Status
          <select
            className="mt-1 block w-full rounded-md border border-memo-rule bg-[#FBFCFA] px-3 py-2 text-sm text-memo-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:w-40"
            value={status}
            onChange={(e) => updateQuery({ status: e.target.value, offset: 0 })}
          >
            {STATUSES.map((s) => (
              <option key={s || "all"} value={s}>
                {s || "all"}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-sm text-memo-muted">
          Issuer
          <select
            className="mt-1 block w-full rounded-md border border-memo-rule bg-[#FBFCFA] px-3 py-2 text-sm text-memo-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:w-56"
            value={issuer}
            onChange={(e) => updateQuery({ issuer: e.target.value, offset: 0 })}
          >
            <option value="">all</option>
            {(issuers?.items ?? []).map((iss) => (
              <option key={iss.id} value={iss.key ?? ""}>
                {iss.name}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-sm text-memo-muted">
          Size of change
          <select
            className="mt-1 block w-full rounded-md border border-memo-rule bg-[#FBFCFA] px-3 py-2 text-sm text-memo-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:w-48"
            value={minSize}
            onChange={(e) => updateQuery({ min: e.target.value, offset: 0 })}
          >
            {SIZES.map((x) => (
              <option key={x.value || "any"} value={x.value}>
                {x.label}
              </option>
            ))}
          </select>
        </label>
      </form>
      {minSize ? (
        <p className="-mt-3 mb-6 text-sm text-memo-muted">
          Showing debt changes where at least one maturity year moved by{" "}
          {SIZES.find((x) => x.value === minSize)?.label.replace(" or more", "")} or more. Ratio alerts are
          hidden while a size is set.
        </p>
      ) : null}

      {error ? <ErrorLine message={error} /> : null}
      {loading ? <LoadingLine label="Loading alerts…" /> : null}

      {!loading && data && data.items.length === 0 ? (
        <EmptyLine>
          No material changes yet. Alerts appear after the historical replay (or a live filing) finds a
          debt move large enough to surface here.
        </EmptyLine>
      ) : null}

      {!loading && data && data.items.length > 0 ? (
        <>
          <ol className="border-t border-memo-ink">
            {data.items.map((a) => (
              <li key={a.id} className="border-b border-memo-rule">
                <Link
                  href={`/alerts/${a.id}`}
                  className="grid grid-cols-1 gap-x-6 gap-y-1 py-4 hover:bg-[#EEF0EC] sm:grid-cols-[7.5rem_1fr]"
                >
                  <span className="text-sm text-memo-muted">{formatDate(a.event_date)}</span>
                  <span>
                    <span className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
                      <span className="font-semibold text-memo-ink">{a.issuer.name}</span>
                      {a.live ? (
                        <span className="rounded border border-memo-info/50 px-1.5 py-0.5 text-xs text-memo-info">
                          found live
                        </span>
                      ) : null}
                      {a.kind === "metric" ? (
                        <span className="rounded border border-memo-rule px-1.5 py-0.5 text-xs text-memo-muted">
                          credit metric
                        </span>
                      ) : (
                        <ReplayVerdictBadge verdict={a.replay_verdict} reason={a.replay_verdict_reason} />
                      )}
                    </span>
                    <span className="mt-1 block max-w-[70ch] text-base text-memo-ink">
                      {a.impact_sentence ?? a.title}
                    </span>
                    {a.impact_sentence ? (
                      <span className="mt-0.5 block max-w-[70ch] text-sm text-memo-muted">{a.title}</span>
                    ) : null}
                  </span>
                </Link>
              </li>
            ))}
          </ol>
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-sm text-slate-400">
            <span className="tabular-nums text-xs">
              {data.offset + 1}–{Math.min(data.offset + data.items.length, data.total)} of {data.total}
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={offset <= 0}
                className="rounded-md border border-memo-ink px-3 py-1.5 text-sm text-memo-ink enabled:hover:bg-[#EEF0EC] disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                onClick={() => updateQuery({ offset: Math.max(0, offset - PAGE_SIZE) })}
              >
                Previous
              </button>
              <button
                type="button"
                disabled={offset + PAGE_SIZE >= data.total}
                className="rounded-md border border-memo-ink px-3 py-1.5 text-sm text-memo-ink enabled:hover:bg-[#EEF0EC] disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                onClick={() => updateQuery({ offset: offset + PAGE_SIZE })}
              >
                Next
              </button>
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}
