"""Document page read with optional fact highlight."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.common import document_page_payload
from app.api.schemas import DocumentPageOut
from app.db import get_session
from app.models import Fact, Filing

router = APIRouter(prefix="/api/documents", tags=["documents"])
DbSession = Annotated[Session, Depends(get_session)]


@router.get("/{filing_id}", response_model=DocumentPageOut)
def get_document_page(
    filing_id: str,
    session: DbSession,
    page: int = Query(0, ge=0),
    fact_id: str | None = None,
) -> DocumentPageOut:
    filing = session.get(Filing, filing_id)
    if filing is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if not filing.body_text:
        raise HTTPException(status_code=404, detail="Document has no stored text")

    fact: Fact | None = None
    if fact_id is not None:
        fact = session.get(Fact, fact_id)
        if fact is None:
            raise HTTPException(status_code=404, detail="Fact not found")
        if fact.filing_id and fact.filing_id != filing_id:
            raise HTTPException(status_code=400, detail="fact_id is not from this filing")

    payload = document_page_payload(filing, page=page, fact=fact)
    return DocumentPageOut(**payload)
