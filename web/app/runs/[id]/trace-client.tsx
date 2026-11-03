"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { ErrorLine, LoadingLine, PageShell } from "@/components/loading-error";
import { JsonCollapse } from "@/components/json-collapse";
import { PassFailChip } from "@/components/status-chip";
import { getJson, type RunTraceOut, type TraceTurnOut } from "@/lib/api";
import { formatCost, formatDurationMs, formatNumber } from "@/lib/format";

const READ_TOOLS = new Set(["read_document", "search_document"]);

type FactRow = {
  claim: string;
  quote: string;
  filingId: string | null;
  factId: string | null;
  page: number | null;
};

type TraceSummary = {
  readCount: number;
  facts: FactRow[];
  refusals: { tool: string | null; reason: string; sequence: number }[];
  changes: { name: string; detail: string; sequence: number }[];
  metricsLine: string | null;
  claims: { verdict: string; model: string | null } | null;
  duration: string;
};

function pageFromLocation(location: unknown): number | null {
  if (typeof location !== "string") return null;
  const m = /page\s*=\s*(\d+)/i.exec(location);
  return m ? Number(m[1]) : null;
}

function buildSummary(trace: RunTraceOut): TraceSummary {
  const turns = trace.turns;
  let readCount = 0;
  const facts: FactRow[] = [];
  const refusals: TraceSummary["refusals"] = [];
  const changes: TraceSummary["changes"] = [];
  let metricsLine: string | null = null;
  let claims: TraceSummary["claims"] = null;

  for (const t of turns) {
    const tool = t.tool || "";
    if (READ_TOOLS.has(tool)) readCount += 1;
    if (t.refusal_reason) {
      refusals.push({ tool: t.tool, reason: t.refusal_reason, sequence: t.sequence });
    }
    const args = (t.arguments || {}) as Record<string, unknown>;
    const result = (t.result || {}) as Record<string, unknown>;
    if (tool === "record_fact" && result.ok && !t.refusal_reason) {
      const claim = String(args.claim || "");
      const quote = String(args.quote || "");
      if (claim || quote) {
        facts.push({
          claim,
          quote,
          filingId: typeof args.filing_id === "string" ? args.filing_id : null,
          factId: typeof result.fact_id === "string" ? result.fact_id : null,
          page: pageFromLocation(result.location),
        });
      }
    }
    if (tool === "propose_change" && result.ok && !t.refusal_reason) {
      const change = (args.change || {}) as Record<string, unknown>;
      const name = String(change.name || change.action || "change");
      const amount = change.amount != null ? String(change.amount) : "";
      const maturity = change.maturity != null ? String(change.maturity) : "";
      const detail = [change.action, amount && `amount ${amount}`, maturity && `maturity ${maturity}`]
        .filter(Boolean)
        .join(" · ");
      changes.push({ name, detail, sequence: t.sequence });
    }
    if (tool === "compute_metrics" && result.ok) {
      const metrics = (result.metrics || {}) as Record<string, unknown>;
      const issuer = (metrics.issuer || {}) as Record<string, unknown>;
      const buckets = (issuer.changed_buckets || {}) as Record<string, { before?: string; after?: string }>;
      const parts = Object.entries(buckets).map(([label, ba]) => {
        return `${label}: ${ba.before ?? "—"} → ${ba.after ?? "—"}`;
      });
      metricsLine = parts.length ? parts.join("; ") : "Metrics recomputed.";
    }
    if (tool === "submit_alert" && result.claims_check != null) {
      const detail = (result.claims_check_detail || {}) as Record<string, unknown>;
      claims = {
        verdict: String(result.claims_check),
        model: typeof detail.model === "string" ? detail.model : null,
      };
    }
  }

  return {
    readCount,
    facts,
    refusals,
    changes,
    metricsLine,
    claims,
    duration: formatDurationMs(trace.total_latency_ms),
  };
}

