"""Agent tools with in-tool checks (CONTEXT.md §9.3)."""

from app.agent.tools.alert import submit_alert
from app.agent.tools.changes import propose_change
from app.agent.tools.checklist import set_checklist
from app.agent.tools.debt import get_issuer_debt
from app.agent.tools.documents import list_documents, read_document, search_document
from app.agent.tools.facts import record_fact
from app.agent.tools.input_request import request_input
from app.agent.tools.metrics import compute_metrics

__all__ = [
    "compute_metrics",
    "get_issuer_debt",
    "list_documents",
    "propose_change",
    "read_document",
    "record_fact",
    "request_input",
    "search_document",
    "set_checklist",
    "submit_alert",
]
