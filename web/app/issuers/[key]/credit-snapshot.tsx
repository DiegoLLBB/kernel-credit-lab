"use client";

import Link from "next/link";
import { MetricTileCard } from "@/components/credit";
import { LoadingLine } from "@/components/loading-error";
import type { IssuerSnapshot } from "@/lib/api";
import { formatDate } from "@/lib/format";

export function CreditSnapshot({
  snapshot,
  loading,
}: {
  snapshot: IssuerSnapshot | null;
  loading: boolean;
}) {
  if (loading && !snapshot) return <LoadingLine label="Loading credit snapshot…" />;
  if (!snapshot) return null;

  const insight = snapshot.insight;
  const valued = snapshot.metrics.filter((t) => t.value !== null);

  return (
    <section aria-labelledby="snapshot-heading" className="rounded-lg border border-slate-700/60 bg-ink-soft/40 p-5">
      <h3 id="snapshot-heading" className="text-sm font-semibold uppercase tracking-wide text-slate-400">
        Credit snapshot · as of {formatDate(snapshot.as_of)}
      </h3>

      {insight && (insight.headline || insight.sentences.length > 0) ? (
        <div className="mt-3 max-w-3xl">
          {insight.headline ? (
            <p className="text-xl font-semibold leading-snug text-white">{insight.headline}</p>
          ) : null}
          {insight.sentences.map((s, i) => (
            <p key={i} className="mt-2 text-sm leading-relaxed text-slate-300">
              {s}
            </p>
          ))}
          {valued.length > 0 || snapshot.wall ? (
            <p className="mt-3 text-xs leading-relaxed text-slate-500">
              Figures above come from:{" "}
              {valued.map((t, i) => (
                <span key={t.metric}>
                  {i > 0 ? " · " : null}
                  <a
                    href={`#metric-${t.metric}`}
                    className="text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                  >
                    {t.label} ({t.fiscal_label ?? "latest"})
                  </a>
                </span>
              ))}
              {(snapshot.wall?.changes ?? []).map((c) => (
                <span key={`${c.label}-${c.alert_id ?? "na"}`}>
                  {" · "}
                  {c.alert_id ? (
                    <Link
                      href={`/alerts/${c.alert_id}`}
                      className="text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                    >
                      {c.label} change{c.alert_title ? `: ${c.alert_title}` : ""}
                    </Link>
                  ) : (
                    `${c.label} change`
                  )}
                </span>
              ))}
            </p>
          ) : null}
          <details className="mt-3 text-xs leading-relaxed text-slate-500">
            <summary className="cursor-pointer text-slate-400">How this was written</summary>
            <p className="mt-2">
              Drafted from computed filing numbers only, then checked sentence by sentence by an
              independent model against the same numbers
              {[insight.how.model, insight.how.provider].filter(Boolean).length > 0
                ? ` (${[insight.how.model, insight.how.provider].filter(Boolean).join(" · ")})`
                : ""}
              . Any sentence whose numbers didn&apos;t match was dropped
              {insight.how.dropped.length > 0
                ? ` (${insight.how.dropped.length} dropped — see below)`
                : " (none dropped)"}
              . This is description, not advice.
            </p>
            {insight.how.dropped.length > 0 ? (
              <ul className="mt-2 list-disc space-y-1 pl-5">
                {insight.how.dropped.map((d, i) => (
                  <li key={i}>
                    Dropped: {String(d.sentence ?? d.text ?? JSON.stringify(d))}
                    {d.reason ? ` — ${String(d.reason)}` : ""}
                  </li>
                ))}
              </ul>
            ) : null}
          </details>
        </div>
      ) : snapshot.insight_pending ? (
        <p className="mt-3 text-sm text-slate-400">
          Analyst summary still being written for this date.
        </p>
      ) : null}

      {snapshot.metrics.length > 0 ? (
        <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {snapshot.metrics.map((t) => (
            <MetricTileCard key={t.metric} tile={t} />
          ))}
        </div>
      ) : null}

      {snapshot.timeline.length > 0 ? (
        <div className="mt-6">
          <h4 className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
            This year
          </h4>
          <ol className="space-y-3 border-l border-slate-700/80 pl-4">
            {snapshot.timeline.map((item, i) => (
              <li key={`${item.event_date}-${i}`} className="text-sm">
                <p className="font-mono text-[11px] text-slate-500">
                  {formatDate(item.event_date)}
                </p>
                <p className="text-slate-200">{item.title}</p>
                {item.effect ? <p className="text-slate-400">{item.effect}</p> : null}
                {item.alert_id ? (
                  <p className="mt-1 text-xs">
                    <Link
                      href={`/alerts/${item.alert_id}`}
                      className="text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                    >
                      See the alert
                    </Link>
                  </p>
                ) : null}
              </li>
            ))}
          </ol>
        </div>
      ) : null}
    </section>
  );
}
