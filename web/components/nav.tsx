"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import logo from "./kernel-logo.png";

const LINKS = [
  { href: "/", label: "Overview" },
  { href: "/alerts", label: "Alerts" },
  { href: "/issuers", label: "Issuers" },
] as const;

export function Nav() {
  const pathname = usePathname();

  return (
    <header className="no-print border-b border-memo-rule bg-paper">
      <div className="flex w-full flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6 lg:px-10">
        <Link
          href="/"
          aria-label="Kernel Terminal Credit, home"
          className="inline-flex items-baseline gap-3 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent sm:gap-4"
        >
          {/* Inside a line box an image's baseline is its bottom margin edge, so the negative
              margin lifts the baseline to that of "TERMINAL" in the logo (22 of its 104 px sit
              below it). "CREDIT" then aligns by baseline on any font renderer and zoom. */}
          <span className="inline-block leading-none">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={logo.src}
              width={logo.width}
              height={logo.height}
              alt="Kernel Terminal"
              className="-mb-[5.08px] inline-block h-6 w-auto align-baseline sm:-mb-[5.92px] sm:h-7"
            />
          </span>
          <span aria-hidden className="h-5 w-px self-center bg-memo-muted/50 sm:h-6" />
          <span
            className="text-[12px] font-medium uppercase leading-none tracking-[0.2em] text-memo-ink sm:text-[15px]"
            style={{ fontFamily: '"JetBrains Mono", ui-monospace, monospace' }}
          >
            Credit
          </span>
        </Link>
        <nav aria-label="Primary" className="flex flex-wrap gap-1">
          {LINKS.map(({ href, label }) => {
            const active =
              href === "/"
                ? pathname === "/"
                : pathname === href || pathname.startsWith(`${href}/`);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={`rounded px-3 py-1.5 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${
                  active
                    ? "bg-ink-soft text-white"
                    : "text-slate-400 hover:bg-ink-soft/60 hover:text-slate-200"
                }`}
              >
                {label}
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
}
