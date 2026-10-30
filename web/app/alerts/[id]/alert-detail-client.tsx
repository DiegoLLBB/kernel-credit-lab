"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ErrorLine, LoadingLine, PageShell } from "@/components/loading-error";
import {
  AlertStatusChip,
  ClaimsStatusChip,
  MaterialBadge,
} from "@/components/status-chip";
import { WallCompare } from "@/components/wall-compare";
import {
  getJson,
  postJson,
  type AlertDetailOut,
  type AlertReviewOut,
} from "@/lib/api";
import { formatDate, formatUsdMillions } from "@/lib/format";

function changeCell(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string" || typeof value === "number") {
    const n = Number(value);
    if (Number.isFinite(n) && Math.abs(n) >= 1000) return formatUsdMillions(n);
    return String(value);
  }
  return JSON.stringify(value);
}

export function AlertDetailClient() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [alert, setAlert] = useState<AlertDetailOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    setAlert(null);
    getJson<AlertDetailOut>(`/api/alerts/${id}`)
      .then((d) => {
        if (!cancelled) setAlert(d);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  async function review(action: "confirm" | "dismiss") {
    if (!alert) return;
    setBusy(true);
    setReviewError(null);
    try {
      const out = await postJson<AlertReviewOut>(`/api/alerts/${alert.id}/review`, {
        action,
        note: note.trim() || null,
      });
      setAlert({ ...alert, status: out.status });
    } catch (e) {
      setReviewError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <PageShell title="Alert detail">
      {error ? <ErrorLine message={error} /> : null}
      {!error && !alert ? <LoadingLine label="Loading alert…" /> : null}

      {alert ? (
        <article className="space-y-10">
          <header className="max-w-3xl">
            <div className="flex flex-wrap items-center gap-2">
              <Link
                href={`/issuers/${encodeURIComponent(alert.issuer.key ?? "")}`}
                className="font-mono text-xs text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
              >
                {alert.issuer.name}
              </Link>
              <span className="font-mono text-xs text-slate-500">
                {formatDate(alert.event_date)}
              </span>
              <MaterialBadge material={alert.material} />
              <AlertStatusChip status={alert.status} />
            </div>
            <h2 className="mt-3 text-2xl font-semibold leading-snug text-white">{alert.title}</h2>
            {alert.summary ? (
              <p className="mt-3 text-sm leading-relaxed text-slate-400">{alert.summary}</p>
            ) : null}
            {alert.materiality_reasons.length > 0 ? (
              <ul className="mt-4 list-disc space-y-1 pl-5 text-sm text-slate-300">
                {alert.materiality_reasons.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            ) : null}
            {alert.run_id ? (
              <p className="mt-4">
                <Link
                  href={`/runs/${alert.run_id}`}
                  className="text-sm text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                >
                  See how the agent got here
                </Link>
              </p>
            ) : null}
          </header>

          <section aria-labelledby="wall-heading" className="rounded-lg border border-slate-700/60 bg-ink-soft/50 p-5">
            <h3 id="wall-heading" className="text-sm font-semibold uppercase tracking-wide text-slate-400">
              Before / after wall
            </h3>
            <div className="mt-4">
              <WallCompare
                beforeWall={alert.before_wall}
                afterWall={alert.after_wall}
                affected={alert.affected_buckets}
              />
            </div>
          </section>

          <section aria-labelledby="evidence-heading">
            <h3 id="evidence-heading" className="mb-4 text-sm font-semibold uppercase tracking-wide text-slate-400">
              Evidence
            </h3>
            {alert.findings.length === 0 ? (
              <p className="text-sm text-slate-400">No findings on this alert.</p>
            ) : (
              <ul className="space-y-6">
                {alert.findings.map((f, idx) => (
                  <li
                    key={`${f.item_key ?? "finding"}-${idx}`}
                    className="rounded-lg border border-slate-700/60 p-4"
                  >
                    {f.item_key ? (
                      <p className="mb-1 font-mono text-[11px] uppercase tracking-wide text-slate-500">
                        {f.item_key}
                      </p>
                    ) : null}
                    <p className="text-sm text-slate-200">{f.statement}</p>
                    {f.citations.length > 0 ? (
                      <ul className="mt-3 space-y-3">
                        {f.citations.map((c) => (
                          <li key={c.id} className="border-l-2 border-accent/50 pl-3">
                            <blockquote className="text-sm italic leading-relaxed text-slate-300">
                              “{c.exact_quote}”
                            </blockquote>
                            <p className="mt-2 font-mono text-[11px] text-slate-500">
                              {[c.document_title, c.location].filter(Boolean).join(" · ")}
                            </p>
                            <div className="mt-2 flex flex-wrap gap-3 text-xs">
                              {c.document_url ? (
                                <a
                                  href={c.document_url}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                                >
                                  Source URL
                                </a>
                              ) : null}
                              {c.filing_id ? (
                                <Link
                                  href={`/documents/${c.filing_id}?fact=${c.id}${c.page != null ? `&page=${c.page}` : ""}`}
                                  className="text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                                >
                                  View in document
                                </Link>
                              ) : null}
                            </div>
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section aria-labelledby="changes-heading">
            <h3 id="changes-heading" className="mb-4 text-sm font-semibold uppercase tracking-wide text-slate-400">
              Data changes
            </h3>
            {alert.applied_changes.length === 0 ? (
              <p className="text-sm text-slate-400">No applied changes.</p>
            ) : (
              <div className="overflow-x-auto rounded-lg border border-slate-700/60">
                <table className="min-w-full text-left text-sm">
                  <thead className="bg-ink-soft font-mono text-[11px] uppercase tracking-wide text-slate-500">
                    <tr>
                      <th className="px-3 py-2 font-medium">Instrument</th>
                      <th className="px-3 py-2 font-medium">Field</th>
                      <th className="px-3 py-2 font-medium">Before</th>
                      <th className="px-3 py-2 font-medium">After</th>
                      <th className="px-3 py-2 font-medium">Effective</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800">
                    {alert.applied_changes.map((c) => {
                      const diff = c.proposed_diff ?? {};
                      const field =
                        c.field ??
                        (typeof diff.field === "string" ? diff.field : null) ??
                        c.action ??
                        "—";
                      let before = c.before;
                      let after = c.after;
                      if (before == null && after == null && c.action === "add_instrument") {
                        before = "—";
                        after =
                          diff.amount_outstanding != null
                            ? `${diff.name ?? c.instrument_key ?? "new"} · ${formatUsdMillions(String(diff.amount_outstanding))} ${String(diff.currency ?? "")}`
                            : JSON.stringify(diff);
                      }
                      return (
                        <tr key={c.id} className="align-top">
                          <td className="px-3 py-2 font-mono text-xs text-slate-300">
                            {c.instrument_key ?? "—"}
                          </td>
                          <td className="px-3 py-2 font-mono text-xs text-slate-400">{field}</td>
                          <td className="px-3 py-2 font-mono text-xs text-slate-400">
                            {changeCell(before)}
                          </td>
                          <td className="px-3 py-2 font-mono text-xs text-slate-200">
                            {changeCell(after)}
                          </td>
                          <td className="px-3 py-2 font-mono text-xs text-slate-400">
                            {formatDate(c.effective_date)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section aria-labelledby="claims-heading" className="rounded-lg border border-slate-700/60 p-5">
            <h3 id="claims-heading" className="text-sm font-semibold uppercase tracking-wide text-slate-400">
              Independent check
            </h3>
            {!alert.claims_check ? (
              <p className="mt-3 text-sm text-slate-400">No claims check recorded.</p>
            ) : (
              <div className="mt-3 space-y-2 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <ClaimsStatusChip status={alert.claims_check.status} />
                  <span className="font-mono text-xs text-slate-500">
                    {[alert.claims_check.model, alert.claims_check.provider]
                      .filter(Boolean)
                      .join(" · ")}
                  </span>
                </div>
                {alert.claims_check.problems.length > 0 ? (
                  <ul className="list-disc space-y-1 pl-5 text-rose-300">
                    {alert.claims_check.problems.map((p) => (
                      <li key={p}>{p}</li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-slate-400">No problems raised.</p>
                )}
              </div>
            )}
          </section>

          <section aria-labelledby="review-heading" className="rounded-lg border border-slate-700/60 bg-ink-soft/40 p-5">
            <h3 id="review-heading" className="text-sm font-semibold uppercase tracking-wide text-slate-400">
              Review
            </h3>
            <p className="mt-2 text-sm text-slate-400">
              Current status: <AlertStatusChip status={alert.status} />
            </p>
            <label className="mt-4 block text-xs text-slate-400">
              Optional note
              <textarea
                className="mt-1 block w-full rounded border border-slate-700 bg-ink px-3 py-2 text-sm text-slate-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                rows={2}
                value={note}
                onChange={(e) => setNote(e.target.value)}
              />
            </label>
            <div className="mt-4 flex flex-wrap gap-3">
              <button
                type="button"
                disabled={busy || alert.status === "confirmed"}
                onClick={() => void review("confirm")}
                className="rounded bg-emerald-600/90 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
              >
                Confirm
              </button>
              <button
                type="button"
                disabled={busy || alert.status === "dismissed"}
                onClick={() => void review("dismiss")}
                className="rounded border border-slate-600 px-4 py-2 text-sm text-slate-200 hover:bg-ink disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
              >
                Dismiss
              </button>
            </div>
            {reviewError ? <div className="mt-3"><ErrorLine message={reviewError} /></div> : null}
          </section>
        </article>
      ) : null}
    </PageShell>
  );
}
