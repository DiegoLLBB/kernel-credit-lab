export function LoadingLine({ label = "Loading…" }: { label?: string }) {
  return <p className="text-sm text-slate-400">{label}</p>;
}

export function ErrorLine({ message }: { message: string }) {
  return (
    <p className="rounded border border-rose-500/30 bg-rose-500/5 px-3 py-2 text-sm text-rose-300" role="alert">
      {message}
    </p>
  );
}

export function EmptyLine({ children }: { children: React.ReactNode }) {
  return <p className="text-sm text-slate-400">{children}</p>;
}

export function PageShell({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
      <h1 className="sr-only">{title}</h1>
      {children}
      <footer className="mt-16 border-t border-slate-800/80 pt-6 text-xs text-slate-500">
        Research support only. Not investment advice.
      </footer>
    </main>
  );
}