type StepItem =
  | { kind: "single"; turn: TraceTurnOut }
  | { kind: "group"; turns: TraceTurnOut[]; label: string };

function groupTurns(turns: TraceTurnOut[]): StepItem[] {
  const out: StepItem[] = [];
  let i = 0;
  while (i < turns.length) {
    const t = turns[i];
    if (t.tool && READ_TOOLS.has(t.tool)) {
      const group: TraceTurnOut[] = [t];
      let j = i + 1;
      while (j < turns.length && turns[j].tool && READ_TOOLS.has(turns[j].tool!)) {
        group.push(turns[j]);
        j += 1;
      }
      if (group.length >= 2) {
        out.push({
          kind: "group",
          turns: group,
          label: `${group.length} reads and searches`,
        });
        i = j;
        continue;
      }
    }
    out.push({ kind: "single", turn: t });
    i += 1;
  }
  return out;
}

const TOOL_WORDS: Record<string, string> = {
  get_issuer_debt: "Loaded Kernel's debt records",
  set_checklist: "Set what to check in this filing",
  list_documents: "Listed the filing's documents",
  read_document: "Read document pages",
  search_document: "Searched the document",
  record_fact: "Recorded an exact quote",
  propose_change: "Proposed a change to the records",
  compute_metrics: "Recomputed the maturity wall",
  request_input: "Asked for an analyst",
  read_chart: "Read a chart",
  submit_alert: "Submitted the alert",
};

