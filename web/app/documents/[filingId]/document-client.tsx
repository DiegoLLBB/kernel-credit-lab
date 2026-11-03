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

  const [pageLoading, setPageLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    setPageLoading(true);
    const qs = new URLSearchParams();
    qs.set("page", String(page));
    if (factId) qs.set("fact_id", factId);
    getJson<DocumentPageOut>(`/api/documents/${filingId}?${qs.toString()}`)
      .then((d) => {
        if (!cancelled) setDoc(d);
      })
      .catch((e: Error) => {
        if (!cancelled) {
          setError(e.message);
          setDoc(null);
        }
      })
      .finally(() => {
        if (!cancelled) setPageLoading(false);
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
          className="quote-mark bg-transparent"
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
        <p className="text-sm text-memo-muted">
          <button type="button" onClick={() => window.history.back()} className="underline-offset-2 hover:underline">
            Back
          </button>
          <span>, source document</span>
        </p>
        <h2 className="mt-2 break-words text-2xl font-semibold leading-tight text-memo-ink sm:text-[32px] sm:leading-[40px]">
          {(doc?.title ?? "Document").replace(/\.html?$/i, "")}
        </h2>
        {doc ? (
          <p className="mt-2 text-sm text-memo-muted">
            {[doc.form_type, doc.filed_at ? `filed ${formatDate(doc.filed_at)}` : null].filter(Boolean).join(", ")}
            {doc.url ? (
              <>
                {". "}
                <a href={doc.url} target="_blank" rel="noreferrer" className="text-memo-info underline underline-offset-2">
                  Original on SEC EDGAR
                </a>
              </>
            ) : null}
          </p>
        ) : null}
        {factId && doc?.highlight ? (
          <p className="mt-4 max-w-[70ch] border-l-[3px] border-memo-highlight pl-3 text-base text-memo-ink">
            The highlighted passage is the exact quote the agent recorded. Kernel only accepts a fact when
            its quote appears word for word in the filing.
          </p>
        ) : null}
      </header>

      {error ? <ErrorLine message={error} /> : null}
      {!error && !doc ? <LoadingLine label="Loading document…" /> : null}

      {doc ? (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-3 text-sm">
            <button
              type="button"
              disabled={page <= 0 || pageLoading}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              className="rounded-md border border-memo-ink px-3 py-1.5 text-memo-ink enabled:hover:bg-[#EEF0EC] disabled:border-memo-rule disabled:text-memo-muted"
            >
              Previous page
            </button>
            <span className="text-memo-muted" aria-live="polite">
              Page {doc.page + 1} of {doc.pages}
              {pageLoading ? ", loading" : ""}
            </span>
            <button
              type="button"
              disabled={page + 1 >= doc.pages || pageLoading}
              onClick={() => setPage((p) => p + 1)}
              className="rounded-md border border-memo-ink px-3 py-1.5 text-memo-ink enabled:hover:bg-[#EEF0EC] disabled:border-memo-rule disabled:text-memo-muted"
            >
              Next page
            </button>
            {doc.highlight ? (
              <button
                type="button"
                onClick={() => highlightRef.current?.scrollIntoView({ behavior: "smooth", block: "center" })}
                className="text-memo-info underline underline-offset-2"
              >
                Go to the highlighted quote
              </button>
            ) : null}
          </div>
          <pre
            aria-busy={pageLoading}
            className={`max-h-[70vh] overflow-auto whitespace-pre-wrap break-words border-y border-memo-rule bg-[#FBFCFA] px-5 py-4 font-serif text-[15px] leading-7 text-memo-ink ${pageLoading ? "opacity-60" : ""}`}
          >
            {renderText(doc)}
          </pre>
        </>
      ) : null}
    </div>
  );
}
