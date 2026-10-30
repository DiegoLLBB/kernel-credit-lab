"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { EmptyLine, ErrorLine, LoadingLine, PageShell } from "@/components/loading-error";
import { getJson, type IssuerListOut } from "@/lib/api";

export function IssuersClient() {
  const [data, setData] = useState<IssuerListOut | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getJson<IssuerListOut>("/api/issuers?limit=50")
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <PageShell title="Issuers">
      <header className="mb-6">
        <h2 className="text-2xl font-semibold text-white">Issuers</h2>
        <p className="mt-1 text-sm text-slate-400">
          Nine USD investment-grade names on the Kernel watchlist.
        </p>
      </header>

      {error ? <ErrorLine message={error} /> : null}
      {!error && !data ? <LoadingLine /> : null}
      {data && data.items.length === 0 ? <EmptyLine>No issuers loaded.</EmptyLine> : null}

      {data && data.items.length > 0 ? (
        <ul className="divide-y divide-slate-800 overflow-hidden rounded-lg border border-slate-700/60">
          {data.items.map((iss) => (
            <li key={iss.id}>
              <Link
                href={`/issuers/${encodeURIComponent(iss.key ?? iss.id)}`}
                className="flex flex-col gap-1 px-4 py-3 hover:bg-ink-soft/70 focus-visible:bg-ink-soft focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-accent sm:flex-row sm:items-center sm:justify-between"
              >
                <div>
                  <p className="text-sm text-slate-100">{iss.name}</p>
                  <p className="font-mono text-[11px] text-slate-500">
                    {[iss.ticker, iss.key, iss.sector].filter(Boolean).join(" · ")}
                  </p>
                </div>
                <p className="font-mono text-xs text-slate-500">
                  {iss.parent_key ? `parent: ${iss.parent_key}` : null}
                  {iss.subsidiary_count > 0
                    ? `${iss.parent_key ? " · " : ""}${iss.subsidiary_count} subsidiaries`
                    : null}
                </p>
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
    </PageShell>
  );
}
