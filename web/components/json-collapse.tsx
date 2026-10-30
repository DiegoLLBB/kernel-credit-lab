"use client";

import { useId, useState } from "react";

export function JsonCollapse({
  label,
  value,
  defaultOpen = false,
}: {
  label: string;
  value: unknown;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  const panelId = `${id}-panel`;

  return (
    <div className="rounded border border-slate-700/60 bg-ink/40">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left font-mono text-xs text-slate-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((v) => !v)}
      >
        <span>{label}</span>
        <span aria-hidden="true" className="text-slate-500">
          {open ? "−" : "+"}
        </span>
      </button>
      {open ? (
        <pre
          id={panelId}
          className="max-h-72 overflow-auto border-t border-slate-700/60 px-3 py-2 font-mono text-[11px] leading-relaxed text-slate-400"
        >
          {JSON.stringify(value, null, 2)}
        </pre>
      ) : null}
    </div>
  );
}
