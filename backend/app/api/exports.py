"""CSV and RSS exports for the credit dashboard."""

from __future__ import annotations

import csv
import io
from datetime import date
from email.utils import format_datetime
from typing import Annotated, Any
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.alerts import _list_item
from app.api.common import HIDDEN_RUN_STATUSES
from app.api.credit import METRIC_LABELS, metric_tiles
from app.db import get_session
from app.models import Alert, Issuer
from app.models.trace import AgentRun

router = APIRouter(tags=["exports"])
DbSession = Annotated[Session, Depends(get_session)]

PUBLIC_SITE = "https://kernel-credit-lab.web.app"

_WATCHLIST_METRICS = (
    "total_debt",
    "due_24m",
    "liquidity_coverage_total",
    "liquidity_coverage",
    "leverage",
    "interest_coverage",
)


def _visible_alerts(session: Session) -> list[Alert]:
    stmt = (
        select(Alert)
        .outerjoin(AgentRun, Alert.run_id == AgentRun.id)
        .where((Alert.run_id.is_(None)) | (AgentRun.status.not_in(sorted(HIDDEN_RUN_STATUSES))))
        .order_by(Alert.created_at.desc())
    )
    return list(session.scalars(stmt).all())


@router.get("/api/alerts.csv")
def alerts_csv(session: DbSession) -> Response:
    """Same columns as the alerts feed; numbers in plain units; source filing URL."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "id",
            "issuer_key",
            "issuer_name",
            "event_date",
            "title",
            "impact_sentence",
            "status",
            "kind",
            "live",
            "replay_verdict",
            "filing_url",
            "created_at",
        ]
    )
    for alert in _visible_alerts(session):
        item = _list_item(session, alert)
        writer.writerow(
            [
                item.id,
                item.issuer.key or "",
                item.issuer.name,
                item.event_date.isoformat() if item.event_date else "",
                item.title,
                item.impact_sentence or "",
                item.status,
                item.kind,
                "1" if item.live else "0",
                item.replay_verdict or "",
                item.filing_url or "",
                item.created_at.isoformat() if item.created_at else "",
            ]
        )
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="kernel-alerts.csv"'},
    )


@router.get("/api/alerts.rss")
def alerts_rss(session: DbSession, request: Request) -> Response:
    """One RSS item per alert; title = impact sentence; link = alert page."""
    base = str(request.base_url).rstrip("/")
    # Prefer the public Firebase host when the request is the API origin.
    site = PUBLIC_SITE if "run.app" in base or "localhost" in base else base
    items_xml: list[str] = []
    for alert in _visible_alerts(session)[:100]:
        item = _list_item(session, alert)
        title = (item.impact_sentence or item.title or "Alert").strip()
        link = f"{site}/alerts/{item.id}"
        pub = ""
        if item.event_date:
            pub = item.event_date.isoformat()
        elif item.created_at is not None:
            pub = format_datetime(item.created_at)
        desc = escape(item.summary or title)
        items_xml.append(
            "\n".join(
                [
                    "<item>",
                    f"<title>{escape(title)}</title>",
                    f"<link>{escape(link)}</link>",
                    f'<guid isPermaLink="true">{escape(link)}</guid>',
                    f"<description>{desc}</description>",
                    f"<pubDate>{escape(pub)}</pubDate>" if pub else "",
                    "</item>",
                ]
            )
        )
    body = "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<rss version="2.0">',
            "<channel>",
            "<title>Kernel Credit Lab — alerts</title>",
            f"<link>{escape(site)}</link>",
            "<description>Material credit alerts with checked impact sentences.</description>",
            *items_xml,
            "</channel>",
            "</rss>",
        ]
    )
    return Response(content=body + "\n", media_type="application/rss+xml; charset=utf-8")


@router.get("/api/watchlist.csv")
def watchlist_csv(session: DbSession) -> Response:
    """Watchlist metric tiles as CSV; plain numeric units; source tag per metric."""
    point = date.today()
    roots = session.scalars(
        select(Issuer).where(Issuer.parent_id.is_(None)).order_by(Issuer.key.asc())
    ).all()
    buf = io.StringIO()
    writer = csv.writer(buf)
    header = ["issuer_key", "issuer_name", "ticker", "sector"]
    for metric in _WATCHLIST_METRICS:
        label = METRIC_LABELS.get(metric, metric)
        header.extend(
            [
                f"{metric}_value",
                f"{metric}_label",
                f"{metric}_unit",
                f"{metric}_period_end",
                f"{metric}_source_tag",
                f"{metric}_missing_reason",
                f"{metric}_flag",
            ]
        )
        # Keep label once for humans
        _ = label
    writer.writerow(header)
    for iss in roots:
        tiles = {
            t.metric: t
            for t in metric_tiles(
                session,
                iss,
                point,
                only=_WATCHLIST_METRICS,
                include_peers=False,
                include_sparkline=False,
            )
        }
        row: list[Any] = [iss.key or "", iss.name, iss.ticker or "", iss.sector or ""]
        for metric in _WATCHLIST_METRICS:
            t = tiles.get(metric)
            row.extend(
                [
                    "" if t is None or t.value is None else str(t.value),
                    METRIC_LABELS.get(metric, metric),
                    t.unit if t else "",
                    t.period_end.isoformat() if t and t.period_end else "",
                    (t.source_tag or "") if t else "",
                    (t.missing_reason or "") if t else "no data yet",
                    (t.flag or "") if t else "",
                ]
            )
        writer.writerow(row)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="kernel-watchlist.csv"'},
    )
