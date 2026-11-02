"""Pydantic response models for the dashboard read API."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PageMeta(BaseModel):
    total: int
    limit: int
    offset: int


class IssuerRef(BaseModel):
    id: str
    key: str | None = None
    name: str


class AlertListItem(BaseModel):
    id: str
    issuer: IssuerRef
    event_date: date | None = None
    event_id: str | None = None
    title: str
    summary: str | None = None
    material: bool = True
    materiality_reasons: list[str] = Field(default_factory=list)
    status: Literal["new", "confirmed", "dismissed"]
    run_id: str | None = None
    created_at: datetime | None = None
    impact_sentence: str | None = None
    impact_status: str | None = None


class AlertListOut(BaseModel):
    items: list[AlertListItem]
    total: int
    limit: int
    offset: int


class CitedFactOut(BaseModel):
    id: str
    exact_quote: str
    claim: str | None = None
    location: str | None = None
    document_title: str | None = None
    document_url: str | None = None
    page: int | None = None
    filing_id: str | None = None


class FindingOut(BaseModel):
    item_key: str | None = None
    statement: str
    citations: list[CitedFactOut] = Field(default_factory=list)


class AppliedChangeOut(BaseModel):
    id: str
    action: str | None = None
    instrument_key: str | None = None
    field: str | None = None
    before: Any = None
    after: Any = None
    status: str
    effective_date: date | None = None
    proposed_diff: dict[str, Any] = Field(default_factory=dict)


class WallBucketDelta(BaseModel):
    label: str
    before: str
    after: str


class ClaimsCheckOut(BaseModel):
    status: str | None = None
    problems: list[str] = Field(default_factory=list)
    summary: dict[str, Any] | None = None
    findings: list[dict[str, Any]] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None


class AlertDetailOut(BaseModel):
    id: str
    issuer: IssuerRef
    event_id: str | None = None
    event_date: date | None = None
    title: str
    summary: str | None = None
    material: bool = True
    materiality_reasons: list[str] = Field(default_factory=list)
    status: Literal["new", "confirmed", "dismissed"]
    run_id: str | None = None
    findings: list[FindingOut] = Field(default_factory=list)
    applied_changes: list[AppliedChangeOut] = Field(default_factory=list)
    impact_sentence: str | None = None
    impact_status: str | None = None
    affected_buckets: list[WallBucketDelta] = Field(default_factory=list)
    before_wall: dict[str, Any] | None = None
    after_wall: dict[str, Any] | None = None
    claims_check: ClaimsCheckOut | None = None
    created_at: datetime | None = None
    review_writes_enabled: bool = True
    live: bool = False


class AlertReviewIn(BaseModel):
    action: Literal["confirm", "dismiss"]
    note: str | None = None


class AlertReviewOut(BaseModel):
    id: str
    status: Literal["confirmed", "dismissed"]
    note: str | None = None


class CheckResultOut(BaseModel):
    check_name: str
    passed: bool
    problems: Any = None
    attempt: int = 1


class TraceTurnOut(BaseModel):
    sequence: int
    commentary: str | None = None
    tool: str | None = None
    arguments: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    refusal_reason: str | None = None
    checks: list[CheckResultOut] = Field(default_factory=list)
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: Decimal | None = None
    status: str | None = None


class RunTraceOut(BaseModel):
    id: str
    status: str
    prompt_version: str | None = None
    event_id: str | None = None
    turn_count: int = 0
    total_cost_usd: Decimal = Decimal("0")
    total_latency_ms: int = 0
    total_tokens: int | None = None
    turns: list[TraceTurnOut] = Field(default_factory=list)


class InstrumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    key: str | None = None
    name: str
    instrument_type: str
    coupon: Decimal
    rate_type: str
    maturity: date
    maturity_precision: str
    amount_outstanding: Decimal
    currency: str
    status: str
    issuer_id: str
    cusip: str | None = None


class IssuerListItem(BaseModel):
    id: str
    key: str | None = None
    name: str
    ticker: str | None = None
    sector: str | None = None
    parent_key: str | None = None
    subsidiary_count: int = 0


class IssuerListOut(BaseModel):
    items: list[IssuerListItem]
    total: int
    limit: int
    offset: int


class IssuerEventOut(BaseModel):
    id: str
    replay_key: str | None = None
    event_type: str
    event_date: date
    summary: str | None = None
    status: str
    run_id: str | None = None
    run_status: str | None = None
    alert_ids: list[str] = Field(default_factory=list)


class IssuerDetailOut(BaseModel):
    id: str
    key: str | None = None
    name: str
    ticker: str | None = None
    sector: str | None = None
    cik: str | None = None
    parent: IssuerRef | None = None
    subsidiaries: list[IssuerRef] = Field(default_factory=list)
    as_of: date
    instruments: list[InstrumentOut] = Field(default_factory=list)
    maturity_wall: dict[str, Any] = Field(default_factory=dict)
    group_maturity_wall: dict[str, Any] | None = None
    events: list[IssuerEventOut] = Field(default_factory=list)


class RefinancingBucketOut(BaseModel):
    label: str
    total_outstanding: Decimal | None = None
    named_amount: Decimal | None = None
    unattributed_amount: Decimal | None = None
    maturing_coupon_pct: Decimal | None = None
    maturing_coupon_source: str | None = None
    remainder_coupon_pct: Decimal | None = None
    remainder_coupon_source: str | None = None
    incremental_annual_interest: Decimal | None = None


class RefinancingPanelOut(BaseModel):
    """Display-only rate-reset / shock panel (not used by the agent or alerts)."""

    as_of: date
    tenor_years: int = 5
    tenor_choices: list[int] = Field(default_factory=lambda: [2, 5, 7, 10])
    shock_bps: int = 0
    shock_bps_min: int = 0
    shock_bps_max: int = 300
    derivation_line: str | None = None
    spread_pct: Decimal | None = None
    spread_bps: int | None = None
    treasury_pct: Decimal | None = None
    treasury_obs_date: date | None = None
    cost_of_debt_pct: Decimal | None = None
    cost_of_debt_label: str | None = None
    base_total: Decimal | None = None
    named_base: Decimal | None = None
    estimated_base: Decimal | None = None
    shocked_total: Decimal | None = None
    delta: Decimal | None = None
    coverage_before: Decimal | None = None
    coverage_after_base: Decimal | None = None
    coverage_after_shock: Decimal | None = None
    coverage_missing: str | None = None
    missing: str | None = None
    buckets: list[RefinancingBucketOut] = Field(default_factory=list)
    pressure_rank: int | None = None
    pressure_score: Decimal | None = None
    weights: dict[str, str] = Field(default_factory=dict)
    footnote: str
    insight_sentence: str | None = None
    insight_status: str | None = None


class QuoteHighlight(BaseModel):
    fact_id: str
    quote: str
    start: int
    end: int


class DocumentPageOut(BaseModel):
    filing_id: str
    title: str | None = None
    form_type: str | None = None
    url: str
    filed_at: date | None = None
    page: int
    pages: int
    page_chars: int
    start: int
    end: int
    text: str
    highlight: QuoteHighlight | None = None


class EvalDetailEventOut(BaseModel):
    name: str
    date: str


class ReplayEvalSummary(BaseModel):
    precision: float | None = None
    recall: float | None = None
    incomplete: bool | None = None
    cost_usd: float | None = None
    source_path: str | None = None
    evaluated_at: str | None = None
    text: str | None = None
    caught: int | None = None
    expected_material: int | None = None
    false_alert_count: int | None = None
    missed_events: list[EvalDetailEventOut] = Field(default_factory=list)
    false_events: list[EvalDetailEventOut] = Field(default_factory=list)


class ReplayStatusOut(BaseModel):
    window_from: date | None = None
    window_until: date | None = None
    event_counts_by_status: dict[str, int] = Field(default_factory=dict)
    latest_eval: ReplayEvalSummary | None = None


class ChartFilingCrosscheckValueOut(BaseModel):
    category: str
    chart_value: float | None = None
    chart_unit: str | None = None
    status: str
    reason: str | None = None
    xbrl_value: float | None = None
    xbrl_unit: str | None = None
    fiscal_label: str | None = None
    period_end: str | None = None
    accession: str | None = None
    filing_url: str | None = None
    metric_id: str | None = None
    metric_label: str | None = None


class ChartFilingCrosscheckOut(BaseModel):
    chart_id: str
    title: str | None = None
    unit: str | None = None
    metric_id: str | None = None
    metric_label: str | None = None
    summary_status: str
    values: list[ChartFilingCrosscheckValueOut] = Field(default_factory=list)


class ChartReadingOut(BaseModel):
    id: str
    issuer_key: str | None = None
    filing_id: str
    filing_title: str | None = None
    page: int
    title: str | None = None
    unit: str | None = None
    categories: list[Any] = Field(default_factory=list)
    values: list[Any] = Field(default_factory=list)
    verified: bool
    verification_detail: dict[str, Any] | None = None
    bbox: dict[str, Any] = Field(default_factory=dict)
    has_crop: bool = False
    has_overlay: bool = False
    filing_crosscheck: ChartFilingCrosscheckOut | None = None


class ChartListOut(BaseModel):
    items: list[ChartReadingOut]
    total: int


class ChartCrosscheckSummaryOut(BaseModel):
    agree: int = 0
    differ: int = 0
    not_comparable: int = 0
    line: str | None = None


# --- Analyst layer: credit snapshot + "What changed" overview ---


class SparkPoint(BaseModel):
    period_end: date
    label: str
    value: Decimal | None = None


class MetricTileOut(BaseModel):
    metric: str
    label: str
    value: Decimal | None = None
    unit: str
    fiscal_label: str | None = None
    period_end: date | None = None
    filed: date | None = None
    source_tag: str | None = None
    missing_reason: str | None = None
    change_pct: float | None = None
    prior_value: Decimal | None = None
    prior_period_end: date | None = None
    flag: Literal["green", "amber", "red"] | None = None
    cash_only: bool | None = None
    wall_disagrees: bool = False
    wall_value: Decimal | None = None
    sparkline: list[SparkPoint] = Field(default_factory=list)


class InsightHowOut(BaseModel):
    provider: str | None = None
    model: str | None = None
    check_prompt: str | None = None
    dropped: list[dict[str, Any]] = Field(default_factory=list)


class SnapshotInsightOut(BaseModel):
    headline: str | None = None
    sentences: list[str] = Field(default_factory=list)
    status: str
    how: InsightHowOut = Field(default_factory=InsightHowOut)


class WallChangeOut(BaseModel):
    label: str
    before: Decimal | None = None
    after: Decimal | None = None
    alert_id: str | None = None
    alert_title: str | None = None


class WallBucketOut(BaseModel):
    label: str
    amount: Decimal | None = None
    changed: bool = False


class SnapshotWallOut(BaseModel):
    note: str
    buckets: list[WallBucketOut] = Field(default_factory=list)
    changes: list[WallChangeOut] = Field(default_factory=list)


class TimelineItemOut(BaseModel):
    event_date: date
    title: str
    effect: str | None = None
    alert_id: str | None = None


class ThresholdBandOut(BaseModel):
    label: str
    unit: str
    bands: dict[str, Any] = Field(default_factory=dict)


class IssuerSnapshotOut(BaseModel):
    issuer: IssuerRef
    as_of: date
    insight: SnapshotInsightOut | None = None
    insight_pending: bool = False
    metrics: list[MetricTileOut] = Field(default_factory=list)
    wall: SnapshotWallOut | None = None
    timeline: list[TimelineItemOut] = Field(default_factory=list)
    thresholds: list[ThresholdBandOut] = Field(default_factory=list)


class OverviewHeaderOut(BaseModel):
    companies: int
    filings_read: int
    material_changes: int
    last_check: date | None = None


class BeforeAfterOut(BaseModel):
    field: str | None = None
    before: Any = None
    after: Any = None


class FeedItemOut(BaseModel):
    alert_id: str
    issuer: IssuerRef
    event_date: date | None = None
    title: str
    impact_sentence: str | None = None
    impact_pending: bool = False
    before_after: BeforeAfterOut | None = None
    live: bool = False


class WatchlistRowOut(BaseModel):
    issuer: IssuerRef
    ticker: str | None = None
    metrics: list[MetricTileOut] = Field(default_factory=list)


class EvalSummaryOut(BaseModel):
    text: str
    label: str | None = None
    kind: str | None = None
    evaluated_at: str | None = None
    precision: float | None = None
    recall: float | None = None
    incomplete: bool = False
    caught: int | None = None
    expected_material: int | None = None
    false_alert_count: int | None = None
    unsupported_claims: int | None = None
    accurate_caught: int | None = None
    accurate_total: int | None = None
    keeps_records: str | None = None
    wall_passed: int | None = None
    wall_total: int | None = None
    cost_usd: float | None = None
    missed_events: list[EvalDetailEventOut] = Field(default_factory=list)
    false_events: list[EvalDetailEventOut] = Field(default_factory=list)


class EvalComparisonRowOut(BaseModel):
    label: str
    kind: str | None = None
    caught: int | None = None
    expected_material: int | None = None
    accurate_caught: int | None = None
    accurate_total: int | None = None
    false_alert_count: int | None = None
    unsupported_claims: int | None = None
    keeps_records: str | None = None
    cost_usd: float | None = None


class EvalComparisonOut(BaseModel):
    rows: list[EvalComparisonRowOut] = Field(default_factory=list)
    note: str | None = None
    chart_crosscheck: ChartCrosscheckSummaryOut | None = None


class BriefSentenceOut(BaseModel):
    sentence: str
    refs: list[dict[str, str]] = Field(default_factory=list)


class DailyBriefOut(BaseModel):
    as_of: date
    status: str
    sentences: list[BriefSentenceOut] = Field(default_factory=list)
    refused_count: int = 0
    provider: str | None = None
    model: str | None = None
    check_model: str | None = None
    cost_usd: float | None = None
    model_calls: int = 0
    attribution: str = (
        "Written by Muse Wonder · checked by Muse Spark Open"
    )


class LiveDocumentOut(BaseModel):
    event_id: str
    alert_id: str | None = None
    issuer_key: str | None = None
    issuer_name: str | None = None
    form: str | None = None
    event_date: str | None = None
    title: str | None = None
    outcome: str
    reason: str | None = None
    cost_usd: float | None = None


class LiveSectionOut(BaseModel):
    since: str
    git_sha: str | None = None
    prompt_version: str | None = None
    documents_read: int = 0
    alerted: int = 0
    logged: int = 0
    refused: int = 0
    paused: int = 0
    failed: int = 0
    documents: list[LiveDocumentOut] = Field(default_factory=list)
    feed: list[FeedItemOut] = Field(default_factory=list)


class CreditOverviewOut(BaseModel):
    header: OverviewHeaderOut
    brief: DailyBriefOut | None = None
    feed: list[FeedItemOut] = Field(default_factory=list)
    watchlist: list[WatchlistRowOut] = Field(default_factory=list)
    evaluation: EvalSummaryOut | None = None
    baseline: EvalSummaryOut | None = None
    comparison: EvalComparisonOut | None = None
    live: LiveSectionOut | None = None
    thresholds: list[ThresholdBandOut] = Field(default_factory=list)
