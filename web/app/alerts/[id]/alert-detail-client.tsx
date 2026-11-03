"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ErrorLine, LoadingLine, PageShell } from "@/components/loading-error";
import { ReplayVerdictBadge } from "@/components/replay-verdict-badge";
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
  type AlertStatus,
} from "@/lib/api";
import {
  formatBeforeAfter,
  formatDate,
  formatUsdMillions,
  formatUsdMillionsDelta,
} from "@/lib/format";

function changeCell(value: unknown, field?: string | null): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string" || typeof value === "number") {
    const pair = formatBeforeAfter(value, value, field);
    if (pair && !pair.includes("→")) return pair;
    const n = Number(value);
    if (Number.isFinite(n) && Math.abs(n) >= 1000) return formatUsdMillions(n);
    if (Number.isFinite(n) && Math.abs(n) < 100 && (field || "").toLowerCase().includes("coverage")) {
      return `${n.toFixed(2)}×`;
    }
    return String(value);
  }
  return JSON.stringify(value);
}

function localReviewKey(alertId: string): string {
  return `kernel-alert-review:${alertId}`;
}

type LocalReview = { status: "confirmed" | "dismissed"; note?: string };

function readLocalReview(alertId: string): LocalReview | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(localReviewKey(alertId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as LocalReview;
    if (parsed.status === "confirmed" || parsed.status === "dismissed") return parsed;
  } catch {
    /* ignore */
  }
  return null;
}

function writeLocalReview(alertId: string, review: LocalReview): void {
  window.localStorage.setItem(localReviewKey(alertId), JSON.stringify(review));
}

export function AlertDetailClient() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [alert, setAlert] = useState<AlertDetailOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [localStatus, setLocalStatus] = useState<AlertStatus | null>(null);
  const [browserOnly, setBrowserOnly] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    setAlert(null);
    getJson<AlertDetailOut>(`/api/alerts/${id}`)
      .then((d) => {
        if (cancelled) return;
        setAlert(d);
        const writes = d.review_writes_enabled !== false;
        setBrowserOnly(!writes);
        const local = readLocalReview(d.id);
        if (!writes && local) {
          setLocalStatus(local.status);
          if (local.note) setNote(local.note);
        } else {
          setLocalStatus(null);
        }
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  const displayStatus: AlertStatus =
    browserOnly && localStatus ? localStatus : (alert?.status ?? "new");

  async function review(action: "confirm" | "dismiss") {
    if (!alert) return;
    setBusy(true);
    setReviewError(null);
    const next: "confirmed" | "dismissed" =
      action === "confirm" ? "confirmed" : "dismissed";
    if (browserOnly || alert.review_writes_enabled === false) {
      writeLocalReview(alert.id, { status: next, note: note.trim() || undefined });
      setLocalStatus(next);
      setBrowserOnly(true);
      setBusy(false);
      return;
    }
    try {
      const out = await postJson<AlertReviewOut>(`/api/alerts/${alert.id}/review`, {
        action,
        note: note.trim() || null,
      });
      setAlert({ ...alert, status: out.status });
      setLocalStatus(null);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (msg.includes("403") || /disabled|forbidden/i.test(msg)) {
        writeLocalReview(alert.id, { status: next, note: note.trim() || undefined });
        setLocalStatus(next);
        setBrowserOnly(true);
      } else {
        setReviewError(msg);
      }
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
              {alert.issuer.key ? (
                <Link
                  href={`/issuers/${encodeURIComponent(alert.issuer.key)}`}
                  className="font-mono text-xs text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                >
                  {alert.issuer.name}
                </Link>
              ) : (
                <span className="font-mono text-xs text-slate-300">{alert.issuer.name}</span>
              )}
              <span className="font-mono text-xs text-slate-500">
                {formatDate(alert.event_date)}
              </span>
              {alert.live ? (
                <span className="rounded border border-accent/40 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-accent">
                  live
                </span>
              ) : null}
              <MaterialBadge material={alert.material} />
              <AlertStatusChip status={displayStatus} />
              <ReplayVerdictBadge
                verdict={alert.replay_verdict}
                reason={alert.replay_verdict_reason}
              />
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

          <section aria-labelledby="impact-heading" className="rounded-lg border border-slate-700/60 bg-ink-soft/50 p-5">
            <h3 id="impact-heading" className="text-sm font-semibold uppercase tracking-wide text-slate-400">
              What this means
            </h3>
            {alert.impact_sentence ? (
              <p className="mt-2 text-base leading-relaxed text-slate-100">
                {alert.impact_sentence}
              </p>
            ) : (
              <p className="mt-2 text-sm text-slate-400">
                Analyst summary still being written for this alert.
              </p>
            )}
            {alert.affected_buckets.length > 0 ? (
              <ul className="mt-3 space-y-1 font-mono text-xs text-slate-300">
                {alert.affected_buckets.map((b) => (
                  <li key={b.label}>
                    {b.label}: {formatUsdMillions(b.before)} → {formatUsdMillions(b.after)} (
                    {formatUsdMillionsDelta(b.before, b.after)})
                  </li>
                ))}
              </ul>
            ) : null}
            {(alert.agent_affected_buckets?.length ?? 0) > 0 ? (
              <p className="mt-3 font-mono text-[11px] text-slate-500">
                {alert.agent_wall_note ?? "as computed by the agent at the time"}:{" "}
                {alert.agent_affected_buckets!.map((b, i) => (
                  <span key={`agent-${b.label}`}>
                    {i > 0 ? "; " : ""}
                    {b.label} {formatUsdMillions(b.before)} → {formatUsdMillions(b.after)}
                  </span>
                ))}
              </p>
            ) : null}
          </section>

          <section aria-labelledby="wall-heading" className="rounded-lg border border-slate-700/60 bg-ink-soft/50 p-5">
            <h3 id="wall-heading" className="text-sm font-semibold uppercase tracking-wide text-slate-400">
              Before / after wall
            </h3>
            <p className="mt-1 text-xs text-slate-500">
              Year buckets are calendar / fiscal maturity years on the debt schedule. Highlighted
              bars are the buckets this alert moved.
            </p>
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
              <p className="text-sm text-slate-400">
                No extracted findings on this alert
                {alert.run_id
                  ? " — open the agent trace for the steps that ran."
                  : " — the run may still be incomplete."}
              </p>
            ) : (
              <ul className="space-y-6">
                {alert.findings.map((f, idx) => (
                  <li
                    key={`${f.item_key ?? "finding"}-${idx}`}
                    className="rounded-lg border border-slate-700/60 p-4"
                  >
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
              <p className="text-sm text-slate-400">
                No ledger updates were applied for this alert
                {alert.live
                  ? " — live runs may still be waiting on review."
                  : " — the agent may have logged the event without changing instruments."}
              </p>
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
                            {changeCell(before, String(field))}
                          </td>
                          <td className="px-3 py-2 font-mono text-xs text-slate-200">
                            {changeCell(after, String(field))}
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
              <p className="mt-3 text-sm text-slate-400">
                No independent number check was stored for this alert (older runs, or the check was
                skipped).
              </p>
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
              Current status: <AlertStatusChip status={displayStatus} />
            </p>
            {browserOnly ? (
              <p className="mt-2 text-xs text-slate-500">
                Your review, saved in this browser — the shared demo is read-only.
              </p>
            ) : null}
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
                disabled={busy || displayStatus === "confirmed"}
                onClick={() => void review("confirm")}
                className="rounded bg-emerald-600/90 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
              >
                Confirm
              </button>
              <button
                type="button"
                disabled={busy || displayStatus === "dismissed"}
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
