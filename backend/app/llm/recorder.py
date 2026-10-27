"""Persists every model call to the model_call table."""

import logging

from app.db import SessionLocal
from app.llm.client import ModelCallRecord
from app.models import ModelCall

logger = logging.getLogger(__name__)


def db_recorder(record: ModelCallRecord) -> None:
    logger.info("model_call", extra={"model_call": record.__dict__})
    try:
        with SessionLocal() as session:
            session.add(ModelCall(**record.__dict__))
            session.commit()
    except Exception:  # observability must never break a research run
        logger.exception("model_call_persist_failed")
