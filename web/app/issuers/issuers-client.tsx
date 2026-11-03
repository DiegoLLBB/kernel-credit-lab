"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { EmptyLine, ErrorLine, LoadingLine, PageShell } from "@/components/loading-error";
import {
  getJson,
  postJson,
  readAdminToken,
  writeAdminToken,
  type IssuerListOut,
  type OnboardIssuerOut,
} from "@/lib/api";

export function IssuersClient() {
  const [data, setData] = useState<IssuerListOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adminToken, setAdminToken] = useState("");
  const [tokenDraft, setTokenDraft] = useState("");
  const [ticker, setTicker] = useState("");
  const [busy, setBusy] = useState(false);
  const [onboardMsg, setOnboardMsg] = useState<string | null>(null);
  const [onboardErr, setOnboardErr] = useState<string | null>(null);

  useEffect(() => {
    setAdminToken(readAdminToken());
  }, []);

  const reload = () => {
    setError(null);
    return getJson<IssuerListOut>("/api/issuers?limit=50")
      .then((d) => setData(d))
      .catch((e: Error) => setError(e.message));
  };

  useEffect(() => {
    let cancelled = false;
    getJson<IssuerListOut>("/api/issuers?limit=50")
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const saveToken = (e: FormEvent) => {
    e.preventDefault();
    writeAdminToken(tokenDraft);
    setAdminToken(tokenDraft.trim());
    setTokenDraft("");
  };

  const clearToken = () => {
    writeAdminToken("");
    setAdminToken("");
  };

  const addCompany = async (e: FormEvent) => {
    e.preventDefault();
    if (!adminToken || !ticker.trim()) return;
    setBusy(true);
    setOnboardErr(null);
    setOnboardMsg(null);
    try {
      const out = await postJson<OnboardIssuerOut>(
        "/api/issuers",
        { ticker: ticker.trim() },
        { adminToken },
      );
      setOnboardMsg(out.message);
      setTicker("");
      await reload();
    } catch (err) {
      setOnboardErr(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onboarded = data?.onboarded_by_ticker ?? [];

  return (
    <PageShell title="Issuers">
      <header className="mb-8">
        <h2 className="text-3xl font-semibold text-memo-ink sm:text-[40px] sm:leading-[46px]">Companies</h2>
        <p className="mt-3 max-w-[62ch] text-lg text-memo-ink">
          {data
            ? `${data.items.filter((x) => !x.parent_key).length} investment-grade companies Kernel watches, grouped by sector. Duke Energy's operating subsidiaries are tracked separately under it.`
            : "Companies Kernel watches."}
        </p>
      </header>



      {error ? <ErrorLine message={error} /> : null}
      {!error && !data ? <LoadingLine /> : null}
      {data && data.items.length === 0 ? (
        <EmptyLine>No issuers loaded yet — seed the watchlist to populate this page.</EmptyLine>
      ) : null}

      {data && data.items.length > 0 ? (
        <div className="space-y-10">
          {Object.entries(
            data.items
              .filter((x) => !x.parent_key)
              .reduce<Record<string, typeof data.items>>((acc, iss) => {
                const k = iss.sector ?? "Other";
                (acc[k] ??= []).push(iss);
                return acc;
              }, {}),
          )
            .sort(([x], [y]) => x.localeCompare(y))
            .map(([sector, list]) => (
              <section key={sector} aria-label={sector}>
                <h3 className="text-sm font-medium text-memo-muted">{sector}</h3>
                <ul className="mt-2 border-t border-memo-ink">
                  {list.map((iss) => {
                    const subs = data.items.filter((x) => x.parent_key && x.parent_key === iss.key);
                    return (
                      <li key={iss.id} className="border-b border-memo-rule">
                        <Link
                          href={`/issuers/${encodeURIComponent(iss.key ?? iss.id)}`}
                          className="flex items-baseline justify-between gap-4 py-3 hover:bg-[#EEF0EC]"
                        >
                          <span className="text-base font-medium text-memo-ink">{iss.name}</span>
                          <span className="text-sm text-memo-muted">{iss.ticker ?? ""}</span>
                        </Link>
                        {subs.length > 0 ? (
                          <ul className="mb-3 ml-5 border-l border-memo-rule pl-4">
                            {subs.map((sub) => (
                              <li key={sub.id}>
                                <Link
                                  href={`/issuers/${encodeURIComponent(sub.key ?? sub.id)}`}
                                  className="block py-1.5 text-sm text-memo-ink hover:underline"
                                >
                                  {sub.name}
                                </Link>
                              </li>
                            ))}
                          </ul>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
              </section>
            ))}
        </div>
      ) : null}
      <details className="mt-12 border-t border-memo-rule pt-6 text-sm">
        <summary className="cursor-pointer text-memo-muted">Operator: add a company by ticker</summary>
        <div className="mt-4">
        
        {adminToken ? (
          <>
            <p className="mt-1 text-xs text-slate-500">
              Operator mode — admin token is stored only in this browser.
              {" "}
              <button
                type="button"
                onClick={clearToken}
                className="text-accent underline-offset-2 hover:underline"
              >
                Clear token
              </button>
            </p>
            <form onSubmit={addCompany} className="mt-3 flex flex-wrap items-end gap-2">
              <label className="flex flex-col gap-1 text-xs text-slate-400">
                Ticker
                <input
                  value={ticker}
                  onChange={(e) => setTicker(e.target.value.toUpperCase())}
                  className="rounded-md border border-memo-rule bg-[#FBFCFA] px-3 py-2 tabular-nums text-sm text-slate-100"
                  placeholder="MSFT"
                  maxLength={16}
                  disabled={busy}
                />
              </label>
              <button
                type="submit"
                disabled={busy || !ticker.trim()}
                className="rounded-md bg-memo-ink px-3 py-2 text-sm font-medium text-paper disabled:opacity-50"
              >
                {busy ? "Onboarding…" : "Add by ticker"}
              </button>
            </form>
            {onboardMsg ? <p className="mt-2 text-xs text-emerald-400">{onboardMsg}</p> : null}
            {onboardErr ? <p className="mt-2 text-xs text-rose-400">{onboardErr}</p> : null}
          </>
        ) : (
          <>
            <p className="mt-1 text-sm text-slate-400">
              Companies are added by the operator.
            </p>
            <form onSubmit={saveToken} className="mt-3 flex flex-wrap items-end gap-2">
              <label className="flex flex-col gap-1 text-xs text-slate-500">
                Operator token (optional)
                <input
                  type="password"
                  value={tokenDraft}
                  onChange={(e) => setTokenDraft(e.target.value)}
                  className="rounded-md border border-memo-rule bg-[#FBFCFA] px-3 py-2 tabular-nums text-sm text-slate-100"
                  placeholder="Paste admin token"
                  autoComplete="off"
                />
              </label>
              <button
                type="submit"
                disabled={!tokenDraft.trim()}
                className="rounded border border-slate-600 px-3 py-2 text-sm text-slate-300 disabled:opacity-50"
              >
                Save locally
              </button>
            </form>
          </>
        )}

        {onboarded.length > 0 ? (
          <div className="mt-4 border-t border-slate-800 pt-3">
            <p className="text-xs font-medium text-slate-500">
              Added by ticker
            </p>
            <ul className="mt-2 space-y-1 text-sm text-slate-300">
              {onboarded.map((iss) => (
                <li key={iss.id} className="flex flex-wrap gap-x-3 gap-y-0.5">
                  <Link
                    href={`/issuers/${encodeURIComponent(iss.key ?? iss.id)}`}
                    className="text-accent hover:underline"
                  >
                    {iss.ticker || iss.key}
                  </Link>
                  <span className="text-slate-500">{iss.name}</span>
                  <span className="tabular-nums text-[11px] text-slate-600">
                    {iss.onboarded_at
                      ? `onboarded ${iss.onboarded_at.slice(0, 10)}`
                      : "onboarded"}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <p className="mt-3 text-xs text-slate-600">No ticker-onboarded companies yet.</p>
        )}
        </div>
      </details>
    </PageShell>
  );
}
