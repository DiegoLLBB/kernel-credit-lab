/** Half-up to ``sig`` significant figures (11.25 → 11.3). Matches backend. */
export function roundSigHalfUp(n: number, sig = 3): number {
  if (!Number.isFinite(n) || n === 0) return 0;
  const abs = Math.abs(n);
  const order = Math.floor(Math.log10(abs));
  const factor = 10 ** (sig - 1 - order);
  const rounded = Math.round(abs * factor + Number.EPSILON) / factor;
  // Math.round is half-up for positive numbers.
  return n < 0 ? -rounded : rounded;
}

function formatSigNumber(n: number, sig = 3): string {
  if (n === 0) return "0";
  const abs = Math.abs(n);
  const order = Math.floor(Math.log10(abs));
  const decimals = Math.max(0, sig - 1 - order);
  if (decimals === 0) return Math.round(n).toLocaleString("en-US");
  return n.toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

/**
 * Absolute currency units → millions for walls/tiles.
 * e.g. "11889000000" → "$11,889M". Prose $bn uses 3 sig figs half-up.
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
  if (currency === "USD" || !currency) {
    // Prefer $bn for ≥ $1bn when callers want prose; walls stay in $M below $100bn.
    if (abs >= 100_000) {
      const bn = roundSigHalfUp(Math.abs(millions) / 1_000, 3);
      return `${signed}$${formatSigNumber(bn)}bn`;
    }
    return `${signed}$${formatThousands(Math.round(abs), 0)}M`;
  }
  return `${signed}${formatThousands(Math.round(abs), 0)}M ${currency}`;
}

/** Prose $bn/$M matching backend format_usd_display (3 sig figs half-up for $bn). */
export function formatUsdDisplay(raw: string | number | null | undefined): string {
  if (raw === null || raw === undefined || raw === "") return "—";
  const n = typeof raw === "number" ? raw : Number(String(raw).replace(/,/g, ""));
  if (!Number.isFinite(n)) return String(raw);
  const abs = Math.abs(n);
  const signed = n < 0 ? "-" : "";
  if (abs >= 1_000_000_000) {
    const bn = roundSigHalfUp(abs / 1_000_000_000, 3);
    return `${signed}$${formatSigNumber(bn)}bn`;
  }
  if (abs >= 1_000_000) {
    const m = abs / 1_000_000;
    if (m >= 100) return `${signed}$${formatThousands(Math.round(m), 0)}M`;
    return `${signed}$${formatSigNumber(roundSigHalfUp(m, 3))}M`;
  }
  if (abs >= 1_000) return `${signed}$${formatThousands(Math.round(abs), 0)}`;
  return `${signed}$${abs.toFixed(2)}`;
}

/** 428536 → "7 min 9 s" */
export function formatDurationMs(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return "—";
  const total = Math.max(0, Math.round(ms / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const parts: string[] = [];
  if (hours) parts.push(`${hours} h`);
  if (minutes || hours) parts.push(`${minutes} min`);
  parts.push(`${seconds} s`);
  return parts.join(" ");
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

const UNPRICED_MODELS = /^(muse-wonder|sam-3)/i;

/** Muse Wonder / SAM 3.1 have no published $/token — never show as $0. */
export function formatCost(
  n: string | number | null | undefined,
  opts?: { model?: string | null; hasUnpriced?: boolean },
): string {
  const model = opts?.model ?? null;
  if (model && UNPRICED_MODELS.test(model)) {
    if (n === null || n === undefined || n === "" || Number(n) === 0) {
      return "cost unknown (price not published)";
    }
  }
  if (n === null || n === undefined || n === "") {
    return opts?.hasUnpriced ? "cost unknown (price not published)" : "—";
  }
  const v = typeof n === "number" ? n : Number(n);
  if (!Number.isFinite(v)) return String(n);
  if (v === 0 && opts?.hasUnpriced) return "cost unknown (price not published)";
  const money = `$${v.toFixed(v < 1 ? 4 : 2)}`;
  return opts?.hasUnpriced ? `${money} (excl. unpriced calls)` : money;
}

export function formatNumber(n: string | number | null | undefined): string {
  if (n === null || n === undefined || n === "") return "—";
  const v = typeof n === "number" ? n : Number(n);
  if (!Number.isFinite(v)) return String(n);
  return v.toLocaleString("en-US");
}

/** Format a before→after pair for feed/alert change lines. Prefer pre-formatted displays. */
export function formatBeforeAfter(
  before: unknown,
  after: unknown,
  field?: string | null,
  displays?: { before?: string | null; after?: string | null },
): string | null {
  if (displays?.before && displays?.after) {
    return `${displays.before} → ${displays.after}`;
  }
  if (before === null || before === undefined || after === null || after === undefined) {
    return null;
  }
  const f = (field || "").toLowerCase();
  if (
    f.includes("status") ||
    f.includes("action") ||
    f.includes("rate_type") ||
    f.includes("currency")
  ) {
    return `${String(before)} → ${String(after)}`;
  }
  const bn = Number(before);
  const an = Number(after);
  if (!Number.isFinite(bn) || !Number.isFinite(an)) {
    return `${String(before)} → ${String(after)}`;
  }
  const ratioField =
    f.includes("coverage") ||
    f.includes("leverage") ||
    f.includes("ratio") ||
    f.includes("coupon");
  if (ratioField || (Math.abs(bn) < 100 && Math.abs(an) < 100 && !f.includes("amount"))) {
    const unit = f.includes("coupon") ? "%" : "×";
    return `${bn.toFixed(2)}${unit} → ${an.toFixed(2)}${unit}`;
  }
  if (
    Math.abs(bn) >= 1000 ||
    Math.abs(an) >= 1000 ||
    f.includes("amount") ||
    f.includes("outstanding") ||
    f.includes("debt") ||
    /^fy\d{4}$/i.test(f) ||
    f.includes("total")
  ) {
    return `${formatUsdMillions(bn)} → ${formatUsdMillions(an)}`;
  }
  return `${String(before)} → ${String(after)}`;
}

/** Live document outcome → short analyst label (one vocabulary everywhere). */
export function liveOutcomeLabel(outcome: string | null | undefined): string {
  switch ((outcome || "").toLowerCase()) {
    case "alerted":
    case "raised alert":
      return "raised alert";
    case "logged":
    case "reviewed, no alert":
      return "reviewed, no alert";
    case "paused":
      return "paused";
    case "refused":
    case "needs_input":
    case "needs input":
    case "needs an analyst":
      return "needs an analyst";
    case "failed":
      return "failed";
    case "pending":
    case "processing":
      return "pending";
    default:
      return outcome || "—";
  }
}

/** Basis points for display: "+150 bp (1.50%)". */
export function formatBps(bps: number): string {
  if (!Number.isFinite(bps)) return "—";
  const pct = (bps / 100).toFixed(2);
  return `+${bps} bp (${pct}%)`;
}

export function wallAmount(v: string | number | null | undefined): number {
  if (v === null || v === undefined || v === "") return 0;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : 0;
}

/**
 * A maturity schedule older than ~18 months before the page date can't stand for today's
 * debt profile (e.g. a schedule last tagged in a 2012 10-K). Callers say so instead of drawing it.
 */
export function isStaleSchedule(scheduleAsOf: string | null | undefined, asOf: string | null | undefined): boolean {
  if (!scheduleAsOf) return false;
  const s = Date.parse(scheduleAsOf);
  const ref = asOf ? Date.parse(asOf) : Date.now();
  if (!Number.isFinite(s) || !Number.isFinite(ref)) return false;
  return ref - s > 548 * 24 * 60 * 60 * 1000;
}
