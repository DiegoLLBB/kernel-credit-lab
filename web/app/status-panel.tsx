"use client";

import { useEffect, useState } from "react";
import { getJson, type Readiness, type Run } from "@/lib/api";

function Dot({ ok }: { ok: boolean }) {
  return (
    <span
      className={`inline-block h-2 w-2 rounded-full ${ok ? "bg-emerald-400" : "bg-rose-500"}`}
      aria-label={ok ? "ok" : "unavailable"}
    />
  );
}

export function StatusPanel() {
  const [ready, setReady] = useState<Readiness | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([getJson<Readiness>("/health/ready"), getJson<Run[]>("/api/runs")])
      .then(([r, rs]) => {
        setReady(r);
        setRuns(rs);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  if (error) {
    return <p className="text-sm text-rose-400">API unreachable: {error}</p>;
  }
  if (!ready) {
    return <p className="text-sm text-slate-400">Checking services…</p>;
  }

  const rows = [
    ...Object.entries(ready.checks).map(([k, v]) => [k, v] as const),
    ...Object.entries(ready.providers).map(([k, v]) => [`model provider: ${k}`, v] as const),
  ];

  return (
    <div className="grid gap-6 md:grid-cols-2">
      <section className="rounded-lg border border-slate-700/60 bg-ink-soft p-5">
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">System</h2>
        <ul className="space-y-2 font-mono text-sm">
          {rows.map(([name, ok]) => (
            <li key={name} className="flex items-center gap-2">
              <Dot ok={ok} /> {name}
            </li>
          ))}
        </ul>
      </section>
      <section className="rounded-lg border border-slate-700/60 bg-ink-soft p-5">
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">
          Research runs
        </h2>
        {runs.length === 0 ? (
          <p className="text-sm text-slate-400">No runs yet.</p>
        ) : (
          <ul className="space-y-2 text-sm">
            {runs.map((r) => (
              <li key={r.id} className="flex justify-between gap-4">
                <span className="truncate">{r.question}</span>
                <span className="font-mono text-slate-400">{r.status}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