function TurnBody({ t }: { t: TraceTurnOut }) {
  const refused = Boolean(t.refusal_reason);
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-base font-medium text-memo-ink">
          {t.tool ? (TOOL_WORDS[t.tool] ?? t.tool.replace(/_/g, " ")) : "Thinking, no tool call"}
        </h3>
        {t.tool ? <span className="font-mono text-xs text-memo-muted">{t.tool}</span> : null}
        {t.status ? (
          <span className="tabular-nums text-[11px] text-slate-500">{t.status}</span>
        ) : null}
        {t.cost_label || t.cost_usd != null || t.model ? (
          <span className="tabular-nums text-[11px] text-slate-500">
            {t.cost_label ?? formatCost(t.cost_usd, { model: t.model })}
          </span>
        ) : null}
      </div>
      {t.commentary ? <p className="text-sm text-slate-400">{t.commentary}</p> : null}
      {refused ? (
        <p
          className="rounded border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-sm text-rose-200"
          role="alert"
        >
          Refusal: {t.refusal_reason}
        </p>
      ) : null}
      {t.checks.length > 0 ? (
        <ul className="flex flex-wrap gap-2">
          {t.checks.map((c, i) => (
            <li key={`${c.check_name}-${i}`}>
              <PassFailChip
                passed={c.passed}
                label={`${c.check_name}: ${c.passed ? "pass" : "fail"}`}
              />
              {!c.passed && c.problems != null ? (
                <span className="ml-2 tabular-nums text-[11px] text-rose-300">
                  {typeof c.problems === "string" ? c.problems : JSON.stringify(c.problems)}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
      <div className="grid gap-2 sm:grid-cols-2">
        {t.arguments != null ? <JsonCollapse label="Arguments" value={t.arguments} /> : null}
        {t.result != null ? <JsonCollapse label="Result" value={t.result} /> : null}
      </div>
    </div>
  );
}

export function TraceClient() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [trace, setTrace] = useState<RunTraceOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [onlyInteresting, setOnlyInteresting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    setTrace(null);
    getJson<RunTraceOut>(`/api/runs/${id}/trace`)
      .then((d) => {
        if (!cancelled) setTrace(d);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  const turns = useMemo(() => {
    if (!trace) return [];
    if (!onlyInteresting) return trace.turns;
    return trace.turns.filter(
      (t) => Boolean(t.refusal_reason) || (t.checks?.length ?? 0) > 0,
    );
  }, [trace, onlyInteresting]);

  const summary = useMemo(() => (trace ? buildSummary(trace) : null), [trace]);
  const steps = useMemo(() => groupTurns(turns), [turns]);

  return (
    <PageShell title="Run trace">
      {error ? <ErrorLine message={error} /> : null}
      {!error && !trace ? <LoadingLine label="Loading trace…" /> : null}

      {trace && summary ? (
        <div className="space-y-8">
          <header>
            <p className="tabular-nums text-xs text-slate-500">Run {trace.id}</p>
            <h2 className="mt-1 text-3xl font-semibold text-memo-ink sm:text-[40px] sm:leading-[46px]">How the agent got here</h2>
            <p className="mt-2 max-w-2xl text-sm leading-relaxed text-slate-400">
              Summary of what the agent read and proposed, then the step list. Use it to audit an
              alert — refusals and checks are the analyst signals.
            </p>
            <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
              <div>
                <dt className="text-xs text-slate-500">Status</dt>
                <dd className="tabular-nums text-sm text-slate-200">{trace.status}</dd>
              </div>
              <div>
                <dt className="text-xs text-slate-500">Duration</dt>
                <dd className="tabular-nums text-sm text-slate-200">{summary.duration}</dd>
              </div>
              <div>
                <dt className="text-xs text-slate-500">Turns</dt>
                <dd className="tabular-nums text-sm text-slate-200">{trace.turn_count}</dd>
              </div>
              <div>
                <dt className="text-xs text-slate-500">Tokens</dt>
                <dd className="tabular-nums text-sm text-slate-200">
                  {formatNumber(trace.total_tokens)}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-slate-500">Cost</dt>
                <dd className="tabular-nums text-sm text-slate-200">
                  {trace.total_cost_label ??
                    formatCost(trace.total_cost_usd, { hasUnpriced: true })}
                </dd>
              </div>
            </dl>
            <p className="mt-2 tabular-nums text-xs text-slate-500">
              Prompt version: {trace.prompt_version ?? "—"}
            </p>
          </header>

          <section
            aria-labelledby="trace-summary-heading"
            className="border-t border-memo-rule pt-8"
          >
            <h3
              id="trace-summary-heading"
              className="text-lg font-semibold text-memo-ink"
            >
              Summary
            </h3>
            <ul className="mt-3 space-y-3 text-sm text-slate-200">
              <li>
                <span className="text-slate-400">Documents read: </span>
                {summary.readCount} read{summary.readCount === 1 ? "" : "s"}/search
                {summary.readCount === 1 ? "" : "es"}
              </li>
              <li>
                <span className="text-slate-400">Facts recorded: </span>
                {summary.facts.length === 0 ? (
                  "none"
                ) : (
                  <ul className="mt-1 space-y-2">
                    {summary.facts.map((f, i) => {
                      const href =
                        f.filingId && f.factId
                          ? `/documents/${f.filingId}?fact=${f.factId}${
                              f.page != null ? `&page=${f.page}` : ""
                            }`
                          : null;
                      return (
                        <li key={`fact-${i}`} className="border-l border-slate-700 pl-3">
                          <p>{f.claim || "Fact"}</p>
                          {f.quote ? (
                            <p className="mt-1 leading-7">
                              {href ? (
                                <Link
                                  href={href}
                                  className="text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                                >
                                  <span className="quote-mark text-base">{f.quote.length > 180 ? `${f.quote.slice(0, 177)}…` : f.quote}</span>
                                </Link>
                              ) : (
                                <><span className="quote-mark text-base">{f.quote.length > 180 ? `${f.quote.slice(0, 177)}…` : f.quote}</span></>
                              )}
                            </p>
                          ) : null}
                        </li>
                      );
                    })}
                  </ul>
                )}
              </li>
              <li>
                <span className="text-slate-400">Refusals: </span>
                {summary.refusals.length === 0 ? (
                  "none"
                ) : (
                  <ul className="mt-1 list-disc space-y-1 pl-5 text-rose-200/90">
                    {summary.refusals.map((r) => (
                      <li key={`ref-${r.sequence}`}>
                        <span className="tabular-nums text-xs text-slate-500">
                          #{r.sequence} {r.tool}:{" "}
                        </span>
                        {r.reason}
                      </li>
                    ))}
                  </ul>
                )}
              </li>
              <li>
                <span className="text-slate-400">Changes proposed: </span>
                {summary.changes.length === 0 ? (
                  "none kept"
                ) : (
                  <ul className="mt-1 list-disc space-y-1 pl-5">
                    {summary.changes.map((c) => (
                      <li key={`ch-${c.sequence}`}>
                        {c.name}
                        {c.detail ? (
                          <span className="tabular-nums text-xs text-slate-500"> — {c.detail}</span>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                )}
              </li>
              <li>
                <span className="text-slate-400">Metrics: </span>
                {summary.metricsLine ?? "—"}
              </li>
              <li>
                <span className="text-slate-400">Claims check: </span>
                {summary.claims ? (
                  <>
                    <span className="tabular-nums">{summary.claims.verdict}</span>
                    {summary.claims.model ? (
                      <span className="tabular-nums text-xs text-slate-500">
                        {" "}
                        · {summary.claims.model}
                      </span>
                    ) : null}
                  </>
                ) : (
                  "—"
                )}
              </li>
              <li>
                <span className="text-slate-400">Duration: </span>
                {summary.duration}
              </li>
            </ul>
          </section>

          <label className="inline-flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              className="rounded border-slate-600 bg-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
              checked={onlyInteresting}
              onChange={(e) => setOnlyInteresting(e.target.checked)}
            />
            Only refusals and checks
          </label>

          {steps.length === 0 ? (
            <p className="text-sm text-slate-400">
              {onlyInteresting ? "No refusals or checks in this run." : "No turns recorded."}
            </p>
          ) : (
            <ol className="relative space-y-0 border-l border-slate-700/80 pl-6">
              {steps.map((item) => {
                if (item.kind === "group") {
                  const first = item.turns[0];
                  const last = item.turns[item.turns.length - 1];
                  return (
                    <li key={`g-${first.sequence}`} className="relative pb-8">
                      <span
                        className="absolute -left-[1.625rem] top-1 flex h-5 w-5 items-center justify-center rounded-full border border-slate-600 bg-ink text-[10px] tabular-nums text-slate-400"
                        aria-hidden
                      >
                        {first.sequence}
                      </span>
                      <details className="space-y-2">
                        <summary className="cursor-pointer tabular-nums text-sm text-slate-100">
                          {item.label}
                          <span className="ml-2 tabular-nums text-[11px] text-slate-500">
                            steps {first.sequence}–{last.sequence}
                          </span>
                        </summary>
                        <ol className="mt-3 space-y-6 border-l border-slate-800 pl-4">
                          {item.turns.map((t) => (
                            <li key={t.sequence}>
                              <p className="mb-1 tabular-nums text-[11px] text-slate-500">
                                #{t.sequence}
                              </p>
                              <TurnBody t={t} />
                            </li>
                          ))}
                        </ol>
                      </details>
                    </li>
                  );
                }
                const t = item.turn;
                const refused = Boolean(t.refusal_reason);
                return (
                  <li key={t.sequence} className="relative pb-8">
                    <span
                      className={`absolute -left-[1.625rem] top-1 flex h-5 w-5 items-center justify-center rounded-full border text-[10px] tabular-nums ${
                        refused
                          ? "border-rose-500 bg-rose-500/20 text-rose-200"
                          : "border-slate-600 bg-ink text-slate-400"
                      }`}
                      aria-hidden
                    >
                      {t.sequence}
                    </span>
                    <TurnBody t={t} />
                  </li>
                );
              })}
            </ol>
          )}
        </div>
      ) : null}
    </PageShell>
  );
}
