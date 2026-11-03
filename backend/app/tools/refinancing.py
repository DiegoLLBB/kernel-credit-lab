"""Display-only refinancing pressure (not wired into the agent).

Benchmark: selectable FRED constant-maturity Treasury (DGS2/5/7/10), default 5y,
latest observation on or before as_of. Spread = cost of debt − benchmark, floored
at 0.5% and capped at 3.0%. All maturing debt is assumed refinanced at that tenor.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CallTerms, CreditMetric, Instrument, Issuer, Watchlist
from app.tools.fx import latest_on_or_before
from app.tools.maturity import maturity_wall

HORIZON_LABELS = ("FY2026", "FY2027", "FY2028", "FY2029")
SHOCK_BPS_MIN = 0
SHOCK_BPS_MAX = 300
DEFAULT_TENOR_YEARS = 5
TENOR_SERIES: dict[int, str] = {2: "DGS2", 5: "DGS5", 7: "DGS7", 10: "DGS10"}
TENOR_YEARS = tuple(TENOR_SERIES.keys())

SPREAD_FLOOR_PCT = Decimal("0.5")
SPREAD_CAP_PCT = Decimal("3.0")

FOOTNOTE = (
    "Illustrative. Assumes all maturing debt is refinanced at the same tenor; "
    "not a forecast or investment advice."
)

BASE_SCENARIO_LINE = (
    "At today's Treasury plus the company's own borrowing premium, before any rate shock."
)

PRESSURE_WEIGHTS: dict[str, Decimal] = {
    "rate_reset_cost": Decimal("0.45"),
    "maturity_concentration_24m": Decimal("0.35"),
    "coverage_impact": Decimal("0.20"),
}

_HUNDRED = Decimal("100")
_ZERO = Decimal("0")
_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def _dec(value: Any) -> Decimal:
    if value is None:
        return _ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def clamp_shock_bps(shock_bps: int) -> int:
    return max(SHOCK_BPS_MIN, min(SHOCK_BPS_MAX, int(shock_bps)))


def normalize_tenor_years(tenor_years: int | None) -> int:
    if tenor_years in TENOR_SERIES:
        return int(tenor_years)
    return DEFAULT_TENOR_YEARS


def format_day(d: date) -> str:
    return f"{d.day} {_MONTHS[d.month - 1]} {d.year}"


def refinance_date(inst: Instrument, call_terms: CallTerms | None = None) -> date:
    ct = call_terms if call_terms is not None else inst.call_terms
    if ct is not None and ct.par_call_date is not None:
        return ct.par_call_date
    return inst.maturity


def _latest_metric(
    session: Session, issuer_id: str, metric: str, as_of: date
) -> CreditMetric | None:
    return session.scalars(
        select(CreditMetric)
        .where(
            CreditMetric.issuer_id == issuer_id,
            CreditMetric.metric == metric,
            CreditMetric.filed.is_not(None),
            CreditMetric.filed <= as_of,
            CreditMetric.value.is_not(None),
        )
        .order_by(CreditMetric.period_end.desc())
    ).first()


def _metric_series(
    session: Session, issuer_id: str, metric: str, as_of: date
) -> list[CreditMetric]:
    return list(
        session.scalars(
            select(CreditMetric)
            .where(
                CreditMetric.issuer_id == issuer_id,
                CreditMetric.metric == metric,
                CreditMetric.filed.is_not(None),
                CreditMetric.filed <= as_of,
                CreditMetric.value.is_not(None),
            )
            .order_by(CreditMetric.period_end.asc())
        ).all()
    )


def _named_rows(bucket: dict[str, Any]) -> list[dict[str, Any]]:
    return list(bucket.get("named_active") or []) + list(bucket.get("new_since_schedule") or [])


def treasury_benchmark(
    session: Session,
    as_of: date,
    *,
    tenor_years: int = DEFAULT_TENOR_YEARS,
    shock_bps: int = 0,
) -> dict[str, Any]:
    """Latest DGS on/before as_of for the selected tenor (+ optional parallel shock)."""
    tenor_years = normalize_tenor_years(tenor_years)
    series_id = TENOR_SERIES[tenor_years]
    shock_bps = clamp_shock_bps(shock_bps)
    got = latest_on_or_before(session, series_id, as_of)
    if got is None:
        return {
            "tenor_years": tenor_years,
            "series_id": series_id,
            "obs_date": None,
            "value": None,
            "shocked_value": None,
            "shock_bps": shock_bps,
            "missing": f"no {series_id} observation on or before {as_of.isoformat()}",
        }
    obs_date, value = got
    shocked = value + (Decimal(shock_bps) / _HUNDRED)
    return {
        "tenor_years": tenor_years,
        "series_id": series_id,
        "obs_date": obs_date.isoformat(),
        "value": str(value),
        "shocked_value": str(shocked),
        "shock_bps": shock_bps,
        "missing": None,
    }


def cost_of_debt(session: Session, issuer_id: str, as_of: date) -> dict[str, Any]:
    """Interest expense ÷ average total debt. Never estimates interest."""
    coverage = _latest_metric(session, issuer_id, "interest_coverage", as_of)
    if coverage is None:
        return {
            "rate_pct": None,
            "interest_expense": None,
            "average_total_debt": None,
            "fiscal_label": None,
            "period_end": None,
            "missing": "interest expense unavailable",
            "source": None,
        }
    detail = coverage.detail or {}
    interest_raw = detail.get("interest_expense")
    if interest_raw is None:
        return {
            "rate_pct": None,
            "interest_expense": None,
            "average_total_debt": None,
            "fiscal_label": coverage.fiscal_label,
            "period_end": coverage.period_end.isoformat() if coverage.period_end else None,
            "missing": "interest expense unavailable",
            "source": None,
        }
    interest = _dec(interest_raw)
    if interest <= 0:
        return {
            "rate_pct": None,
            "interest_expense": str(interest),
            "average_total_debt": None,
            "fiscal_label": coverage.fiscal_label,
            "period_end": coverage.period_end.isoformat() if coverage.period_end else None,
            "missing": "interest expense non-positive",
            "source": None,
        }

    debt_series = _metric_series(session, issuer_id, "total_debt", as_of)
    if not debt_series:
        return {
            "rate_pct": None,
            "interest_expense": str(interest),
            "average_total_debt": None,
            "fiscal_label": coverage.fiscal_label,
            "period_end": coverage.period_end.isoformat() if coverage.period_end else None,
            "missing": "total debt unavailable",
            "source": None,
        }

    latest_debt = debt_series[-1]
    from app.metrics.periods import prior_same_period

    prior_debt = prior_same_period(debt_series, latest_debt)
    if (
        prior_debt is not None
        and prior_debt.value is not None
        and latest_debt.value is not None
        and _dec(prior_debt.value) > 0
        and _dec(latest_debt.value) > 0
    ):
        avg_debt = (_dec(prior_debt.value) + _dec(latest_debt.value)) / Decimal("2")
        debt_note = "average of prior and latest total debt"
    elif latest_debt.value is not None and _dec(latest_debt.value) > 0:
        avg_debt = _dec(latest_debt.value)
        debt_note = "latest total debt (prior unavailable)"
    else:
        return {
            "rate_pct": None,
            "interest_expense": str(interest),
            "average_total_debt": None,
            "fiscal_label": coverage.fiscal_label,
            "period_end": coverage.period_end.isoformat() if coverage.period_end else None,
            "missing": "total debt unavailable",
            "source": None,
        }

    rate = (interest / avg_debt) * _HUNDRED
    return {
        "rate_pct": str(rate),
        "interest_expense": str(interest),
        "average_total_debt": str(avg_debt),
        "fiscal_label": coverage.fiscal_label,
        "period_end": coverage.period_end.isoformat() if coverage.period_end else None,
        "missing": None,
        "source": f"interest expense ÷ {debt_note}",
    }


def issuer_spread(
    session: Session,
    issuer_id: str,
    as_of: date,
    *,
    tenor_years: int = DEFAULT_TENOR_YEARS,
) -> dict[str, Any]:
    """Spread % = COD − benchmark Treasury, floored at 0.5% and capped at 3.0%."""
    tenor_years = normalize_tenor_years(tenor_years)
    bench = treasury_benchmark(session, as_of, tenor_years=tenor_years, shock_bps=0)
    cod = cost_of_debt(session, issuer_id, as_of)

    if bench.get("missing"):
        return {
            "spread_pct": None,
            "spread_bps": None,
            "raw_spread_pct": None,
            "floored": False,
            "capped": False,
            "derivation_line": None,
            "missing": bench["missing"],
            "benchmark": bench,
            "cost_of_debt": cod,
        }
    if cod.get("missing"):
        return {
            "spread_pct": None,
            "spread_bps": None,
            "raw_spread_pct": None,
            "floored": False,
            "capped": False,
            "derivation_line": None,
            "missing": cod["missing"],
            "benchmark": bench,
            "cost_of_debt": cod,
        }

    tsy = _dec(bench["value"])
    rate = _dec(cod["rate_pct"])
    raw = rate - tsy
    floored = raw < SPREAD_FLOOR_PCT
    capped = raw > SPREAD_CAP_PCT
    spread = min(SPREAD_CAP_PCT, max(SPREAD_FLOOR_PCT, raw))
    spread_bps = int((spread * _HUNDRED).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    obs = date.fromisoformat(str(bench["obs_date"]))
    fiscal = str(cod.get("fiscal_label") or "latest")
    # "2025 cost of debt" from FY2025 / 2025 labels.
    year_bit = fiscal.replace("FY", "").strip() or "latest"
    derivation = (
        f"{tenor_years}-year Treasury {tsy:.2f}% on {format_day(obs)} "
        f"+ {spread:.2f}% spread from {year_bit} cost of debt "
        f"(interest expense ÷ average debt, minus Treasury"
        + (", raised to the 0.50% floor" if floored else "")
        + (", cut to the 3.00% cap" if capped else "")
        + "; a book figure, not a market spread)"
    )

    return {
        "spread_pct": str(spread),
        "spread_bps": spread_bps,
        "raw_spread_pct": str(raw),
        "floored": floored,
        "capped": capped,
        "derivation_line": derivation,
        "missing": None,
        "benchmark": bench,
        "cost_of_debt": cod,
    }


def bucket_maturing_coupon(
    session: Session,
    bucket: dict[str, Any],
    *,
    cost_of_debt_pct: Decimal | None,
) -> dict[str, Any]:
    """Weighted coupon of named bonds when known; else cost of debt."""
    coupon_amt = _ZERO
    total_amt = _ZERO
    for row in _named_rows(bucket):
        inst = session.get(Instrument, row["id"]) if row.get("id") else None
        if inst is None:
            continue
        amt = _dec(row.get("amount_outstanding"))
        if amt <= 0:
            continue
        coupon_amt += amt * _dec(inst.coupon)
        total_amt += amt
    if total_amt > 0:
        return {
            "coupon_pct": str(coupon_amt / total_amt),
            "source": "weighted coupon of named bonds",
            "named_amount": str(total_amt),
        }
    if cost_of_debt_pct is not None:
        return {
            "coupon_pct": str(cost_of_debt_pct),
            "source": "cost of debt (no named bonds in bucket)",
            "named_amount": "0",
        }
    return {
        "coupon_pct": None,
        "source": "unavailable",
        "named_amount": "0",
        "missing": "no named coupons and cost of debt unavailable",
    }


def calculate_rate_reset_cost(
    session: Session,
    issuer_id: str,
    as_of: date,
    *,
    shock_bps: int = 0,
    tenor_years: int = DEFAULT_TENOR_YEARS,
    wall: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Incremental annual interest at one refinancing tenor for all maturing debt."""
    tenor_years = normalize_tenor_years(tenor_years)
    shock_bps = clamp_shock_bps(shock_bps)
    wall = wall if wall is not None else maturity_wall(session, issuer_id, as_of)
    spread_info = issuer_spread(session, issuer_id, as_of, tenor_years=tenor_years)
    bench = treasury_benchmark(session, as_of, tenor_years=tenor_years, shock_bps=shock_bps)

    if spread_info.get("missing") or bench.get("missing"):
        reason = spread_info.get("missing") or bench.get("missing")
        return {
            "issuer_id": issuer_id,
            "as_of": as_of.isoformat(),
            "tenor_years": tenor_years,
            "shock_bps": shock_bps,
            "spread": spread_info,
            "benchmark": bench,
            "derivation_line": spread_info.get("derivation_line"),
            "total_incremental_annual_interest": None,
            "named_incremental_annual_interest": None,
            "estimated_incremental_annual_interest": None,
            "buckets": [],
            "missing": reason,
            "footnote": FOOTNOTE,
        }

    spread_pct = _dec(spread_info["spread_pct"])
    reset_yield = _dec(bench["shocked_value"]) + spread_pct
    cod_pct = (
        _dec(spread_info["cost_of_debt"]["rate_pct"])
        if spread_info.get("cost_of_debt", {}).get("rate_pct") is not None
        else None
    )

    buckets_out: list[dict[str, Any]] = []
    named_total = _ZERO
    estimated_total = _ZERO

    for label in HORIZON_LABELS:
        bucket = (wall.get("buckets") or {}).get(label) or {}
        named_sum = _dec(bucket.get("named_active_sum")) + _dec(
            bucket.get("new_since_schedule_sum")
        )
        unattributed = _dec(bucket.get("unattributed"))
        total = _dec(bucket.get("total_outstanding"))
        if total <= 0 and named_sum <= 0 and unattributed <= 0:
            continue

        named_coupon = bucket_maturing_coupon(session, bucket, cost_of_debt_pct=cod_pct)
        # Named slice uses weighted named coupon when present.
        if named_sum > 0 and named_coupon.get("coupon_pct") is not None:
            named_cpn = _dec(named_coupon["coupon_pct"])
            named_inc = named_sum * max(_ZERO, reset_yield - named_cpn) / _HUNDRED
            named_src = named_coupon["source"]
        else:
            named_inc = _ZERO
            named_cpn = None
            named_src = named_coupon.get("source") or "unavailable"

        if unattributed > 0 and cod_pct is not None:
            rem_inc = unattributed * max(_ZERO, reset_yield - cod_pct) / _HUNDRED
            rem_src = "cost of debt"
            rem_cpn = str(cod_pct)
        elif unattributed > 0:
            rem_inc = _ZERO
            rem_src = "unavailable"
            rem_cpn = None
        else:
            rem_inc = _ZERO
            rem_src = "none"
            rem_cpn = None

        named_total += named_inc
        estimated_total += rem_inc
        buckets_out.append(
            {
                "label": label,
                "total_outstanding": str(total),
                "named_amount": str(named_sum),
                "unattributed_amount": str(unattributed),
                "maturing_coupon_pct": named_coupon.get("coupon_pct"),
                "maturing_coupon_source": named_src,
                "remainder_coupon_pct": rem_cpn,
                "remainder_coupon_source": rem_src,
                "named_incremental_annual_interest": str(named_inc),
                "estimated_incremental_annual_interest": str(rem_inc),
                "incremental_annual_interest": str(named_inc + rem_inc),
                "reset_yield": str(reset_yield),
            }
        )

    return {
        "issuer_id": issuer_id,
        "as_of": as_of.isoformat(),
        "tenor_years": tenor_years,
        "shock_bps": shock_bps,
        "spread": spread_info,
        "benchmark": bench,
        "derivation_line": spread_info.get("derivation_line"),
        "reset_yield": str(reset_yield),
        "total_incremental_annual_interest": str(named_total + estimated_total),
        "named_incremental_annual_interest": str(named_total),
        "estimated_incremental_annual_interest": str(estimated_total),
        "buckets": buckets_out,
        "missing": None,
        "footnote": FOOTNOTE,
    }


