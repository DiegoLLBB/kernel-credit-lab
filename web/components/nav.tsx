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
          aria-label="Kernel, home"
          className="inline-flex items-center focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent"
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={logo.src}
            width={logo.width}
            height={logo.height}
            alt="Kernel Terminal"
            className="h-6 w-auto sm:h-7"
          />
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
