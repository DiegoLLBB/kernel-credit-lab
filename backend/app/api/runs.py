"""Read-only access to research runs and their model-call trace."""

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ModelCall, ResearchRun

router = APIRouter(prefix="/api", tags=["runs"])
DbSession = Annotated[Session, Depends(get_session)]


class ModelCallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    provider: str
    model: str
    task: str | None
    status: str
    attempts: int
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: Decimal | None
    latency_ms: int | None


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    question: str
    workflow: str | None
    mode: str
    status: str
    state: str
    total_cost_usd: Decimal
    total_latency_ms: int


@router.get("/runs", response_model=list[RunOut])
def list_runs(session: DbSession, limit: int = 20) -> list[ResearchRun]:
    stmt = select(ResearchRun).order_by(ResearchRun.created_at.desc()).limit(min(limit, 100))
    return list(session.scalars(stmt))


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(run_id: str, session: DbSession) -> ResearchRun:
    run = session.get(ResearchRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/model-calls", response_model=list[ModelCallOut])
def recent_model_calls(session: DbSession, limit: int = 50) -> list[ModelCall]:
    stmt = select(ModelCall).order_by(ModelCall.created_at.desc()).limit(min(limit, 200))
    return list(session.scalars(stmt))
