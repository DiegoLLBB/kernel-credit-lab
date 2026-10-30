/**
 * Absolute currency units → millions. Wall totals are USD dollars as strings
 * (e.g. "2752000000" → "$2,752M"). Optional billions above $10,000M for USD.
 */
export function formatUsdMillions(
  raw: string | number | null | undefined,
  currency = "USD",
): string {
  if (raw === null || raw === undefined || raw === "") return "—";
  const n = typeof raw === "number" ? raw : Number(String(raw).replace(/,/g, ""));
  if (!Number.isFinite(n)) return String(raw);
  const millions = n / 1_000_000;
  const abs = Math.abs(millions);
  const signed = millions < 0 ? "-" : "";
  // Prefer $M for credit walls (e.g. $13,032M). Billions only for very large totals.
  if (currency === "USD" || !currency) {
    if (abs >= 100_000) {
      return `${signed}$${formatThousands(Math.abs(millions) / 1_000, 1)}B`;
    }
    return `${signed}$${formatThousands(Math.round(abs), 0)}M`;
  }
  return `${signed}${formatThousands(Math.round(abs), 0)}M ${currency}`;
}

export function formatUsdMillionsDelta(before: string | number, after: string | number): string {
  const b = Number(before);
  const a = Number(after);
  if (!Number.isFinite(b) || !Number.isFinite(a)) return "";
  const delta = a - b;
  const sign = delta > 0 ? "+" : delta < 0 ? "−" : "";
  const absLabel = formatUsdMillions(Math.abs(delta)).replace(/^-/, "");
  return `${sign}${absLabel}`;
}

function formatThousands(n: number, fractionDigits: number): string {
  return n.toLocaleString("en-US", {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  });
}

/** "2026-09-21" → "21 Sep 2026" */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = iso.slice(0, 10);
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(d);
  if (!m) return iso;
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const month = months[Number(m[2]) - 1] ?? m[2];
  return `${Number(m[3])} ${month} ${m[1]}`;
}

export function formatPct(n: number | null | undefined, digits = 1): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "—";
  return `${(n * 100).toFixed(digits)}%`;
}

export function formatCost(n: string | number | null | undefined): string {
  if (n === null || n === undefined || n === "") return "—";
  const v = typeof n === "number" ? n : Number(n);
  if (!Number.isFinite(v)) return String(n);
  return `$${v.toFixed(v < 1 ? 4 : 2)}`;
}

export function formatNumber(n: string | number | null | undefined): string {
  if (n === null || n === undefined || n === "") return "—";
  const v = typeof n === "number" ? n : Number(n);
  if (!Number.isFinite(v)) return String(n);
  return v.toLocaleString("en-US");
}

export function wallAmount(v: string | number | null | undefined): number {
  if (v === null || v === undefined || v === "") return 0;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : 0;
}
