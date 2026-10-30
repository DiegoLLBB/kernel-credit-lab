"use client";

import { useParams, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { ErrorLine, LoadingLine } from "@/components/loading-error";
import { getJson, type DocumentPageOut } from "@/lib/api";
import { formatDate } from "@/lib/format";

export function DocumentClient() {
  const params = useParams<{ filingId: string }>();
  const searchParams = useSearchParams();
  const filingId = params.filingId;
  const factId = searchParams.get("fact");
  const initialPage = Number(searchParams.get("page") ?? "0") || 0;

  const [page, setPage] = useState(initialPage);
  const [doc, setDoc] = useState<DocumentPageOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const highlightRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    const qs = new URLSearchParams();
    qs.set("page", String(page));
    if (factId) qs.set("fact_id", factId);
    getJson<DocumentPageOut>(`/api/documents/${filingId}?${qs.toString()}`)
      .then((d) => {
        if (!cancelled) setDoc(d);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [filingId, page, factId]);

  useEffect(() => {
    if (doc?.highlight && highlightRef.current) {
      highlightRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [doc]);

  function renderText(d: DocumentPageOut) {
    const text = d.text ?? "";
    const h = d.highlight;
    if (!h || h.start < 0 || h.end <= h.start || h.start >= text.length) {
      return <span>{text}</span>;
    }
    const start = Math.min(h.start, text.length);
    const end = Math.min(h.end, text.length);
    return (
      <>
        <span>{text.slice(0, start)}</span>
        <mark
          ref={highlightRef}
          className="rounded bg-amber-400/40 text-amber-50 outline outline-1 outline-amber-300/60"
        >
          {text.slice(start, end)}
        </mark>
        <span>{text.slice(end)}</span>
      </>
    );
  }

  return (
    <div>
      <header className="mb-6">
        <h2 className="text-2xl font-semibold text-white">
          {doc?.title ?? "Document"}
        </h2>
        {doc ? (
          <p className="mt-1 font-mono text-xs text-slate-500">
            {[doc.form_type, formatDate(doc.filed_at), `page ${doc.page + 1} of ${doc.pages}`]
              .filter(Boolean)
              .join(" · ")}
            {doc.url ? (
              <>
                {" · "}
                <a
                  href={doc.url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                >
                  Source
                </a>
              </>
            ) : null}
          </p>
        ) : null}
      </header>

      {error ? <ErrorLine message={error} /> : null}
      {!error && !doc ? <LoadingLine label="Loading document…" /> : null}

      {doc ? (
        <>
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <button
              type="button"
              disabled={page <= 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              className="rounded border border-slate-700 px-3 py-1.5 text-xs enabled:hover:bg-ink-soft disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              Previous page
            </button>
            <span className="font-mono text-xs text-slate-400">
              Page {doc.page + 1} / {doc.pages}
            </span>
            <button
              type="button"
              disabled={page + 1 >= doc.pages}
              onClick={() => setPage((p) => p + 1)}
              className="rounded border border-slate-700 px-3 py-1.5 text-xs enabled:hover:bg-ink-soft disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              Next page
            </button>
            {factId ? (
              <span className="font-mono text-[11px] text-slate-500">
                Highlighting fact {factId.slice(0, 8)}…
              </span>
            ) : null}
          </div>
          <pre className="max-h-[70vh] overflow-auto whitespace-pre-wrap break-words rounded-lg border border-slate-700/60 bg-ink-soft/50 p-4 font-mono text-xs leading-relaxed text-slate-300">
            {renderText(doc)}
          </pre>
        </>
      ) : null}
    </div>
  );
}
