"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { MetricTileCard } from "@/components/credit";
import { EmptyLine, LoadingLine } from "@/components/loading-error";
import type { IssuerSnapshot } from "@/lib/api";
import { formatDate } from "@/lib/format";

/** "press_release filed 2026-10-27" → "press release filed 27 Oct 2026". */
function tidyTitle(t: string | null | undefined): string {
  return (t ?? "")
    .replace(/_/g, " ")
    .replace(/\b(\d{4})-(\d{2})-(\d{2})\b/g, (m) => formatDate(m));
}

export function CreditSnapshot({
  snapshot,
  loading,
  asOf,
  watch,
}: {
  snapshot: IssuerSnapshot | null;
  loading: boolean;
  asOf?: string;
  /** "What to watch" lines, placed under the written summary. */
  watch?: ReactNode;
}) {
  if (loading && !snapshot) {
    return (
      <LoadingLine
        label={
          asOf
            ? `Updating credit snapshot for ${formatDate(asOf)}…`
            : "Loading credit snapshot…"
        }
      />
    );
  }
  if (!snapshot) {
    return (
      <EmptyLine>
        Credit snapshot unavailable
        {asOf ? ` for ${formatDate(asOf)}` : ""} — metrics may still appear below from the issuer
        filing schedule.
      </EmptyLine>
    );
  }

  const insight = snapshot.insight;
  const valued = snapshot.metrics.filter((t) => t.value !== null);

  return (
    <section aria-labelledby="snapshot-heading" className="border-t border-memo-rule pt-8">
      <h3 id="snapshot-heading" className="text-lg font-semibold text-memo-ink">
        Credit snapshot, as of {formatDate(snapshot.as_of)}
      </h3>

      {insight && (insight.headline || insight.sentences.length > 0) ? (
        <div className="mt-3 max-w-3xl">
          {insight.headline ? (
            <p className="text-2xl font-semibold leading-tight text-memo-ink sm:text-[30px] sm:leading-[38px]">{insight.headline}</p>
          ) : null}
          {insight.sentences.map((s, i) => (
            <p key={i} className="mt-3 max-w-[70ch] text-base leading-7 text-memo-ink">
              {s}
            </p>
          ))}
          {valued.length > 0 || snapshot.wall ? (
            <details className="mt-3 text-xs leading-relaxed text-memo-muted"><summary className="cursor-pointer">Where these figures come from</summary><p className="mt-2">
              
              {valued.map((t, i) => (
                <span key={t.metric}>
                  {i > 0 ? ", " : null}
                  <a href={`#metric-${t.metric}`} className="text-memo-info underline underline-offset-2">
                    {t.label} ({t.fiscal_label ?? "latest"})
                  </a>
                </span>
              ))}
              {(snapshot.wall?.changes ?? []).map((c) => (
                <span key={`${c.label}-${c.alert_id ?? "na"}`}>
                  {", "}
                  {c.alert_id ? (
                    <Link href={`/alerts/${c.alert_id}`} className="text-memo-info underline underline-offset-2">
                      {c.label} change{c.alert_title ? `: ${c.alert_title}` : ""}
                    </Link>
                  ) : (
                    `${c.label} change`
                  )}
                </span>
              ))}
            </p></details>
          ) : null}
          <details className="mt-1 text-xs leading-relaxed text-memo-muted">
            <summary className="cursor-pointer">How this summary was written</summary>
            <p className="mt-2">
              Drafted from computed filing numbers only, then checked sentence by sentence by an
              independent model against the same numbers
              {[insight.how.model, insight.how.provider].filter(Boolean).length > 0
                ? ` (${[insight.how.model, insight.how.provider].filter(Boolean).join(", ")})`
                : ""}
              . Any sentence whose numbers didn&apos;t match was dropped
              {insight.how.dropped.length > 0
                ? ` (${insight.how.dropped.length} dropped, listed below)`
                : " (none dropped)"}
              . This is description, not advice.
            </p>
            {insight.how.dropped.length > 0 ? (
              <ul className="mt-2 space-y-1">
                {insight.how.dropped.map((d, i) => (
                  <li key={i}>
                    Dropped: {String(d.sentence ?? d.text ?? JSON.stringify(d))}
                    {d.reason ? `: ${String(d.reason)}` : ""}
                  </li>
                ))}
              </ul>
            ) : null}
          </details>
        </div>
      ) : snapshot.insight_pending ? (
        <p className="mt-3 text-sm text-memo-muted">
          The written summary covers the latest figures; move the date slider to today to read it.
        </p>
      ) : null}

      {watch}

      {snapshot.metrics.length > 0 ? (
        <>
          {snapshot.metrics.some((t) => /fewer than 3/i.test(t.peer?.line ?? "")) ? (
            <p className="mt-6 hidden text-sm text-memo-muted">
              Sector medians appear once a sector has at least three companies with the metric.
            </p>
          ) : null}
          <h4 className="mt-10 text-lg font-semibold text-memo-ink">Credit figures</h4>
          <p className="mt-1 max-w-[70ch] text-sm text-memo-muted">
            From the latest SEC filings, with the change from a year earlier and the rank across the
            watchlist.
          </p>
          <div className="mt-3 grid gap-x-8 gap-y-2 sm:grid-cols-2 lg:grid-cols-3">
            {snapshot.metrics
              .filter((t) => t.value !== null || t.missing_reason === "not meaningful for banks")
              .map((t) => (
                <MetricTileCard key={t.metric} tile={t} />
              ))}
          </div>
          {snapshot.metrics.some((t) => t.value === null && t.missing_reason !== "not meaningful for banks") ? (
            <p className="mt-4 text-sm text-memo-muted">
              Not available from this company&apos;s filings:{" "}
              {snapshot.metrics
                .filter((t) => t.value === null && t.missing_reason !== "not meaningful for banks")
                .map((t) => t.label)
                .join(", ")}
              .
            </p>
          ) : null}
        </>
      ) : null}

      {snapshot.timeline.length > 0 ? (
        <div className="mt-10">
          <h4 className="text-lg font-semibold text-memo-ink">This year</h4>
          <ol className="mt-3 border-t border-memo-ink">
            {snapshot.timeline.map((item, i) => (
              <li
                key={`${item.event_date}-${i}`}
                className="grid grid-cols-1 gap-x-6 border-b border-memo-rule py-3 sm:grid-cols-[7.5rem_1fr]"
              >
                <span className="text-sm text-memo-muted">{formatDate(item.event_date)}</span>
                <span>
                  <span className="block text-base text-memo-ink">{tidyTitle(item.effect ?? item.title)}</span>
                  {item.effect && item.title && item.effect !== item.title ? (
                    <span className="block text-sm text-memo-muted">{tidyTitle(item.title)}</span>
                  ) : null}
                  {item.alert_id ? (
                    <Link
                      href={`/alerts/${item.alert_id}`}
                      className="mt-1 inline-block text-sm text-memo-info underline underline-offset-2"
                    >
                      Open the alert and its evidence
                    </Link>
                  ) : null}
                </span>
              </li>
            ))}
          </ol>
        </div>
      ) : null}
    </section>
  );
}
