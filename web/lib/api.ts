export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Readiness = {
  status: "ok" | "degraded";
  checks: Record<string, boolean>;
  providers: Record<string, boolean>;
};

export type PageMeta = {
  total: number;
  limit: number;
  offset: number;
};

export type IssuerRef = {
  id: string;
  key: string | null;
  name: string;
};

export type AlertStatus = "new" | "confirmed" | "dismissed";

export type AlertListItem = {
  id: string;
  issuer: IssuerRef;
  event_date: string | null;
  event_id: string | null;
  title: string;
  summary: string | null;
  material: boolean;
  materiality_reasons: string[];
  status: AlertStatus;
  run_id: string | null;
  created_at: string | null;
  impact_sentence: string | null;
  impact_status: string | null;
};

export type AlertListOut = {
  items: AlertListItem[];
  total: number;
  limit: number;
  offset: number;
};

export type CitedFactOut = {
  id: string;
  exact_quote: string;
  claim: string | null;
  location: string | null;
  document_title: string | null;
  document_url: string | null;
  page: number | null;
  filing_id: string | null;
};

export type FindingOut = {
  item_key: string | null;
  statement: string;
  citations: CitedFactOut[];
};

export type AppliedChangeOut = {
  id: string;
  action: string | null;
  instrument_key: string | null;
  field: string | null;
  before: unknown;
  after: unknown;
  status: string;
  effective_date: string | null;
  proposed_diff: Record<string, unknown>;
};

export type WallBucketDelta = {
  label: string;
  before: string;
  after: string;
};

export type ClaimsCheckOut = {
  status: string | null;
  problems: string[];
  summary: Record<string, unknown> | null;
  findings: Record<string, unknown>[];
  provider: string | null;
  model: string | null;
};

/** Free-form maturity wall; bucket values are absolute USD dollars as strings. */
export type WallBucket = {
  label: string;
  period_end: string | null;
  schedule_amount: string | number;
  named_active: unknown[];
  named_active_sum: string | number;
  repaid_since_schedule: unknown[];
  repaid_since_schedule_sum?: string | number;
  new_since_schedule: unknown[];
  new_since_schedule_sum: string | number;
  unattributed: string | number;
  breach: boolean;
  total_outstanding: string | number;
};

export type MaturityWall = {
  issuer_id?: string;
  as_of?: string;
  schedule_id?: string | null;
  schedule_as_of?: string | null;
  basis?: string | null;
  source_url?: string | null;
  buckets: Record<string, WallBucket>;
  fx_rates_used?: Record<string, unknown>;
};

export type AlertDetailOut = {
  id: string;
  issuer: IssuerRef;
  event_id: string | null;
  event_date: string | null;
  title: string;
  summary: string | null;
  material: boolean;
  materiality_reasons: string[];
  status: AlertStatus;
  run_id: string | null;
  findings: FindingOut[];
  applied_changes: AppliedChangeOut[];
  impact_sentence: string | null;
  impact_status: string | null;
  affected_buckets: WallBucketDelta[];
  before_wall: MaturityWall | null;
  after_wall: MaturityWall | null;
  claims_check: ClaimsCheckOut | null;
  created_at: string | null;
  review_writes_enabled?: boolean;
  live?: boolean;
};

export type AlertReviewIn = {
  action: "confirm" | "dismiss";
  note?: string | null;
};

export type AlertReviewOut = {
  id: string;
  status: "confirmed" | "dismissed";
  note: string | null;
};

export type CheckResultOut = {
  check_name: string;
  passed: boolean;
  problems: unknown;
  attempt: number;
};

export type TraceTurnOut = {
  sequence: number;
  commentary: string | null;
  tool: string | null;
  arguments: Record<string, unknown> | null;
  result: Record<string, unknown> | null;
  refusal_reason: string | null;
  checks: CheckResultOut[];
  input_tokens: number | null;
  output_tokens: number | null;
  /** Decimal arrives as string */
  cost_usd: string | null;
  status: string | null;
};

export type RunTraceOut = {
  id: string;
  status: string;
  prompt_version: string | null;
  event_id: string | null;
  turn_count: number;
  /** Decimal arrives as string */
  total_cost_usd: string;
  total_latency_ms: number;
  total_tokens: number | null;
  turns: TraceTurnOut[];
};

export type InstrumentOut = {
  id: string;
  key: string | null;
  name: string;
  instrument_type: string;
  /** Decimal as string */
  coupon: string;
  rate_type: string;
  maturity: string;
  maturity_precision: string;
  /** Absolute USD (or native) dollars as string */
  amount_outstanding: string;
  currency: string;
  status: string;
  issuer_id: string;
  cusip: string | null;
};

export type IssuerListItem = {
  id: string;
  key: string | null;
  name: string;
  ticker: string | null;
  sector: string | null;
  parent_key: string | null;
  subsidiary_count: number;
};

export type IssuerListOut = {
  items: IssuerListItem[];
  total: number;
  limit: number;
  offset: number;
};