def calculate_proforma_coverage(
    session: Session,
    issuer_id: str,
    as_of: date,
    *,
    shock_bps: int = 0,
    tenor_years: int = DEFAULT_TENOR_YEARS,
    rate_reset: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Coverage before/after reset. Missing EBITDA or interest → null with reason."""
    tenor_years = normalize_tenor_years(tenor_years)
    shock_bps = clamp_shock_bps(shock_bps)
    rate_reset = rate_reset or calculate_rate_reset_cost(
        session, issuer_id, as_of, shock_bps=shock_bps, tenor_years=tenor_years
    )

    ebitda_row = _latest_metric(session, issuer_id, "ebitda", as_of)
    coverage_row = _latest_metric(session, issuer_id, "interest_coverage", as_of)

    missing: str | None = None
    ebitda: Decimal | None = None
    interest: Decimal | None = None

    if ebitda_row is None or ebitda_row.value is None:
        missing = "EBITDA unavailable"
    else:
        ebitda = _dec(ebitda_row.value)
        if ebitda <= 0:
            missing = "EBITDA non-positive"
            ebitda = None

    if missing is None:
        if coverage_row is None:
            missing = "interest expense unavailable"
        else:
            detail = coverage_row.detail or {}
            if detail.get("interest_expense") is not None:
                interest = _dec(detail["interest_expense"])
            else:
                missing = "interest expense unavailable"
            if interest is not None and interest <= 0:
                missing = "interest expense non-positive"
                interest = None

    reset_cost = rate_reset.get("total_incremental_annual_interest")
    reset_dec = _dec(reset_cost) if reset_cost is not None else None

    before = after = None
    if missing is None and ebitda is not None and interest is not None:
        before = ebitda / interest
        if reset_dec is not None:
            after_interest = interest + reset_dec
            if after_interest > 0:
                after = ebitda / after_interest

    return {
        "issuer_id": issuer_id,
        "as_of": as_of.isoformat(),
        "tenor_years": tenor_years,
        "shock_bps": shock_bps,
        "ebitda": str(ebitda) if ebitda is not None else None,
        "interest_expense": str(interest) if interest is not None else None,
        "rate_reset_cost": str(reset_dec) if reset_dec is not None else None,
        "coverage_before": str(before) if before is not None else None,
        "coverage_after": str(after) if after is not None else None,
        "coverage_delta": str(after - before) if before is not None and after is not None else None,
        "missing": missing,
    }


def maturity_concentration_24m(
    session: Session, issuer_id: str, as_of: date, wall: dict[str, Any] | None = None
) -> dict[str, Any]:
    wall = wall if wall is not None else maturity_wall(session, issuer_id, as_of)
    near = _ZERO
    for label in ("FY2026", "FY2027"):
        bucket = (wall.get("buckets") or {}).get(label) or {}
        near += _dec(bucket.get("total_outstanding"))
    total_debt_row = _latest_metric(session, issuer_id, "total_debt", as_of)
    total_debt = (
        _dec(total_debt_row.value) if total_debt_row and total_debt_row.value is not None else None
    )
    if total_debt is None or total_debt <= 0:
        total_debt = _ZERO
        for bucket in (wall.get("buckets") or {}).values():
            total_debt += _dec(bucket.get("total_outstanding"))
    share = (near / total_debt) if total_debt > 0 else _ZERO
    return {"due_24m": str(near), "total_debt": str(total_debt), "share": str(share)}


def _normalize(values: list[Decimal]) -> list[Decimal]:
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        return [_ZERO for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


def rank_refinancing_pressure(
    session: Session,
    as_of: date,
    *,
    issuer_ids: list[str] | None = None,
    shock_bps: int = 0,
    tenor_years: int = DEFAULT_TENOR_YEARS,
) -> dict[str, Any]:
    """Rank on filing-derived reset cost, 24m share, coverage drop. Weights visible."""
    tenor_years = normalize_tenor_years(tenor_years)
    shock_bps = clamp_shock_bps(shock_bps)
    if issuer_ids is None:
        watched = session.scalars(select(Watchlist)).all()
        if watched:
            issuer_ids = [w.issuer_id for w in watched]
        else:
            issuer_ids = list(
                session.scalars(select(Issuer.id).where(Issuer.parent_id.is_(None))).all()
            )

    rows: list[dict[str, Any]] = []
    for iid in issuer_ids:
        issuer = session.get(Issuer, iid)
        if issuer is None:
            continue
        wall = maturity_wall(session, iid, as_of)
        reset = calculate_rate_reset_cost(
            session, iid, as_of, shock_bps=shock_bps, tenor_years=tenor_years, wall=wall
        )
        coverage = calculate_proforma_coverage(
            session,
            iid,
            as_of,
            shock_bps=shock_bps,
            tenor_years=tenor_years,
            rate_reset=reset,
        )
        conc = maturity_concentration_24m(session, iid, as_of, wall=wall)
        reset_cost = (
            _dec(reset["total_incremental_annual_interest"])
            if reset.get("total_incremental_annual_interest") is not None
            else _ZERO
        )
        share = _dec(conc["share"])
        before_c = (
            _dec(coverage["coverage_before"])
            if coverage.get("coverage_before") is not None
            else None
        )
        after_c = (
            _dec(coverage["coverage_after"]) if coverage.get("coverage_after") is not None else None
        )
        coverage_drop = (
            max(_ZERO, before_c - after_c)
            if before_c is not None and after_c is not None
            else _ZERO
        )
        rows.append(
            {
                "issuer_id": iid,
                "issuer_key": issuer.key,
                "issuer_name": issuer.name,
                "rate_reset_cost": reset_cost,
                "maturity_concentration_24m": share,
                "coverage_drop": coverage_drop,
                "coverage_before": str(before_c) if before_c is not None else None,
                "coverage_after": str(after_c) if after_c is not None else None,
            }
        )

    reset_norm = _normalize([r["rate_reset_cost"] for r in rows])
    conc_norm = _normalize([r["maturity_concentration_24m"] for r in rows])
    cov_norm = _normalize([r["coverage_drop"] for r in rows])
    w_reset = PRESSURE_WEIGHTS["rate_reset_cost"]
    w_conc = PRESSURE_WEIGHTS["maturity_concentration_24m"]
    w_cov = PRESSURE_WEIGHTS["coverage_impact"]

    ranked: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        score = w_reset * reset_norm[i] + w_conc * conc_norm[i] + w_cov * cov_norm[i]
        ranked.append(
            {
                "issuer_id": row["issuer_id"],
                "issuer_key": row["issuer_key"],
                "issuer_name": row["issuer_name"],
                "score": str(score.quantize(Decimal("0.0001"))),
                "components": {
                    "rate_reset_cost": str(row["rate_reset_cost"]),
                    "maturity_concentration_24m": str(row["maturity_concentration_24m"]),
                    "coverage_drop": str(row["coverage_drop"]),
                    "coverage_before": row["coverage_before"],
                    "coverage_after": row["coverage_after"],
                },
            }
        )
    ranked.sort(key=lambda r: Decimal(r["score"]), reverse=True)
    for i, row in enumerate(ranked, start=1):
        row["rank"] = i
    return {
        "as_of": as_of.isoformat(),
        "tenor_years": tenor_years,
        "shock_bps": shock_bps,
        "weights": {k: str(v) for k, v in PRESSURE_WEIGHTS.items()},
        "issuers": ranked,
    }


def refinancing_panel(
    session: Session,
    issuer_id: str,
    as_of: date,
    *,
    shock_bps: int = 0,
    tenor_years: int = DEFAULT_TENOR_YEARS,
) -> dict[str, Any]:
    """Base + shocked panel payload for the issuer page."""
    tenor_years = normalize_tenor_years(tenor_years)
    shock_bps = clamp_shock_bps(shock_bps)
    base = calculate_rate_reset_cost(
        session, issuer_id, as_of, shock_bps=0, tenor_years=tenor_years
    )
    shocked = calculate_rate_reset_cost(
        session, issuer_id, as_of, shock_bps=shock_bps, tenor_years=tenor_years
    )
    base_cov = calculate_proforma_coverage(
        session, issuer_id, as_of, shock_bps=0, tenor_years=tenor_years, rate_reset=base
    )
    shock_cov = calculate_proforma_coverage(
        session,
        issuer_id,
        as_of,
        shock_bps=shock_bps,
        tenor_years=tenor_years,
        rate_reset=shocked,
    )
    ranking = rank_refinancing_pressure(session, as_of, shock_bps=0, tenor_years=tenor_years)
    rank_row = next(
        (r for r in ranking.get("issuers") or [] if r.get("issuer_id") == issuer_id),
        None,
    )
    base_total = base.get("total_incremental_annual_interest")
    shocked_total = shocked.get("total_incremental_annual_interest")
    delta = None
    if base_total is not None and shocked_total is not None:
        delta = str(_dec(shocked_total) - _dec(base_total))
    return {
        "issuer_id": issuer_id,
        "as_of": as_of.isoformat(),
        "tenor_years": tenor_years,
        "tenor_choices": list(TENOR_YEARS),
        "shock_bps": shock_bps,
        "shock_bps_min": SHOCK_BPS_MIN,
        "shock_bps_max": SHOCK_BPS_MAX,
        "base": base,
        "shocked": shocked,
        "delta_incremental_annual_interest": delta,
        "coverage_base": base_cov,
        "coverage_shocked": shock_cov,
        "pressure_rank": int(rank_row["rank"]) if rank_row else None,
        "pressure_score": rank_row["score"] if rank_row else None,
        "weights": ranking.get("weights") or {},
        "footnote": FOOTNOTE,
        "base_scenario_line": BASE_SCENARIO_LINE if shock_bps == 0 else None,
    }
