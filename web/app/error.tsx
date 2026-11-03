"use client";

import Link from "next/link";
import { useEffect } from "react";

/** Last-resort page when a whole route fails to render. */
export default function RouteError({ error, reset }: { error: Error; reset: () => void }) {
  useEffect(() => {
    console.error("[Kernel] page failed to render", error);
  }, [error]);
  return (
    <main className="mx-auto max-w-3xl px-4 py-16 sm:px-6">
      <h1 className="text-2xl font-semibold text-memo-ink">This page couldn&apos;t be shown</h1>
      <p className="mt-3 text-memo-ink">
        Something in the data for this page didn&apos;t load as expected. Try again, or go back to the
        overview.
      </p>
      <div className="mt-6 flex gap-3 text-sm">
        <button
          type="button"
          onClick={reset}
          className="rounded-md bg-memo-ink px-3 py-1.5 text-paper"
        >
          Try again
        </button>
        <Link href="/" className="rounded-md border border-memo-ink px-3 py-1.5 text-memo-ink">
          Back to the overview
        </Link>
      </div>
    </main>
  );
}
