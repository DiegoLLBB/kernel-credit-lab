"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ErrorLine, LoadingLine, PageShell } from "@/components/loading-error";
import { ReplayVerdictBadge } from "@/components/replay-verdict-badge";
import { AlertStatusChip } from "@/components/status-chip";
import { DebtByYear, sourceLine, yearLabel } from "@/components/debt-by-year";
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
  formatUsdDisplay,
  formatUsdMillions,
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

  const quoteCount = alert ? alert.findings.reduce((n, f) => n + f.citations.length, 0) : 0;
  const claims = alert?.claims_check ?? null;
  const claimsOk = claims ? /^(pass|supported|ok)$/i.test(claims.status ?? "") : false;

  return (
    <PageShell title="Alert detail">
      {error ? <ErrorLine message={error} /> : null}
      {!error && !alert ? <LoadingLine label="Loading alert…" /> : null}

      {alert ? (
        <article className="max-w-4xl">
          <header>
            <p className="flex flex-wrap items-center gap-x-3 gap-y-2 text-sm text-memo-muted">
              {alert.issuer.key ? (
                <Link
                  href={`/issuers/${encodeURIComponent(alert.issuer.key)}`}
                  className="font-semibold text-memo-ink underline-offset-2 hover:underline"
                >
                  {alert.issuer.name}
                </Link>
              ) : (
                <span className="font-semibold text-memo-ink">{alert.issuer.name}</span>
              )}
              <span>{formatDate(alert.event_date)}</span>
              {alert.live ? (
                <span className="rounded border border-memo-info/50 px-1.5 py-0.5 text-xs text-memo-info">
                  found live
                </span>
              ) : null}
              <ReplayVerdictBadge verdict={alert.replay_verdict} reason={alert.replay_verdict_reason} />
            </p>
            <h2 className="mt-4 max-w-[30ch] text-2xl font-semibold leading-tight text-memo-ink sm:text-[34px] sm:leading-[42px]">
              {alert.impact_sentence ?? alert.title}
            </h2>
            {alert.impact_sentence ? (
              <p className="mt-3 max-w-[70ch] text-base text-memo-muted">{alert.title}</p>
            ) : null}
            {alert.materiality_reasons.length > 0 ? (
              <p className="mt-3 max-w-[70ch] text-sm text-memo-muted">
                Why it matters:{" "}
                {alert.materiality_reasons
                  .map((r) => r.replace(/\$([\d,]{10,})/g, (_, n: string) => formatUsdDisplay(n)))
                  .join("; ")}
                .
              </p>
            ) : null}
          </header>

          <section aria-labelledby="wall-heading" className="mt-10">
            <h3 id="wall-heading" className="text-lg font-semibold text-memo-ink">
              Debt due by year
            </h3>
            <p className="mt-1 max-w-[70ch] text-sm text-memo-muted">
              What this change does to the company&apos;s maturities, recomputed from Kernel&apos;s
              records.
            </p>
            <div className="mt-5">
              <DebtByYear
                beforeWall={alert.before_wall}
                afterWall={alert.after_wall}
                affected={alert.affected_buckets}
                findings={alert.findings}
                animate
              />
            </div>
            {(alert.agent_affected_buckets?.length ?? 0) > 0 ? (
              <p className="mt-4 text-xs text-memo-muted">
                {alert.agent_wall_note ?? "As computed by the agent at the time"}:{" "}
                {alert.agent_affected_buckets!.map((b, i) => (
                  <span key={`agent-${b.label}`}>
                    {i > 0 ? "; " : ""}
                    {yearLabel(b.label)} {formatUsdDisplay(b.before)} → {formatUsdDisplay(b.after)}
                  </span>
                ))}
              </p>
            ) : null}
          </section>

          <section aria-labelledby="evidence-heading" className="mt-12 border-t border-memo-rule pt-8">
            <h3 id="evidence-heading" className="text-lg font-semibold text-memo-ink">
              What the filing says
            </h3>
            <p className="mt-1 text-sm text-memo-muted">
              {quoteCount > 0
                ? `${quoteCount} exact ${quoteCount === 1 ? "quote" : "quotes"}, each checked word for word against the document.`
                : "No quotes were recorded for this alert."}
            </p>
            {alert.findings.length === 0 ? (
              <p className="mt-4 text-sm text-memo-muted">
                {alert.run_id
                  ? "Open the agent trace for the steps that ran."
                  : "The run may still be incomplete."}
              </p>
            ) : (
              <ol className="mt-6 space-y-8">
                {alert.findings.map((f, idx) => (
                  <li key={`${f.item_key ?? "finding"}-${idx}`}>
                    <p className="max-w-[70ch] text-base font-medium text-memo-ink">{f.statement}</p>
                    {f.citations.map((c) => (
                      <div key={c.id} className="mt-3">
                        <blockquote className="max-w-[68ch] leading-8">
                          <span className="quote-mark">{c.exact_quote}</span>
                        </blockquote>
                        <p className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-xs text-memo-muted">
                          <span>{sourceLine(c)}</span>
                          {c.filing_id ? (
                            <Link
                              href={`/documents/${c.filing_id}?fact=${c.id}${c.page != null ? `&page=${c.page}` : ""}`}
                              className="text-memo-info underline underline-offset-2"
                            >
                              Open in the document
                            </Link>
                          ) : null}
                          {c.document_url ? (
                            <a
                              href={c.document_url}
                              target="_blank"
                              rel="noreferrer"
                              className="text-memo-info underline underline-offset-2"
                            >
                              Original on sec.gov or the issuer site
                            </a>
                          ) : null}
                        </p>
                      </div>
                    ))}
                  </li>
                ))}
              </ol>
            )}
          </section>

          <section aria-labelledby="changes-heading" className="mt-12 border-t border-memo-rule pt-8">
            <h3 id="changes-heading" className="text-lg font-semibold text-memo-ink">
              What Kernel changed in its records
            </h3>
            {alert.applied_changes.length === 0 ? (
              <p className="mt-2 text-sm text-memo-muted">
                {alert.live
                  ? "No record changes yet; live runs may be waiting on review."
                  : "The agent logged this event without changing any instrument."}
              </p>
            ) : (
              <div className="mt-4 overflow-x-auto">
                <table className="min-w-full border-collapse text-left text-sm">
                  <thead>
                    <tr className="border-b border-memo-ink text-xs text-memo-muted">
                      <th className="py-2 pr-4 font-medium">Instrument</th>
                      <th className="py-2 pr-4 font-medium">Field</th>
                      <th className="py-2 pr-4 font-medium">Before</th>
                      <th className="py-2 pr-4 font-medium">After</th>
                      <th className="py-2 font-medium">Effective</th>
                    </tr>
                  </thead>
                  <tbody>
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
                        <tr key={c.id} className="border-b border-memo-rule align-top">
                          <td className="py-2 pr-4 text-memo-ink">{c.instrument_key ?? "—"}</td>
                          <td className="py-2 pr-4 text-memo-muted">{String(field).replace(/_/g, " ")}</td>
                          <td className="py-2 pr-4 text-memo-muted">{changeCell(before, String(field))}</td>
                          <td className="py-2 pr-4 font-medium text-memo-ink">{changeCell(after, String(field))}</td>
                          <td className="py-2 text-memo-muted">{formatDate(c.effective_date)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section aria-labelledby="claims-heading" className="mt-12 border-t border-memo-rule pt-8">
            <h3 id="claims-heading" className="text-lg font-semibold text-memo-ink">
              Independent check
            </h3>
            {!claims ? (
              <p className="mt-2 text-sm text-memo-muted">
                No independent check was stored for this alert.
              </p>
            ) : (
              <>
                <p className="mt-2 max-w-[70ch] text-base text-memo-ink">
                  {claimsOk ? "Passed." : `Result: ${claims.status ?? "unknown"}.`} A second model
                  {claims.model ? ` (${claims.model.replace(/^meta-models\//, "")})` : ""} read the alert
                  against the quoted evidence before it was published.
                </p>
                {claims.problems.length > 0 ? (
                  <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-memo-added">
                    {claims.problems.map((p) => (
                      <li key={p}>{p}</li>
                    ))}
                  </ul>
                ) : null}
              </>
            )}
            {alert.run_id ? (
              <p className="mt-4 text-sm">
                <Link href={`/runs/${alert.run_id}`} className="text-memo-info underline underline-offset-2">
                  See every step the agent took
                </Link>
              </p>
            ) : null}
          </section>

          <section aria-labelledby="review-heading" className="mt-12 border-t border-memo-rule pt-8">
            <h3 id="review-heading" className="text-lg font-semibold text-memo-ink">
              Your review
            </h3>
            <p className="mt-2 text-sm text-memo-muted">
              Status: <AlertStatusChip status={displayStatus} />
              {browserOnly ? " · saved in this browser only; the shared demo stays read-only." : null}
            </p>
            <label className="mt-4 block max-w-xl text-sm text-memo-muted">
              Note (optional)
              <textarea
                className="mt-1 block w-full rounded-md border border-memo-rule bg-[#FBFCFA] px-3 py-2 text-sm text-memo-ink"
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
                className="rounded-md bg-memo-ink px-4 py-2 text-sm font-medium text-paper disabled:opacity-40"
              >
                Confirm alert
              </button>
              <button
                type="button"
                disabled={busy || displayStatus === "dismissed"}
                onClick={() => void review("dismiss")}
                className="rounded-md border border-memo-ink px-4 py-2 text-sm text-memo-ink disabled:opacity-40"
              >
                Dismiss alert
              </button>
            </div>
            {reviewError ? <div className="mt-3"><ErrorLine message={reviewError} /></div> : null}
          </section>
        </article>
      ) : null}
    </PageShell>
  );
}
