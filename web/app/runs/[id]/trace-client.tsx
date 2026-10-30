"use client";

import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { ErrorLine, LoadingLine, PageShell } from "@/components/loading-error";
import { JsonCollapse } from "@/components/json-collapse";
import { PassFailChip } from "@/components/status-chip";
import { getJson, type RunTraceOut } from "@/lib/api";
import { formatCost, formatNumber } from "@/lib/format";

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
      (t) =>
        Boolean(t.refusal_reason) ||
        (t.checks?.length ?? 0) > 0,
    );
  }, [trace, onlyInteresting]);

  return (
    <PageShell title="Run trace">
      {error ? <ErrorLine message={error} /> : null}
      {!error && !trace ? <LoadingLine label="Loading trace…" /> : null}

      {trace ? (
        <div className="space-y-8">
          <header>
            <p className="font-mono text-xs text-slate-500">Run {trace.id}</p>
            <h2 className="mt-1 text-2xl font-semibold text-white">Agent trace</h2>
            <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
              <div>
                <dt className="text-xs text-slate-500">Status</dt>
                <dd className="font-mono text-sm text-slate-200">{trace.status}</dd>
              </div>
              <div>
                <dt className="text-xs text-slate-500">Turns</dt>
                <dd className="font-mono text-sm text-slate-200">{trace.turn_count}</dd>
              </div>
              <div>
                <dt className="text-xs text-slate-500">Tokens</dt>
                <dd className="font-mono text-sm text-slate-200">
                  {formatNumber(trace.total_tokens)}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-slate-500">Cost</dt>
                <dd className="font-mono text-sm text-slate-200">
                  {formatCost(trace.total_cost_usd)}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-slate-500">Latency</dt>
                <dd className="font-mono text-sm text-slate-200">
                  {formatNumber(trace.total_latency_ms)} ms
                </dd>
              </div>
            </dl>
            <p className="mt-2 font-mono text-xs text-slate-500">
              Prompt version: {trace.prompt_version ?? "—"}
            </p>
          </header>

          <label className="inline-flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              className="rounded border-slate-600 bg-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
              checked={onlyInteresting}
              onChange={(e) => setOnlyInteresting(e.target.checked)}
            />
            Only refusals and checks
          </label>

          {turns.length === 0 ? (
            <p className="text-sm text-slate-400">
              {onlyInteresting
                ? "No refusals or checks in this run."
                : "No turns recorded."}
            </p>
          ) : (
            <ol className="relative space-y-0 border-l border-slate-700/80 pl-6">
              {turns.map((t) => {
                const refused = Boolean(t.refusal_reason);
                return (
                  <li key={t.sequence} className="relative pb-8">
                    <span
                      className={`absolute -left-[1.625rem] top-1 flex h-5 w-5 items-center justify-center rounded-full border text-[10px] font-mono ${
                        refused
                          ? "border-rose-500 bg-rose-500/20 text-rose-200"
                          : "border-slate-600 bg-ink text-slate-400"
                      }`}
                      aria-hidden
                    >
                      {t.sequence}
                    </span>
                    <div className="space-y-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="font-mono text-sm text-slate-100">
                          {t.tool ?? "(no tool)"}
                        </h3>
                        {t.status ? (
                          <span className="font-mono text-[11px] text-slate-500">{t.status}</span>
                        ) : null}
                        {t.cost_usd ? (
                          <span className="font-mono text-[11px] text-slate-500">
                            {formatCost(t.cost_usd)}
                          </span>
                        ) : null}
                      </div>
                      {t.commentary ? (
                        <p className="text-sm text-slate-400">{t.commentary}</p>
                      ) : null}
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
                                <span className="ml-2 font-mono text-[11px] text-rose-300">
                                  {typeof c.problems === "string"
                                    ? c.problems
                                    : JSON.stringify(c.problems)}
                                </span>
                              ) : null}
                            </li>
                          ))}
                        </ul>
                      ) : null}
                      <div className="grid gap-2 sm:grid-cols-2">
                        {t.arguments != null ? (
                          <JsonCollapse label="Arguments" value={t.arguments} />
                        ) : null}
                        {t.result != null ? (
                          <JsonCollapse label="Result" value={t.result} />
                        ) : null}
                      </div>
                    </div>
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
