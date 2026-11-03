"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ChangeQuote, DebtByYear } from "@/components/debt-by-year";
import { getJson, type AlertDetailOut } from "@/lib/api";
import { formatDate } from "@/lib/format";

/** The newest agent alert, shown in full on the overview: the change, the chart and its quote. */
export function FeaturedChange({ alertId }: { alertId: string }) {
  const [alert, setAlert] = useState<AlertDetailOut | null>(null);

  useEffect(() => {
    let cancelled = false;
    getJson<AlertDetailOut>(`/api/alerts/${alertId}`)
      .then((d) => {
        if (!cancelled) setAlert(d);
      })
      .catch(() => {
        /* the feed below still lists it */
      });
    return () => {
      cancelled = true;
    };
  }, [alertId]);

  if (!alert) return null;

  const checked = alert.claims_check?.status === "pass";

  return (
    <section aria-labelledby="featured-heading" className="mb-16 border-t-[3px] border-memo-ink pt-6">
      <p className="text-sm text-memo-muted">
        Latest change{alert.live ? ", found live" : ""}: {alert.issuer.name}, {formatDate(alert.event_date)}
      </p>
      <div className="mt-3 grid gap-x-12 gap-y-8 md:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] md:items-end">
        <div className="md:pb-10">
          <h2
            id="featured-heading"
            className="text-2xl font-semibold leading-tight text-memo-ink sm:text-[30px] sm:leading-[38px]"
          >
            {alert.impact_sentence ?? alert.title}
          </h2>
          <p className="mt-4 max-w-[46ch] text-base text-memo-muted">
            {alert.live ? "Kernel's agent read the filing on its own" : "Found when Kernel replayed 2026's filings"}
            {checked ? ", and a second model checked every claim before it was published." : "."}
          </p>
          <p className="mt-5 text-sm">
            <Link
              href={`/alerts/${alert.id}`}
              className="font-medium text-memo-info underline underline-offset-2"
            >
              Open the alert: evidence, record changes and the independent check
            </Link>
          </p>
        </div>
        <div className="hidden md:block">
          <DebtByYear
            beforeWall={alert.before_wall}
            afterWall={alert.after_wall}
            affected={alert.affected_buckets}
            findings={alert.findings}
            animate
            large
            showQuote={false}
          />
        </div>
        <div className="md:hidden">
          <DebtByYear
            beforeWall={alert.before_wall}
            afterWall={alert.after_wall}
            affected={alert.affected_buckets}
            findings={alert.findings}
            animate
            showQuote={false}
          />
        </div>
      </div>
      <div className="mt-8 border-t border-memo-rule pt-6">
        <ChangeQuote affected={alert.affected_buckets} findings={alert.findings} />
      </div>
    </section>
  );
}
