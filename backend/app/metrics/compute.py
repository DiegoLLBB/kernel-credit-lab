"""Credit metric maths: tagged datapoints in, per-period metric rows out.

Twelve metrics per issuer (``METRIC_IDS``): total_debt, cash, st_investments,
due_12m, due_24m, liquidity_coverage, ebitda, leverage, net_leverage,
interest_coverage, fcf, fcf_to_debt.

Each metric gets one row per fiscal year (history for sparklines) plus one
latest-quarter row (TTM for flows). Anything unbuildable yields a row with
``value=None`` and the reason in ``detail["missing"]`` — the UI shows these as
"missing: <reason>", never an estimate. Guardrails: non-positive EBITDA voids
leverage/coverage (a negative multiple is worse than no multiple), zero
maturities void liquidity coverage, and the tag-vs-wall cross-check stores both
numbers when they disagree beyond tolerance so the wall can show both.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from app.metrics.config import IssuerMetricConfig
from app.metrics.select import (
    Datapoint,
    full_year_ends,
    select_full_year,
    select_instant,
    trailing_twelve_months,
)

METRIC_IDS = (
    "total_debt",
    "cash",
    "st_investments",
    "due_12m",
    "due_24m",
    "liquidity_coverage",
    "ebitda",
    "leverage",
    "net_leverage",
    "interest_coverage",
    "fcf",
    "fcf_to_debt",
)

_MONEY = "USD"
_MULTIPLE = "x"
_RATIO_METRICS = frozenset(
    {"leverage", "net_leverage", "interest_coverage", "liquidity_coverage", "fcf_to_debt"}
)


@dataclass(frozen=True)
class MetricRow:
    metric: str
    period_end: date
    fiscal_label: str
    value: Decimal | None
    unit: str
    source_tag: str
    accession: str | None
    filed: date | None
    detail: dict[str, Any]

    def missing_reason(self) -> str | None:
        reason = self.detail.get("missing")
        return str(reason) if reason else None


def _missing(
    metric: str, period_end: date, fiscal_label: str, reason: str, **detail: Any
) -> MetricRow:
    return MetricRow(
        metric=metric,
        period_end=period_end,
        fiscal_label=fiscal_label,
        value=None,
        unit=_MULTIPLE if metric in _RATIO_METRICS else _MONEY,
        source_tag="",
        accession=None,
        filed=None,
        detail={"missing": reason, **detail},
    )


def _quantize_ratio(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"))


def _latest_inputs(inputs: list[Datapoint]) -> tuple[str, str | None, date | None]:
    best = max(inputs, key=lambda d: d.filed, default=None)
    tags = "+".join(d.tag for d in inputs)
    if best is None:
        return tags, None, None
    return tags, best.accession or None, best.filed


def _reason(cfg: IssuerMetricConfig, field_name: str) -> str:
    return cfg.reason(field_name) or f"{field_name} tag not configured"


def _flow_missing_reason(
    cfg: IssuerMetricConfig, field_name: str, flow: FlowValue, *, is_quarter: bool
) -> str:
    """Why a flow input is missing: unconfigured tag vs no quarterly datapoint yet."""
    tag = cfg.tag(field_name)
    if not tag:
        return _reason(cfg, field_name)
    if is_quarter:
        return f"{tag} has no quarterly datapoint filed yet"
    return _reason(cfg, field_name)


def compute_issuer(
    doc: dict[str, Any],
    cfg: IssuerMetricConfig,
    *,
    as_of: date,
    wall: dict[date, tuple[Decimal, Decimal]] | None = None,
) -> list[MetricRow]:
    """Compute all metric rows for an issuer visible at ``as_of``.

    ``wall`` maps a metric period end to (wall_due_12m, wall_due_24m) from the
    maturity wall for the cross-check; pass None to skip it. Unusable issuers
    (``cfg.usable`` False) yield no rows — the API reports the config reason.
    """
    if not cfg.usable:
        return []
    anchor = cfg.tag("debt_total") or cfg.tag("debt_current")
    if anchor is None:
        return []
    periods = full_year_ends(doc, anchor, as_of=as_of, limit=cfg.history_years)
    if not periods:
        return []
    rows: list[MetricRow] = []
    for end in periods:
        # Label from the period end itself: 10-K comparatives re-tag the prior
        # year with the filing's fy (e.g. end 2023-12-31 carries fy=2024), so
        # the datapoint's fy attribute would mislabel the row.
        rows.extend(
            _compute_fy_period(doc, cfg, as_of=as_of, end=end, label=f"FY{end.year}", wall=wall)
        )
    quarter_rows = _compute_quarter(doc, cfg, as_of=as_of, wall=wall)
    seen = {r.period_end for r in rows}
    rows.extend(r for r in quarter_rows if r.period_end not in seen)
    return rows


def _point(
    doc: dict[str, Any], tag: str | None, *, as_of: date, end: date | None
) -> Datapoint | None:
    """Instant datapoint for an FY period (10-K facts only, ``fp="FY"``).

    The fp filter matters: 10-Qs restate the prior year-end balance sheet with
    the same period end, and without it the FY row would cite the 10-Q.
    """
    if not tag:
        return None
    return select_instant(doc, tag, as_of=as_of, period_end=end, fp="FY")


def _compute_fy_period(
    doc: dict[str, Any],
    cfg: IssuerMetricConfig,
    *,
    as_of: date,
    end: date,
    label: str,
    wall: dict[date, tuple[Decimal, Decimal]] | None,
) -> list[MetricRow]:
    debt = _total_debt_fy(doc, cfg, as_of=as_of, end=end)
    return _assemble(
        cfg,
        period_end=end,
        label=label,
        wall=wall,
        debt=debt,
        cash_dp=_point(doc, cfg.tag("cash"), as_of=as_of, end=end),
        stinv_dp=_point(doc, cfg.tag("st_investments"), as_of=as_of, end=end),
        mat12_dp=_point(doc, cfg.tag("mat_next12"), as_of=as_of, end=end),
        mat2_dp=_point(doc, cfg.tag("mat_year2"), as_of=as_of, end=end),
        opinc=_fy_flow(doc, cfg.tag("opinc"), as_of=as_of, end=end),
        dep=_fy_flow(doc, cfg.tag("depreciation"), as_of=as_of, end=end),
        interest=_fy_flow(doc, cfg.tag("interest"), as_of=as_of, end=end),
        ocf=_fy_flow(doc, cfg.tag("ocf"), as_of=as_of, end=end),
        capex=_fy_flow(doc, cfg.tag("capex"), as_of=as_of, end=end),
    )


def _compute_quarter(
    doc: dict[str, Any],
    cfg: IssuerMetricConfig,
    *,
    as_of: date,
    wall: dict[date, tuple[Decimal, Decimal]] | None,
) -> list[MetricRow]:
    latest = _latest_point_ends(doc, cfg, as_of=as_of)
    if not latest:
        return []
    period_end = max(latest)
    quarter_label = _quarter_label(doc, cfg, as_of=as_of, period_end=period_end)
    has_ttm = False
    flows = {}
    for name, field_name in (
        ("opinc", "opinc"),
        ("dep", "depreciation"),
        ("interest", "interest"),
        ("ocf", "ocf"),
        ("capex", "capex"),
    ):
        value, rep = _ttm_flow(doc, cfg.tag(field_name), as_of=as_of)
        flows[name] = (value, rep)
        has_ttm = has_ttm or value is not None
    label = f"TTM {quarter_label}" if has_ttm else quarter_label
    return _assemble(
        cfg,
        period_end=period_end,
        label=label,
        wall=wall,
        debt=_total_debt_latest(doc, cfg, as_of=as_of),
        cash_dp=select_instant(doc, cfg.tag("cash") or "", as_of=as_of),
        stinv_dp=select_instant(doc, cfg.tag("st_investments") or "", as_of=as_of),
        mat12_dp=select_instant(doc, cfg.tag("mat_next12") or "", as_of=as_of),
        mat2_dp=select_instant(doc, cfg.tag("mat_year2") or "", as_of=as_of),
        opinc=flows["opinc"],
        dep=flows["dep"],
        interest=flows["interest"],
        ocf=flows["ocf"],
        capex=flows["capex"],
        is_quarter=True,
    )


def _latest_point_ends(doc: dict[str, Any], cfg: IssuerMetricConfig, *, as_of: date) -> list[date]:
    ends = []
    for field_name in ("debt_total", "debt_current", "cash", "mat_next12"):
        tag = cfg.tag(field_name)
        if not tag:
            continue
        dp = select_instant(doc, tag, as_of=as_of)
        if dp is not None:
            ends.append(dp.end)
    return ends


def _quarter_label(
    doc: dict[str, Any], cfg: IssuerMetricConfig, *, as_of: date, period_end: date
) -> str:
    # Any point datapoint ending on the quarter end carries the filing's fp
    # (Oracle's August quarter is fp=Q1). Fall back to the calendar quarter.
    # Label is fp + calendar year of the period end ("Q1 2026" for Aug 2026).
    for field_name in ("debt_total", "debt_current", "cash", "mat_next12"):
        tag = cfg.tag(field_name)
        if not tag:
            continue
        dp = select_instant(doc, tag, as_of=as_of, period_end=period_end)
        if dp is not None and dp.fp in ("Q1", "Q2", "Q3", "Q4"):
            return f"{dp.fp} {period_end.year}"
    return f"Q{(period_end.month - 1) // 3 + 1} {period_end.year}"


FlowValue = tuple[Decimal | None, Datapoint | None]


def _fy_flow(doc: dict[str, Any], tag: str | None, *, as_of: date, end: date) -> FlowValue:
    if not tag:
        return None, None
    dp = select_full_year(doc, tag, as_of=as_of, period_end=end)
    return (dp.value, dp) if dp else (None, None)


def _ttm_flow(doc: dict[str, Any], tag: str | None, *, as_of: date) -> FlowValue:
    if not tag:
        return None, None
    ttm = trailing_twelve_months(doc, tag, as_of=as_of)
    if ttm is None or ttm.is_full_year_only:
        return None, None
    return ttm.value, ttm.ytd or ttm.full_year


def _total_debt_fy(
    doc: dict[str, Any], cfg: IssuerMetricConfig, *, as_of: date, end: date
) -> tuple[Decimal | None, list[Datapoint]]:
    if cfg.tag("debt_total"):
        parts = [_point(doc, cfg.tag("debt_total"), as_of=as_of, end=end)]
        parts.extend(_point(doc, extra, as_of=as_of, end=end) for extra in cfg.debt_extra)
        if any(p is None for p in parts):
            return None, []
        dps = [p for p in parts if p is not None]
        return sum((p.value for p in dps), Decimal(0)), dps
    cur = _point(doc, cfg.tag("debt_current"), as_of=as_of, end=end)
    non = _point(doc, cfg.tag("debt_noncurrent"), as_of=as_of, end=end)
    if cur is None or non is None:
        return None, []
    return cur.value + non.value, [cur, non]


def _total_debt_latest(
    doc: dict[str, Any], cfg: IssuerMetricConfig, *, as_of: date
) -> tuple[Decimal | None, list[Datapoint]]:
    if cfg.tag("debt_total"):
        tags = [cfg.tag("debt_total"), *cfg.debt_extra]
        dps = [select_instant(doc, t, as_of=as_of) for t in tags if t]
        if any(d is None for d in dps):
            return None, []
        clean = [d for d in dps if d is not None]
        return sum((d.value for d in clean), Decimal(0)), clean
    cur = select_instant(doc, cfg.tag("debt_current") or "", as_of=as_of)
    non = select_instant(doc, cfg.tag("debt_noncurrent") or "", as_of=as_of)
    if cur is None or non is None:
        return None, []
    return cur.value + non.value, [cur, non]


def _assemble(
    cfg: IssuerMetricConfig,
    *,
    period_end: date,
    label: str,
    wall: dict[date, tuple[Decimal, Decimal]] | None,
    debt: tuple[Decimal | None, list[Datapoint]],
    cash_dp: Datapoint | None,
    stinv_dp: Datapoint | None,
    mat12_dp: Datapoint | None,
    mat2_dp: Datapoint | None,
    opinc: FlowValue,
    dep: FlowValue,
    interest: FlowValue,
    ocf: FlowValue,
    capex: FlowValue,
    is_quarter: bool = False,
) -> list[MetricRow]:
    rows: list[MetricRow] = []
    debt_val, debt_dps = debt
    tol = cfg.wall_tolerance_pct / 100

    if debt_val is None:
        rows.append(
            _missing(
                "total_debt",
                period_end,
                label,
                _reason(cfg, "debt_total" if cfg.tag("debt_total") else "debt_current"),
            )
        )
    else:
        tags, accn, filed = _latest_inputs(debt_dps)
        rows.append(
            MetricRow("total_debt", period_end, label, debt_val, _MONEY, tags, accn, filed, {})
        )
    rows.append(_money_row("cash", period_end, label, cash_dp, _reason(cfg, "cash")))
    if stinv_dp is None:
        rows.append(
            _missing(
                "st_investments",
                period_end,
                label,
                cfg.reason("st_investments") or _reason(cfg, "st_investments"),
            )
        )
    else:
        rows.append(
            _money_row(
                "st_investments", period_end, label, stinv_dp, _reason(cfg, "st_investments")
            )
        )

    due12, due12_row = _due_row(
        "due_12m", period_end, label, mat12_dp, cfg, wall, tol, wall_index=0
    )
    rows.append(due12_row)
    due24, due24_row = _due_24m_row(period_end, label, mat12_dp, mat2_dp, cfg, wall, tol)
    rows.append(due24_row)

    cash_val = cash_dp.value if cash_dp else None
    stinv_val = stinv_dp.value if stinv_dp else None
    rows.append(
        _liquidity_row(
            period_end,
            label,
            cash_val,
            stinv_val,
            due12,
            due24,
            [d for d in (cash_dp, stinv_dp) if d],
            cfg,
        )
    )

    ebitda: Decimal | None = None
    ebitda_dps: list[Datapoint] = []
    if opinc[0] is not None and dep[0] is not None:
        ebitda = opinc[0] + dep[0]
        ebitda_dps = [d for d in (opinc[1], dep[1]) if d is not None]
    if ebitda is None:
        missing = (
            _flow_missing_reason(cfg, "opinc", opinc, is_quarter=is_quarter)
            if opinc[0] is None
            else _flow_missing_reason(cfg, "depreciation", dep, is_quarter=is_quarter)
        )
        rows.append(_missing("ebitda", period_end, label, missing))
    else:
        tags, accn, filed = _latest_inputs(ebitda_dps)
        rows.append(MetricRow("ebitda", period_end, label, ebitda, _MONEY, tags, accn, filed, {}))

    rows.append(
        _leverage_row(
            "leverage", period_end, label, ebitda, debt_val, None, None, ebitda_dps, debt_dps
        )
    )
    rows.append(
        _leverage_row(
            "net_leverage",
            period_end,
            label,
            ebitda,
            debt_val,
            cash_val,
            stinv_val,
            ebitda_dps,
            debt_dps,
        )
    )
    rows.append(
        _coverage_row(period_end, label, ebitda, interest, ebitda_dps, cfg, is_quarter=is_quarter)
    )

    fcf: Decimal | None = None
    fcf_dps: list[Datapoint] = []
    if ocf[0] is not None and capex[0] is not None:
        fcf = ocf[0] - capex[0]
        fcf_dps = [d for d in (ocf[1], capex[1]) if d is not None]
    if fcf is None:
        missing = (
            _flow_missing_reason(cfg, "ocf", ocf, is_quarter=is_quarter)
            if ocf[0] is None
            else _flow_missing_reason(cfg, "capex", capex, is_quarter=is_quarter)
        )
        rows.append(_missing("fcf", period_end, label, missing))
    else:
        tags, accn, filed = _latest_inputs(fcf_dps)
        rows.append(MetricRow("fcf", period_end, label, fcf, _MONEY, tags, accn, filed, {}))
        if debt_val is None:
            rows.append(_missing("fcf_to_debt", period_end, label, "total debt unavailable"))
        elif debt_val == 0:
            rows.append(_missing("fcf_to_debt", period_end, label, "total debt is zero"))
        else:
            tags, accn, filed = _latest_inputs(fcf_dps + debt_dps)
            rows.append(
                MetricRow(
                    "fcf_to_debt",
                    period_end,
                    label,
                    _quantize_ratio(fcf / debt_val),
                    _MULTIPLE,
                    tags,
                    accn,
                    filed,
                    {"fcf": str(fcf), "total_debt": str(debt_val)},
                )
            )
    return rows


def _money_row(
    metric: str, period_end: date, label: str, dp: Datapoint | None, reason: str
) -> MetricRow:
    if dp is None:
        return _missing(metric, period_end, label, reason)
    return MetricRow(
        metric, period_end, label, dp.value, _MONEY, dp.tag, dp.accession or None, dp.filed, {}
    )


def _due_reason(cfg: IssuerMetricConfig) -> str:
    if cfg.tag("mat_next12") is None:
        return cfg.reason("mat_next12") or "maturities not tagged"
    if cfg.tag("mat_year2") is None:
        return cfg.reason("mat_year2") or "year-two maturities not tagged"
    return "maturity datapoints missing for the period"


def _wall_detail(
    value: Decimal,
    period_end: date,
    wall: dict[date, tuple[Decimal, Decimal]] | None,
    tol: float,
    wall_index: int,
) -> dict[str, Any]:
    if not wall or period_end not in wall:
        return {}
    w = wall[period_end][wall_index]
    agrees = abs(value - w) <= abs(w * Decimal(str(tol))) if w != 0 else value == 0
    return {"wall_due": str(w), "wall_agrees": agrees}


def _due_row(
    metric: str,
    period_end: date,
    label: str,
    dp: Datapoint | None,
    cfg: IssuerMetricConfig,
    wall: dict[date, tuple[Decimal, Decimal]] | None,
    tol: float,
    wall_index: int,
) -> tuple[Decimal | None, MetricRow]:
    if dp is None:
        return None, _missing(metric, period_end, label, _due_reason(cfg))
    return dp.value, MetricRow(
        metric,
        period_end,
        label,
        dp.value,
        _MONEY,
        dp.tag,
        dp.accession or None,
        dp.filed,
        _wall_detail(dp.value, period_end, wall, tol, wall_index),
    )


def _due_24m_row(
    period_end: date,
    label: str,
    mat12: Datapoint | None,
    mat2: Datapoint | None,
    cfg: IssuerMetricConfig,
    wall: dict[date, tuple[Decimal, Decimal]] | None,
    tol: float,
) -> tuple[Decimal | None, MetricRow]:
    if mat12 is None or mat2 is None:
        return None, _missing("due_24m", period_end, label, _due_reason(cfg))
    value = mat12.value + mat2.value
    tags, accn, filed = _latest_inputs([mat12, mat2])
    return value, MetricRow(
        "due_24m",
        period_end,
        label,
        value,
        _MONEY,
        tags,
        accn,
        filed,
        _wall_detail(value, period_end, wall, tol, 1),
    )


def _liquidity_row(
    period_end: date,
    label: str,
    cash_val: Decimal | None,
    stinv_val: Decimal | None,
    due12: Decimal | None,
    due24: Decimal | None,
    inputs: list[Datapoint],
    cfg: IssuerMetricConfig,
) -> MetricRow:
    del due12
    if cash_val is None:
        return _missing("liquidity_coverage", period_end, label, _reason(cfg, "cash"))
    if due24 is None:
        return _missing(
            "liquidity_coverage", period_end, label, _due_reason(cfg), cash_only=stinv_val is None
        )
    if due24 == 0:
        return _missing(
            "liquidity_coverage", period_end, label, "no debt matures in the next 24 months"
        )
    cushion = cash_val + (stinv_val or Decimal(0))
    tags, accn, filed = _latest_inputs(inputs)
    return MetricRow(
        "liquidity_coverage",
        period_end,
        label,
        _quantize_ratio(cushion / due24),
        _MULTIPLE,
        tags,
        accn,
        filed,
        {
            "cash_only": stinv_val is None,
            "cash": str(cash_val),
            "st_investments": str(stinv_val) if stinv_val is not None else None,
            "due_24m": str(due24),
        },
    )


def _leverage_row(
    metric: str,
    period_end: date,
    label: str,
    ebitda: Decimal | None,
    debt: Decimal | None,
    cash: Decimal | None,
    stinv: Decimal | None,
    ebitda_dps: list[Datapoint],
    debt_dps: list[Datapoint],
) -> MetricRow:
    if ebitda is None or debt is None:
        return _missing(metric, period_end, label, "EBITDA or total debt unavailable")
    if ebitda <= 0:
        return _missing(metric, period_end, label, "EBITDA non-positive; leverage not meaningful")
    if metric == "net_leverage":
        if cash is None:
            return _missing(metric, period_end, label, "cash unavailable")
        numerator = debt - cash - (stinv or Decimal(0))
        detail: dict[str, Any] = {
            "total_debt": str(debt),
            "cash": str(cash),
            "cash_only": stinv is None,
            "st_investments": str(stinv) if stinv is not None else None,
        }
    else:
        numerator = debt
        detail = {"total_debt": str(debt), "ebitda": str(ebitda)}
    tags, accn, filed = _latest_inputs(ebitda_dps + debt_dps)
    return MetricRow(
        metric,
        period_end,
        label,
        _quantize_ratio(numerator / ebitda),
        _MULTIPLE,
        tags,
        accn,
        filed,
        detail,
    )


def _coverage_row(
    period_end: date,
    label: str,
    ebitda: Decimal | None,
    interest: FlowValue,
    ebitda_dps: list[Datapoint],
    cfg: IssuerMetricConfig,
    *,
    is_quarter: bool,
) -> MetricRow:
    if ebitda is None:
        return _missing("interest_coverage", period_end, label, "EBITDA unavailable")
    if interest[0] is None:
        return _missing(
            "interest_coverage",
            period_end,
            label,
            _flow_missing_reason(cfg, "interest", interest, is_quarter=is_quarter),
        )
    if interest[0] <= 0:
        return _missing(
            "interest_coverage",
            period_end,
            label,
            "interest expense non-positive; coverage not meaningful",
        )
    if ebitda <= 0:
        return _missing(
            "interest_coverage", period_end, label, "EBITDA non-positive; coverage not meaningful"
        )
    assert interest[0] is not None
    inputs = ebitda_dps + ([interest[1]] if interest[1] is not None else [])
    tags, accn, filed = _latest_inputs(inputs)
    return MetricRow(
        "interest_coverage",
        period_end,
        label,
        _quantize_ratio(ebitda / interest[0]),
        _MULTIPLE,
        tags,
        accn,
        filed,
        {"ebitda": str(ebitda), "interest_expense": str(interest[0])},
    )
