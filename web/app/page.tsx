import { StatusPanel } from "./status-panel";

export default function Home() {
  return (
    <main className="mx-auto max-w-5xl px-4 py-12 sm:px-6">
      <header className="mb-10">
        <p className="font-mono text-xs uppercase tracking-[0.2em] text-accent">Kernel Credit Lab</p>
        <h1 className="mt-2 text-3xl font-semibold text-white">
          Auditable research for investment-grade credit
        </h1>
        <p className="mt-3 max-w-2xl text-slate-400">
          Plan, compute, verify, critique and revise: every number traced to code, every claim to
          a source.
        </p>
      </header>
      <StatusPanel />
      <footer className="mt-16 text-xs text-slate-500">
        Research support only. Not investment advice.
      </footer>
    </main>
  );
}
