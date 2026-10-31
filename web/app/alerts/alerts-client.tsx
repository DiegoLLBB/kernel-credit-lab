"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { AlertStatusChip, MaterialBadge } from "@/components/status-chip";
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

export function AlertsClient() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const status = (searchParams.get("status") ?? "") as AlertStatus | "";
  const issuer = searchParams.get("issuer") ?? "";
  const offset = Math.max(0, Number(searchParams.get("offset") ?? "0") || 0);

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
  }, [status, issuer, offset]);

  function updateQuery(next: { status?: string; issuer?: string; offset?: number }) {
    const qs = new URLSearchParams();
    const s = next.status !== undefined ? next.status : status;
    const i = next.issuer !== undefined ? next.issuer : issuer;
    const o = next.offset !== undefined ? next.offset : 0;
    if (s) qs.set("status", s);
    if (i) qs.set("issuer", i);
    if (o > 0) qs.set("offset", String(o));
    const q = qs.toString();
    router.push(q ? `/alerts?${q}` : "/alerts");
  }

  return (
    <div>
      <header className="mb-6">
        <h2 className="text-2xl font-semibold text-white">Alerts</h2>
        <p className="mt-1 text-sm text-slate-400">
          Material maturity-wall changes from the Jan–Oct 2026 replay.
        </p>
      </header>

      <form
        className="mb-6 flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-end"
        onSubmit={(e) => e.preventDefault()}
      >
        <label className="block text-xs text-slate-400">
          Status
          <select
            className="mt-1 block w-full rounded border border-slate-700 bg-ink-soft px-3 py-2 text-sm text-slate-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:w-40"
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
        <label className="block text-xs text-slate-400">
          Issuer
          <select
            className="mt-1 block w-full rounded border border-slate-700 bg-ink-soft px-3 py-2 text-sm text-slate-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:w-56"
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
      </form>

      {error ? <ErrorLine message={error} /> : null}
      {loading ? <LoadingLine label="Loading alerts…" /> : null}

      {!loading && data && data.items.length === 0 ? (
        <EmptyLine>No alerts yet. Run the replay.</EmptyLine>
      ) : null}

      {!loading && data && data.items.length > 0 ? (
        <>
          <ul className="divide-y divide-slate-800 overflow-hidden rounded-lg border border-slate-700/60">
            {data.items.map((a) => (
              <li key={a.id}>
                <Link
                  href={`/alerts/${a.id}`}
                  className="block px-4 py-3 hover:bg-ink-soft/70 focus-visible:bg-ink-soft focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-accent"
                >
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
                  <p className="mt-1 text-sm text-slate-100">{a.title}</p>
                  {a.impact_sentence ? (
                    <p className="mt-1 line-clamp-2 text-xs text-slate-300">
                      {a.impact_sentence}
                    </p>
                  ) : a.summary ? (
                    <p className="mt-1 line-clamp-2 text-xs text-slate-500">{a.summary}</p>
                  ) : null}
                </Link>
              </li>
            ))}
          </ul>
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-sm text-slate-400">
            <span className="font-mono text-xs">
              {data.offset + 1}–{Math.min(data.offset + data.items.length, data.total)} of {data.total}
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={offset <= 0}
                className="rounded border border-slate-700 px-3 py-1.5 text-xs enabled:hover:bg-ink-soft disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                onClick={() => updateQuery({ offset: Math.max(0, offset - PAGE_SIZE) })}
              >
                Previous
              </button>
              <button
                type="button"
                disabled={offset + PAGE_SIZE >= data.total}
                className="rounded border border-slate-700 px-3 py-1.5 text-xs enabled:hover:bg-ink-soft disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
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