export type IssuerEventOut = {
  id: string;
  replay_key: string | null;
  event_type: string;
  event_date: string;
  summary: string | null;
  status: string;
  run_id: string | null;
  run_status: string | null;
  alert_ids: string[];
};

export type IssuerDetailOut = {
  id: string;
  key: string | null;
  name: string;
  ticker: string | null;
  sector: string | null;
  cik: string | null;
  parent: IssuerRef | null;
  subsidiaries: IssuerRef[];
  as_of: string;
  instruments: InstrumentOut[];
  maturity_wall: MaturityWall;
  group_maturity_wall: MaturityWall | null;
  events: IssuerEventOut[];
};

/** Display-only refinancing panel (`GET /api/issuers/{key}/refinancing`). */
export type RefinancingBucket = {
  label: string;
  total_outstanding: string | null;
  named_amount: string | null;
  unattributed_amount: string | null;
  maturing_coupon_pct: string | null;
  maturing_coupon_source: string | null;
  remainder_coupon_pct: string | null;
  remainder_coupon_source: string | null;
  incremental_annual_interest: string | null;
};

export type RefinancingPanel = {
  as_of: string;
  tenor_years: number;
  tenor_choices: number[];
  shock_bps: number;
  shock_bps_min: number;
  shock_bps_max: number;
  derivation_line: string | null;
  spread_pct: string | null;
  spread_bps: number | null;
  treasury_pct: string | null;
  treasury_obs_date: string | null;
  cost_of_debt_pct: string | null;
  cost_of_debt_label: string | null;
  base_total: string | null;
  named_base: string | null;
  estimated_base: string | null;
  shocked_total: string | null;
  delta: string | null;
  coverage_before: string | null;
  coverage_after_base: string | null;
  coverage_after_shock: string | null;
  coverage_missing: string | null;
  missing: string | null;
  buckets: RefinancingBucket[];
  pressure_rank: number | null;
  pressure_score: string | null;
  weights: Record<string, string>;
  footnote: string;
  insight_sentence: string | null;
  insight_status: string | null;
};

export type QuoteHighlight = {
  fact_id: string;
  quote: string;
  start: number;
  end: number;
};

export type DocumentPageOut = {
  filing_id: string;
  title: string | null;
  form_type: string | null;
  url: string;
  filed_at: string | null;
  page: number;
  pages: number;
  page_chars: number;
  start: number;
  end: number;
  text: string;
  highlight: QuoteHighlight | null;
};

export type ReplayEvalSummary = {
  precision: number | null;
  recall: number | null;
  incomplete: boolean | null;
  cost_usd: number | null;
  source_path: string | null;
  evaluated_at: string | null;
};

export type ReplayStatusOut = {
  window_from: string | null;
  window_until: string | null;
  event_counts_by_status: Record<string, number>;
  latest_eval: ReplayEvalSummary | null;
};

export type ChartVerificationCheck = {
  ok?: boolean;
  read?: number;
  category?: string;
  deviation?: number;
  implied?: number;
  allowed?: number;
  series?: string | null;
};

export type ChartFilingCrosscheckValue = {
  category: string;
  chart_value: number | null;
  chart_unit: string | null;
  status: string;
  reason: string | null;
  xbrl_value: number | null;
  xbrl_unit: string | null;
  fiscal_label: string | null;
  period_end: string | null;
  accession: string | null;
  filing_url: string | null;
  metric_id: string | null;
  metric_label: string | null;
};

export type ChartFilingCrosscheck = {
  chart_id: string;
  title: string | null;
  unit: string | null;
  metric_id: string | null;
  metric_label: string | null;
  summary_status: string;
  values: ChartFilingCrosscheckValue[];
};

export type ChartReadingOut = {
  id: string;
  issuer_key: string | null;
  filing_id: string;
  filing_title: string | null;
  page: number;
  title: string | null;
  unit: string | null;
  categories: unknown[];
  values: unknown[];
  verified: boolean;
  verification_detail: {
    reason?: string;
    status?: string;
    checks?: ChartVerificationCheck[];
    llm?: unknown;
    [key: string]: unknown;
  } | null;
  bbox: Record<string, unknown>;
  has_crop: boolean;
  has_overlay: boolean;
  filing_crosscheck?: ChartFilingCrosscheck | null;
};

export type ChartListOut = {
  items: ChartReadingOut[];
  total: number;
};

/* ---------- Analyst layer: XBRL credit metrics + checked insight text ---------- */

export type SparkPoint = {
  period_end: string;
  label: string;
  /** Decimal arrives as string */
  value: string | null;
};

export type MetricTile = {
  metric: string;
  label: string;
  /** Decimal arrives as string */
  value: string | null;
  unit: string;
  fiscal_label: string | null;
  period_end: string | null;
  filed: string | null;
  source_tag: string | null;
  missing_reason: string | null;
  change_pct: number | null;
  prior_value: string | null;
  prior_period_end: string | null;
  flag: "green" | "amber" | "red" | null;
  cash_only: boolean | null;
  wall_disagrees: boolean;
  /** Decimal arrives as string */
  wall_value: string | null;
  sparkline: SparkPoint[];
};

