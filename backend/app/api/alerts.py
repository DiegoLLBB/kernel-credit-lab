"""Alerts feed and analyst review."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.audit import audit
from app.agent.normalize import find_quote_offset
from app.agent.text import PAGE_CHARS
from app.api.common import (
    HIDDEN_RUN_STATUSES,
    bucket_deltas,
    clamp_limit,
    fact_page_index,
)
from app.api.schemas import (
    AlertDetailOut,
    AlertListItem,
    AlertListOut,
    AlertReviewIn,
    AlertReviewOut,
    AppliedChangeOut,
    CitedFactOut,
    ClaimsCheckOut,
    FindingOut,
    IssuerRef,
    WallBucketDelta,
)
from app.db import get_session
from app.models import Alert, Event, Fact, Filing, Issuer, StateChange
from app.models.credit import CreditInsight
from app.models.trace import AgentRun

router = APIRouter(prefix="/api/alerts", tags=["alerts"])
DbSession = Annotated[Session, Depends(get_session)]


def _issuer_ref(issuer: Issuer) -> IssuerRef:
    return IssuerRef(id=issuer.id, key=issuer.key, name=issuer.name)


def _reasons(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    return [str(raw)]


def _alert_visible(
    session: Session,
    alert: Alert,
    *,
    include_superseded: bool,
) -> bool:
    if include_superseded or not alert.run_id:
        return True
    run = session.get(AgentRun, alert.run_id)
    if run is None:
        return True
    return (run.status or "") not in HIDDEN_RUN_STATUSES


def impact_for_alert(session: Session, alert: Alert) -> tuple[str | None, bool, str | None]:
    """(impact sentence, pending, status) for an alert from checked insight text."""
    row = session.scalar(
        select(CreditInsight).where(CreditInsight.scope == "impact", CreditInsight.ref == alert.id)
    )
    if row is None:
        return None, True, None
    sentences = row.sentences or []
    return (sentences[0] if sentences else None, False, row.status)


def _list_item(session: Session, alert: Alert) -> AlertListItem:
    issuer = session.get(Issuer, alert.issuer_id)
    if issuer is None:
        raise HTTPException(status_code=500, detail="alert issuer missing")
    event = session.get(Event, alert.event_id) if alert.event_id else None
    sentence, _, impact_status = impact_for_alert(session, alert)
    return AlertListItem(
        id=alert.id,
        issuer=_issuer_ref(issuer),
        event_date=event.event_date if event else None,
        event_id=alert.event_id,
        title=alert.title,
        summary=alert.summary,
        material=True,
        materiality_reasons=_reasons(alert.materiality_reasons),
        status=alert.status,  # type: ignore[arg-type]
        run_id=alert.run_id,
        created_at=alert.created_at,
        impact_sentence=sentence,
        impact_status=impact_status,
    )


def _cited_fact(session: Session, fact_id: str) -> CitedFactOut | None:
    fact = session.get(Fact, fact_id)
    if fact is None:
        return None
    filing: Filing | None = None
    page: int | None = None
    if fact.filing_id:
        filing = session.get(Filing, fact.filing_id)
        if filing is not None:
            page = fact_page_index(filing, fact)
            if page is None and fact.exact_quote and filing.body_text:
                off = find_quote_offset(filing.body_text, fact.exact_quote)
                if off is not None:
                    page = off // PAGE_CHARS
    return CitedFactOut(
        id=fact.id,
        exact_quote=fact.exact_quote,
        claim=fact.claim,
        location=fact.location,
        document_title=filing.title if filing else None,
        document_url=filing.url if filing else None,
        page=page,
        filing_id=fact.filing_id,
    )


def _findings_out(session: Session, findings: list[Any] | None) -> list[FindingOut]:
    out: list[FindingOut] = []
    for raw in findings or []:
        if not isinstance(raw, dict):
            continue
        cites: list[CitedFactOut] = []
        for cid in raw.get("citations") or raw.get("fact_ids") or []:
            cited = _cited_fact(session, str(cid))
            if cited is not None:
                cites.append(cited)
        out.append(
            FindingOut(
                item_key=raw.get("item_key") or raw.get("item"),
                statement=str(raw.get("statement") or raw.get("text") or ""),
                citations=cites,
            )
        )
    return out


def _applied_changes(session: Session, run_id: str | None) -> list[AppliedChangeOut]:
    if not run_id:
        return []
    rows = session.scalars(
        select(StateChange)
        .where(StateChange.run_id == run_id, StateChange.status == "applied")
        .order_by(StateChange.created_at.asc())
    ).all()
    out: list[AppliedChangeOut] = []
    for row in rows:
        diff = row.proposed_diff or {}
        out.append(
            AppliedChangeOut(
                id=row.id,
                action=diff.get("action"),
                instrument_key=diff.get("instrument_key"),
                field=diff.get("field"),
                before=diff.get("before"),
                after=diff.get("after"),
                status=row.status,
                effective_date=row.effective_date,
                proposed_diff=dict(diff),
            )
        )
    return out


def _claims_out(raw: dict[str, Any] | None) -> ClaimsCheckOut | None:
    if not raw:
        return None
    problems = raw.get("problems") or []
    if not isinstance(problems, list):
        problems = [str(problems)]
    return ClaimsCheckOut(
        status=raw.get("status"),
        problems=[str(p) for p in problems],
        summary=raw.get("summary") if isinstance(raw.get("summary"), dict) else None,
        findings=list(raw.get("findings") or []) if isinstance(raw.get("findings"), list) else [],
        provider=raw.get("provider"),
        model=raw.get("model"),
    )


@router.get("", response_model=AlertListOut)
def list_alerts(
    session: DbSession,
    status: Literal["new", "confirmed", "dismissed"] | None = None,
    issuer: str | None = Query(None, description="Issuer key (e.g. verizon)"),
    material: bool | None = None,
    include_superseded: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> AlertListOut:
    limit = clamp_limit(limit)
    # Alerts table only stores material findings.
    if material is False:
        return AlertListOut(items=[], total=0, limit=limit, offset=offset)

    stmt = select(Alert).outerjoin(AgentRun, Alert.run_id == AgentRun.id)
    if status is not None:
        stmt = stmt.where(Alert.status == status)
    if issuer is not None:
        iss = session.scalars(select(Issuer).where(Issuer.key == issuer)).first()
        if iss is None:
            return AlertListOut(items=[], total=0, limit=limit, offset=offset)
        stmt = stmt.where(Alert.issuer_id == iss.id)
    if not include_superseded:
        stmt = stmt.where(
            (Alert.run_id.is_(None)) | (AgentRun.status.not_in(sorted(HIDDEN_RUN_STATUSES)))
        )

    count_stmt = select(func.count()).select_from(stmt.with_only_columns(Alert.id).subquery())
    total = session.scalar(count_stmt) or 0
    rows = session.scalars(stmt.order_by(Alert.created_at.desc()).offset(offset).limit(limit)).all()
    items = [_list_item(session, a) for a in rows]
    return AlertListOut(items=items, total=int(total), limit=limit, offset=offset)


@router.get("/{alert_id}", response_model=AlertDetailOut)
def get_alert(
    alert_id: str,
    session: DbSession,
    include_superseded: bool = False,
) -> AlertDetailOut:
    alert = session.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    if not _alert_visible(session, alert, include_superseded=include_superseded):
        raise HTTPException(status_code=404, detail="Alert not found")

    issuer = session.get(Issuer, alert.issuer_id)
    if issuer is None:
        raise HTTPException(status_code=500, detail="alert issuer missing")
    event = session.get(Event, alert.event_id) if alert.event_id else None

    before = alert.before_metrics
    after = alert.after_metrics
    # Prefer nested issuer walls when present.
    before_issuer = (before or {}).get("issuer") if isinstance(before, dict) else None
    after_issuer = (after or {}).get("issuer") if isinstance(after, dict) else None
    wall_before = before_issuer if isinstance(before_issuer, dict) else before
    wall_after = after_issuer if isinstance(after_issuer, dict) else after
    deltas = [WallBucketDelta(**d) for d in bucket_deltas(wall_before, wall_after)]
    sentence, _, impact_status = impact_for_alert(session, alert)

    return AlertDetailOut(
        id=alert.id,
        issuer=_issuer_ref(issuer),
        event_id=alert.event_id,
        event_date=event.event_date if event else None,
        title=alert.title,
        summary=alert.summary,
        material=True,
        materiality_reasons=_reasons(alert.materiality_reasons),
        status=alert.status,  # type: ignore[arg-type]
        run_id=alert.run_id,
        findings=_findings_out(session, alert.findings),
        applied_changes=_applied_changes(session, alert.run_id),
        impact_sentence=sentence,
        impact_status=impact_status,
        affected_buckets=deltas,
        before_wall=wall_before if isinstance(wall_before, dict) else None,
        after_wall=wall_after if isinstance(wall_after, dict) else None,
        claims_check=_claims_out(
            alert.claims_check if isinstance(alert.claims_check, dict) else None
        ),
        created_at=alert.created_at,
    )


@router.post("/{alert_id}/review", response_model=AlertReviewOut)
def review_alert(alert_id: str, body: AlertReviewIn, session: DbSession) -> AlertReviewOut:
    alert = session.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    if not _alert_visible(session, alert, include_superseded=False):
        raise HTTPException(status_code=404, detail="Alert not found")

    new_status: Literal["confirmed", "dismissed"] = (
        "confirmed" if body.action == "confirm" else "dismissed"
    )
    alert.status = new_status
    if not alert.run_id:
        raise HTTPException(status_code=400, detail="alert has no run_id for audit")
    run = session.get(AgentRun, alert.run_id)
    if run is None:
        raise HTTPException(status_code=400, detail="alert run missing")
    audit(
        session,
        run,
        actor="analyst",
        action=f"alert_{new_status}",
        detail={
            "alert_id": alert.id,
            "action": body.action,
            "note": body.note,
            "previous_status": "new",
        },
    )
    session.commit()
    return AlertReviewOut(id=alert.id, status=new_status, note=body.note)
