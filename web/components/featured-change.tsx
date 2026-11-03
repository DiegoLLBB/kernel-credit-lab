"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { DebtByYear } from "@/components/debt-by-year";
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

  return (
    <section aria-labelledby="featured-heading" className="mb-14 border-t-[3px] border-memo-ink pt-6">
      <p className="text-sm text-memo-muted">
        Latest change{alert.live ? ", found live" : ""}: {alert.issuer.name}, {formatDate(alert.event_date)}
      </p>
      <h2
        id="featured-heading"
        className="mt-2 max-w-[32ch] text-2xl font-semibold leading-tight text-memo-ink sm:text-[30px] sm:leading-[38px]"
      >
        {alert.impact_sentence ?? alert.title}
      </h2>
      <div className="mt-6">
        <DebtByYear
          beforeWall={alert.before_wall}
          afterWall={alert.after_wall}
          affected={alert.affected_buckets}
          findings={alert.findings}
          animate
        />
      </div>
      <p className="mt-5 text-sm">
        <Link href={`/alerts/${alert.id}`} className="font-medium text-memo-info underline underline-offset-2">
          Open the alert: evidence, record changes and the independent check
        </Link>
      </p>
    </section>
  );
}