export type ThresholdBand = {
  label: string;
  unit: string;
  bands: Record<string, unknown>;
};

export type OverviewHeader = {
  companies: number;
  filings_read: number;
  material_changes: number;
  last_check: string | null;
};

export type BeforeAfter = {
  field: string | null;
  before: unknown;
  after: unknown;
};

export type FeedItem = {
  alert_id: string;
  issuer: IssuerRef;
  event_date: string | null;
  title: string;
  impact_sentence: string | null;
  impact_pending: boolean;
  before_after: BeforeAfter | null;
  live?: boolean;
};

export type EvalDetailEvent = {
  name: string;
  date: string;
};

export type WatchlistRow = {
  issuer: IssuerRef;
  ticker: string | null;
  metrics: MetricTile[];
};

export type EvalSummary = {
  text: string;
  label?: string | null;
  kind?: string | null;
  evaluated_at: string | null;
  precision: number | null;
  recall: number | null;
  incomplete: boolean;
  caught?: number | null;
  expected_material?: number | null;
  false_alert_count?: number | null;
  unsupported_claims?: number | null;
  accurate_caught?: number | null;
  accurate_total?: number | null;
  keeps_records?: string | null;
  wall_passed?: number | null;
  wall_total?: number | null;
  cost_usd?: number | null;
  missed_events?: EvalDetailEvent[];
  false_events?: EvalDetailEvent[];
};

export type EvalComparisonRow = {
  label: string;
  kind?: string | null;
  caught?: number | null;
  expected_material?: number | null;
  accurate_caught?: number | null;
  accurate_total?: number | null;
  false_alert_count?: number | null;
  unsupported_claims?: number | null;
  keeps_records?: string | null;
  cost_usd?: number | null;
};

export type ChartCrosscheckSummary = {
  agree: number;
  differ: number;
  not_comparable: number;
  line?: string | null;
};

export type EvalComparison = {
  rows: EvalComparisonRow[];
  note?: string | null;
  chart_crosscheck?: ChartCrosscheckSummary | null;
};

export type BriefSentence = {
  sentence: string;
  refs: { href: string; kind?: string; match?: string }[];
};

export type DailyBrief = {
  as_of: string;
  status: string;
  sentences: BriefSentence[];
  refused_count: number;
  provider: string | null;
  model: string | null;
  check_model: string | null;
  cost_usd: number | null;
  model_calls: number;
  attribution: string;
};

export type LiveDocument = {
  event_id: string;
  alert_id: string | null;
  issuer_key: string | null;
  issuer_name: string | null;
  form: string | null;
  event_date: string | null;
  title: string | null;
  outcome: string;
  reason: string | null;
  cost_usd: number | null;
};

export type LiveSection = {
  since: string;
  git_sha: string | null;
  prompt_version: string | null;
  documents_read: number;
  alerted: number;
  logged: number;
  refused: number;
  paused: number;
  failed: number;
  documents: LiveDocument[];
  feed: FeedItem[];
};

export type CreditOverview = {
  header: OverviewHeader;
  brief: DailyBrief | null;
  feed: FeedItem[];
  watchlist: WatchlistRow[];
  evaluation: EvalSummary | null;
  baseline: EvalSummary | null;
  comparison: EvalComparison | null;
  live: LiveSection | null;
  thresholds: ThresholdBand[];
};

export type InsightHow = {
  provider: string | null;
  model: string | null;
  check_prompt: string | null;
  dropped: Record<string, unknown>[];
};

export type SnapshotInsight = {
  headline: string | null;
  sentences: string[];
  status: string;
  how: InsightHow;
};

export type SnapshotWallChange = {
  label: string;
  /** Decimal arrives as string */
  before: string | null;
  after: string | null;
  alert_id: string | null;
  alert_title: string | null;
};

export type SnapshotWallBucket = {
  label: string;
  /** Decimal arrives as string */
  amount: string | null;
  changed: boolean;
};

export type SnapshotWall = {
  note: string;
  buckets: SnapshotWallBucket[];
  changes: SnapshotWallChange[];
};

export type TimelineItem = {
  event_date: string;
  title: string;
  effect: string | null;
  alert_id: string | null;
};

export type IssuerSnapshot = {
  issuer: IssuerRef;
  as_of: string;
  insight: SnapshotInsight | null;
  insight_pending: boolean;
  metrics: MetricTile[];
  wall: SnapshotWall | null;
  timeline: TimelineItem[];
  thresholds: ThresholdBand[];
};

export async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, { cache: "no-store" });
  if (!res.ok && res.status !== 503) throw new Error(`${path} → HTTP ${res.status}`);
  return (await res.json()) as T;
}

export async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = `${path} → HTTP ${res.status}`;
    try {
      const err = (await res.json()) as { detail?: string };
      if (err.detail) detail = String(err.detail);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export function chartImageUrl(chartId: string, overlay = true): string {
  return `${API_URL}/api/charts/${chartId}/image?overlay=${overlay ? "true" : "false"}`;
}
