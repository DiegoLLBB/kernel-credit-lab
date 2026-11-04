"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { ErrorLine, LoadingLine, PageShell } from "@/components/loading-error";
import { JsonCollapse } from "@/components/json-collapse";
import { PassFailChip } from "@/components/status-chip";
import { getJson, type RunTraceOut, type TraceTurnOut } from "@/lib/api";
import { formatCost, formatDurationMs, formatNumber, formatUsdDisplay } from "@/lib/format";

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
      const detail = [
        typeof change.action === "string" ? change.action.replace(/_/g, " ") : null,
        amount && `amount ${money(amount)}`,
        maturity && `matures ${maturity}`,
      ]
        .filter(Boolean)
        .join(", ");
      changes.push({ name, detail, sequence: t.sequence });
    }
    if (tool === "compute_metrics" && result.ok) {
      const metrics = (result.metrics || {}) as Record<string, unknown>;
      const issuer = (metrics.issuer || {}) as Record<string, unknown>;
      const buckets = (issuer.changed_buckets || {}) as Record<string, { before?: string; after?: string }>;
      const parts = Object.entries(buckets).map(([label, ba]) => {
        return `${label.replace(/^FY/, "")}: ${money(ba.before)} → ${money(ba.after)}`;
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

function money(v: unknown): string {
  const n = Number(v);
  return v != null && v !== "" && Number.isFinite(n) && Math.abs(n) >= 1_000_000 ? formatUsdDisplay(n) : String(v ?? "–");
}

/** Titles for refused steps: the action was attempted, not done. */
const TRIED_WORDS: Record<string, string> = {
  record_fact: "Tried to record a quote",
  propose_change: "Tried to change the records",
  submit_alert: "Tried to submit the alert",
  set_checklist: "Tried to set the checklist",
  compute_metrics: "Tried to recompute the maturity wall",
};

const CHECK_WORDS: Record<string, string> = {
  claims: "independent check",
  alert_rules: "alert rules",
  quote: "exact quote",
  evidence: "evidence",
  coverage: "checklist coverage",
};

/** One chip per check: the last result recorded for each name (a re-check replaces the first). */
function finalChecks(checks: TraceTurnOut["checks"]): TraceTurnOut["checks"] {
  const byName = new Map<string, TraceTurnOut["checks"][number]>();
  for (const c of checks) byName.set(c.check_name, c);
  const out = [...byName.values()];
  // If the independent check failed, "alert rules: failed" only repeats it.
  const claimsFailed = out.some((c) => c.check_name === "claims" && !c.passed);
  return claimsFailed ? out.filter((c) => c.check_name !== "alert_rules") : out;
}

/** A short readable reason instead of the checker's raw output. */
function problemText(problems: unknown): string {
  if (problems == null) return "";
  if (typeof problems === "string") return problems.slice(0, 200);
  if (Array.isArray(problems)) return problems.map((p) => String(p)).join("; ").slice(0, 200);
  return "see the details below";
}

function TurnBody({ t }: { t: TraceTurnOut }) {
  const refused = Boolean(t.refusal_reason);
  const meta = [t.status, t.cost_label ?? (t.cost_usd != null || t.model ? formatCost(t.cost_usd, { model: t.model }) : null)]
    .filter(Boolean)
    .join(", ");
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h4 className={`text-base font-medium ${refused ? "text-memo-added" : "text-memo-ink"}`}>
          {t.tool
            ? refused && TRIED_WORDS[t.tool]
              ? TRIED_WORDS[t.tool]
              : (TOOL_WORDS[t.tool] ?? t.tool.replace(/_/g, " "))
            : "Thinking, no tool call"}
        </h4>
        {t.tool ? <span className="font-mono text-xs text-memo-muted">{t.tool}</span> : null}
        {meta ? <span className="text-xs text-memo-muted">{meta}</span> : null}
      </div>
      {t.commentary ? <p className="max-w-[75ch] text-sm text-memo-muted">{t.commentary}</p> : null}
      {refused ? (
        <p className="max-w-[75ch] border-l-[3px] border-memo-added bg-[#F6EAE5] px-3 py-2 text-sm text-memo-ink" role="note">
          <span className="font-semibold text-memo-added">Refused: </span>
          {t.refusal_reason}
        </p>
      ) : null}
      {t.checks.length > 0 ? (
        <ul className="flex flex-wrap items-center gap-2">
          {finalChecks(t.checks).map((c) => (
            <li key={c.check_name} className="text-sm">
              <PassFailChip
                passed={c.passed}
                label={`${CHECK_WORDS[c.check_name] ?? c.check_name.replace(/_/g, " ")}: ${c.passed ? "passed" : "failed"}`}
              />
              {!c.passed && !refused ? (
                <span className="ml-2 text-xs text-memo-added">{problemText(c.problems)}</span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
      <div className="grid gap-2 sm:grid-cols-2">
        {t.arguments != null ? <JsonCollapse label="What the agent sent" value={t.arguments} /> : null}
        {t.result != null ? <JsonCollapse label="What came back" value={t.result} /> : null}
      </div>
    </div>
  );
}

function leadSentence(s: TraceSummary): string {
  const parts = [
    `It made ${s.readCount} ${s.readCount === 1 ? "read or search" : "reads and searches"} of the filing`,
    `recorded ${s.facts.length} exact ${s.facts.length === 1 ? "quote" : "quotes"}`,
  ];
  if (s.refusals.length > 0)
    parts.push(`had ${s.refusals.length} ${s.refusals.length === 1 ? "step" : "steps"} refused by Kernel's checks`);
  if (s.changes.length > 0)
    parts.push(`proposed ${s.changes.length} ${s.changes.length === 1 ? "change" : "changes"} to the records`);
  let out = parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}.` : `${parts[0]}.`;
  if (s.claims) {
    const ok = /^(pass|supported|ok)$/i.test(s.claims.verdict);
    out += ok ? " A second model then checked its claims and passed them." : ` A second model then checked its claims: ${s.claims.verdict}.`;
  }
  return out;
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
    return trace.turns.filter((t) => Boolean(t.refusal_reason) || (t.checks?.length ?? 0) > 0);
  }, [trace, onlyInteresting]);

  const summary = useMemo(() => (trace ? buildSummary(trace) : null), [trace]);
  const steps = useMemo(() => groupTurns(turns), [turns]);

  return (
    <PageShell title="How the agent got here">
      {error ? <ErrorLine message={error} /> : null}
      {!error && !trace ? <LoadingLine label="Loading the agent's record…" /> : null}

      {trace && summary ? (
        <div className="space-y-12">
          <header className="grid gap-x-12 gap-y-6 md:grid-cols-[minmax(0,1fr)_17rem] md:items-start">
            <div>
              <p className="text-sm text-memo-muted">The agent&apos;s record</p>
              <h2 className="mt-2 text-3xl font-semibold leading-tight text-memo-ink sm:text-[42px] sm:leading-[48px]">
                How the agent got here
              </h2>
              <p className="mt-4 max-w-[62ch] text-lg leading-8 text-memo-ink">{leadSentence(summary)}</p>
              <p className="mt-3 max-w-[70ch] text-sm text-memo-muted">
                Every tool call, check and refusal is stored. Kernel&apos;s tools refuse a step that
                breaks a rule, such as a quote that isn&apos;t word for word in the filing, and the
                agent has to correct it before it can go on.
              </p>
            </div>
            <dl className="border-t border-memo-ink text-sm">
              {(
                [
                  ["Result", trace.status.replace(/_/g, " ")],
                  ["Steps", String(trace.turn_count)],
                  ["Time taken", summary.duration],
                  ["Tokens", formatNumber(trace.total_tokens)],
                  ["Cost", trace.total_cost_label ?? formatCost(trace.total_cost_usd, { hasUnpriced: true })],
                  ["Prompt version", trace.prompt_version ?? "–"],
                ] as [string, string][]
              ).map(([k, v]) => (
                <div key={k} className="flex items-baseline justify-between gap-4 border-b border-memo-rule py-1.5">
                  <dt className="text-memo-muted">{k}</dt>
                  <dd className="text-right font-medium text-memo-ink">{v}</dd>
                </div>
              ))}
            </dl>
          </header>

          {summary.refusals.length > 0 ? (
            <section aria-labelledby="refusals-heading" className="border-t-[3px] border-memo-added pt-6">
              <h3 id="refusals-heading" className="text-lg font-semibold text-memo-ink">
                Steps Kernel refused
              </h3>
              <p className="mt-1 max-w-[70ch] text-sm text-memo-muted">
                Each was blocked by a rule in the agent&apos;s tools; nothing from a refused step reached the
                records.
              </p>
              <ol className="mt-3 border-t border-memo-rule">
                {summary.refusals.map((r) => (
                  <li key={`ref-${r.sequence}`} className="grid grid-cols-1 gap-x-6 border-b border-memo-rule py-3 sm:grid-cols-[7.5rem_minmax(0,1fr)]">
                    <span className="text-sm text-memo-muted">
                      Step {r.sequence}
                      {r.tool ? <span className="block font-mono text-xs">{r.tool}</span> : null}
                    </span>
                    <span className="text-base text-memo-ink">{r.reason}</span>
                  </li>
                ))}
              </ol>
            </section>
          ) : null}

          <section aria-labelledby="facts-heading" className="border-t border-memo-rule pt-8">
            <h3 id="facts-heading" className="text-lg font-semibold text-memo-ink">
              Quotes it recorded
            </h3>
            {summary.facts.length === 0 ? (
              <p className="mt-2 text-sm text-memo-muted">None.</p>
            ) : (
              <ol className="mt-3 space-y-6">
                {summary.facts.map((f, i) => {
                  const href =
                    f.filingId && f.factId
                      ? `/documents/${f.filingId}?fact=${f.factId}${f.page != null ? `&page=${f.page}` : ""}`
                      : null;
                  const q = f.quote.length > 240 ? `${f.quote.slice(0, 237)}…` : f.quote;
                  return (
                    <li key={`fact-${i}`} className="max-w-[75ch]">
                      <p className="text-sm font-medium text-memo-ink">{f.claim || "Fact"}</p>
                      {f.quote ? (
                        <blockquote className="mt-1 leading-8">
                          <span className="quote-mark text-base">{q}</span>
                        </blockquote>
                      ) : null}
                      {href ? (
                        <Link href={href} className="mt-1 inline-block text-sm text-memo-info underline underline-offset-2">
                          Open in the document
                        </Link>
                      ) : null}
                    </li>
                  );
                })}
              </ol>
            )}
          </section>

          <section aria-labelledby="outcome-heading" className="border-t border-memo-rule pt-8">
            <h3 id="outcome-heading" className="text-lg font-semibold text-memo-ink">
              What it changed and how it was checked
            </h3>
            <dl className="mt-3 grid gap-x-8 gap-y-5 sm:grid-cols-3">
              <div className="border-t border-memo-rule pt-3">
                <dt className="text-sm text-memo-muted">Changes proposed to the records</dt>
                <dd className="mt-1 text-sm text-memo-ink">
                  {summary.changes.length === 0 ? (
                    "None kept."
                  ) : (
                    <ul className="space-y-1">
                      {summary.changes.map((c) => (
                        <li key={`ch-${c.sequence}`}>
                          {c.name.replace(/_/g, " ")}
                          {c.detail ? <span className="block text-xs text-memo-muted">{c.detail}</span> : null}
                        </li>
                      ))}
                    </ul>
                  )}
                </dd>
              </div>
              <div className="border-t border-memo-rule pt-3">
                <dt className="text-sm text-memo-muted">Debt by year, recomputed in code</dt>
                <dd className="mt-1 text-sm text-memo-ink">{summary.metricsLine ?? "Not recomputed in this run."}</dd>
              </div>
              <div className="border-t border-memo-rule pt-3">
                <dt className="text-sm text-memo-muted">Independent check</dt>
                <dd className="mt-1 text-sm text-memo-ink">
                  {summary.claims ? (
                    <>
                      <PassFailChip
                        passed={/^(pass|supported|ok)$/i.test(summary.claims.verdict)}
                        label={summary.claims.verdict}
                      />
                      {summary.claims.model ? (
                        <span className="mt-1 block text-xs text-memo-muted">
                          {summary.claims.model.replace(/^meta-models\//, "")}
                        </span>
                      ) : null}
                    </>
                  ) : (
                    "No alert was submitted, so nothing to check."
                  )}
                </dd>
              </div>
            </dl>
          </section>

          <section aria-labelledby="steps-heading" className="border-t border-memo-rule pt-8">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <h3 id="steps-heading" className="text-lg font-semibold text-memo-ink">
                Every step, in order
              </h3>
              <div role="group" aria-label="Show" className="flex rounded-md border border-memo-rule text-sm">
                {(
                  [
                    [false, "Every step"],
                    [true, "Only refusals and checks"],
                  ] as const
                ).map(([v, label]) => (
                  <button
                    key={label}
                    type="button"
                    aria-pressed={onlyInteresting === v}
                    onClick={() => setOnlyInteresting(v)}
                    className={`px-3 py-1.5 first:rounded-l-md last:rounded-r-md ${
                      onlyInteresting === v ? "bg-memo-ink text-paper" : "text-memo-ink hover:bg-[#EEF0EC]"
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
            {steps.length === 0 ? (
              <p className="mt-3 text-sm text-memo-muted">
                {onlyInteresting ? "No refusals or checks in this run." : "No steps recorded."}
              </p>
            ) : (
              <ol className="relative mt-5 border-l border-memo-rule pl-7">
                {steps.map((item) => {
                  if (item.kind === "group") {
                    const first = item.turns[0];
                    const last = item.turns[item.turns.length - 1];
                    return (
                      <li key={`g-${first.sequence}`} className="relative pb-8">
                        <span
                          className="absolute -left-[2.45rem] top-0.5 flex h-6 w-6 items-center justify-center rounded-full border border-memo-rule bg-paper text-[11px] text-memo-muted"
                          aria-hidden
                        >
                          {first.sequence}
                        </span>
                        <details>
                          <summary className="cursor-pointer text-base text-memo-ink">
                            {item.label}
                            <span className="ml-2 text-xs text-memo-muted">
                              steps {first.sequence}–{last.sequence}
                            </span>
                          </summary>
                          <ol className="mt-3 space-y-6 border-l border-memo-rule pl-4">
                            {item.turns.map((t) => (
                              <li key={t.sequence}>
                                <p className="mb-1 text-xs text-memo-muted">Step {t.sequence}</p>
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
                        className={`absolute -left-[2.45rem] top-0.5 flex h-6 w-6 items-center justify-center rounded-full border text-[11px] ${
                          refused
                            ? "border-memo-added bg-memo-added text-paper"
                            : "border-memo-rule bg-paper text-memo-muted"
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
          </section>
        </div>
      ) : null}
    </PageShell>
  );
}
