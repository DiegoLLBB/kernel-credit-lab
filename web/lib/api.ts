export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Readiness = {
  status: "ok" | "degraded";
  checks: Record<string, boolean>;
  providers: Record<string, boolean>;
};

export type Run = {
  id: string;
  question: string;
  workflow: string | null;
  mode: string;
  status: string;
  state: string;
  total_cost_usd: string;
  total_latency_ms: number;
};

export async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, { cache: "no-store" });
  if (!res.ok && res.status !== 503) throw new Error(`${path} -> HTTP ${res.status}`);
  return (await res.json()) as T;
}
