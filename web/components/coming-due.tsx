"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { getJson, type UpcomingMaturities } from "@/lib/api";
import { formatDate, formatUsdDisplay } from "@/lib/format";

/** Bonds and loans in Kernel's records that mature in the next 12 months, soonest first. */
export function ComingDue() {
  const [data, setData] = useState<UpcomingMaturities | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getJson<UpcomingMaturities>("/api/maturities/upcoming?months=12")
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) return null;

  return (
    <section aria-labelledby="due-heading" className="mb-12">
      <h2 id="due-heading" className="text-lg font-semibold text-memo-ink">
        Coming due in the next 12 months
      </h2>
      <p className="mt-1 max-w-[70ch] text-sm text-memo-muted">
        Bonds and loans Kernel tracks one by one, soonest first. Each company&apos;s full 12-month total from
        its filings is on its page.
      </p>
      {!data ? (
        <p className="mt-3 text-sm text-memo-muted">Loading…</p>
      ) : data.instruments.length === 0 ? (
        <p className="mt-3 text-sm text-memo-muted">Nothing tracked matures before {formatDate(data.until)}.</p>
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="min-w-full border-collapse text-left text-sm">
            <thead>
              <tr className="border-b border-memo-ink text-xs text-memo-muted">
                <th className="py-2 pr-4 font-medium">Matures</th>
                <th className="py-2 pr-4 font-medium">Company</th>
                <th className="py-2 pr-4 font-medium">Bond or loan</th>
                <th className="py-2 pr-4 text-right font-medium">Amount</th>
              </tr>
            </thead>
            <tbody>
              {data.instruments.map((r, i) => (
                <tr key={`${r.instrument_key ?? r.instrument_name}-${i}`} className="border-b border-memo-rule">
                  <td className="whitespace-nowrap py-2 pr-4 text-memo-ink">{formatDate(r.maturity_date)}</td>
                  <td className="py-2 pr-4">
                    {r.issuer.key ? (
                      <Link
                        href={`/issuers/${encodeURIComponent(r.issuer.key)}`}
                        className="text-memo-ink underline-offset-2 hover:underline"
                      >
                        {r.issuer.name}
                      </Link>
                    ) : (
                      r.issuer.name
                    )}
                  </td>
                  <td className="py-2 pr-4 text-memo-muted">{r.instrument_name}</td>
                  <td className="whitespace-nowrap py-2 pr-4 text-right font-medium text-memo-ink">
                    {r.currency === "USD" || !r.currency
                      ? formatUsdDisplay(r.amount)
                      : `${formatUsdDisplay(r.amount).replace("$", "")} ${r.currency}`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
