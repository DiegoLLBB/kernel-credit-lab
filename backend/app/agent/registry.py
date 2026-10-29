"""Tool definitions for the Meta ``/responses`` agent loop.

Each entry is a strict JSON schema (additionalProperties: false, every field required)
plus a short description written for the model.
"""

from __future__ import annotations

import traceback
from typing import Any

from sqlalchemy.orm import Session

from app.agent.audit import audit, bump_refusal, record_tool_call
from app.agent.tools import (
    compute_metrics,
    get_issuer_debt,
    list_documents,
    propose_change,
    read_document,
    record_fact,
    request_input,
    search_document,
    set_checklist,
    submit_alert,
)
from app.agent.tools.base import current_step_id
from app.models.trace import AgentRun

# Nullable string for optional fields under strict mode.
_NULLABLE_STRING: dict[str, Any] = {"anyOf": [{"type": "string"}, {"type": "null"}]}
_NULLABLE_INT: dict[str, Any] = {"anyOf": [{"type": "integer"}, {"type": "null"}]}


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties.keys()),
        "additionalProperties": False,
    }


def _tool(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "strict": True,
        "parameters": parameters,
    }


CHECKLIST_ITEM = _object(
    {
        "key": {"type": "string", "description": "Short stable key (e.g. instrument, amount)"},
        "question": {"type": "string", "description": "What must be settled"},
        "evidence_needed": {"type": "string", "description": "What evidence would settle it"},
    }
)

FINDING = _object(
    {
        "item_key": {"type": "string"},
        "statement": {"type": "string", "description": "Finding text; numbers must be evidenced"},
        "citations": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Fact IDs recorded earlier in this run",
        },
    }
)

CHANGE = _object(
    {
        "action": {
            "type": "string",
            "description": (
                "mark_redeemed | mark_repaid | mark_matured | reduce_outstanding | "
                "add_instrument | update_amount"
            ),
        },
        "instrument_key": _NULLABLE_STRING,
        "instrument_id": _NULLABLE_STRING,
        "effective_date": {"type": "string", "description": "ISO date YYYY-MM-DD"},
        "evidence": _object(
            {
                "instrument": _NULLABLE_STRING,
                "effective_date": _NULLABLE_STRING,
                "amount": _NULLABLE_STRING,
                "amount_scale": {
                    **_NULLABLE_STRING,
                    "description": (
                        "Fact whose quote states table units ('in millions' / 'in billions'); "
                        "amount check then accepts value÷scale in the amount quote"
                    ),
                },
                "name": _NULLABLE_STRING,
                "maturity": _NULLABLE_STRING,
                "coupon": _NULLABLE_STRING,
                "issue_date": _NULLABLE_STRING,
                "amount_outstanding": _NULLABLE_STRING,
            }
        ),
        "amount": _NULLABLE_STRING,
        "name": _NULLABLE_STRING,
        "coupon": _NULLABLE_STRING,
        "maturity": {
            **_NULLABLE_STRING,
            "description": "ISO date YYYY-MM-DD, or YYYY-MM when the document gives no day",
        },
        "issue_date": _NULLABLE_STRING,
        "amount_outstanding": _NULLABLE_STRING,
        "currency": _NULLABLE_STRING,
        "instrument_type": _NULLABLE_STRING,
        "rate_type": _NULLABLE_STRING,
        "issuer_id": _NULLABLE_STRING,
    }
)

TOOLS: list[dict[str, Any]] = [
    _tool(
        "get_issuer_debt",
        (
            "Kernel's debt book for this issuer and its group as of the event date: "
            "instruments (key, coupon, maturity, amount, status) and maturity-wall "
            "bucket summary. Call early; cite instrument keys from this list."
        ),
        _object({}),
    ),
    _tool(
        "set_checklist",
        "Declare the event kind and checklist items this run must settle. Call once early.",
        _object(
            {
                "kind": {
                    "type": "string",
                    "description": "Event kind (redemption, tender_results, issuance, …)",
                },
                "items": {
                    "type": "array",
                    "items": CHECKLIST_ITEM,
                    "description": "Checklist items including counter_evidence",
                },
            }
        ),
    ),
    _tool(
        "list_documents",
        "List filings attached to this event (id, title, form, page count). No arguments.",
        _object({}),
    ),
    _tool(
        "read_document",
        (
            "Read one page of an event filing (at most 8000 characters). "
            "Page text is cached; quote checks require a prior read."
        ),
        _object(
            {
                "filing_id": {"type": "string"},
                "page": {
                    **_NULLABLE_INT,
                    "description": "0-based page index; null reads page 0",
                },
            }
        ),
    ),
    _tool(
        "search_document",
        (
            "Case-insensitive search over a filing's stored text. Returns up to 10 hits "
            "with page number and a ±200-character snippet. Searching does not count as "
            "reading — call read_document on a page before quoting from it."
        ),
        _object(
            {
                "filing_id": {"type": "string"},
                "query": {
                    "type": "string",
                    "description": "Substring to find (case-insensitive)",
                },
            }
        ),
    ),
    _tool(
        "record_fact",
        "Record one exact quote from a read document for one checklist item, with a short claim.",
        _object(
            {
                "item_key": {"type": "string"},
                "filing_id": {"type": "string"},
                "quote": {
                    "type": "string",
                    "description": "Verbatim substring of the page text (copy word for word)",
                },
                "claim": {"type": "string", "description": "What the quote means for credit"},
            }
        ),
    ),
    _tool(
        "propose_change",
        "Propose a structured change to Kernel's instrument data, citing recorded facts.",
        _object({"change": CHANGE}),
    ),
    _tool(
        "compute_metrics",
        (
            "Compute before/after maturity-wall metrics (changed buckets and 2026–2029 "
            "totals). Pure code; call before submit_alert."
        ),
        _object({}),
    ),
    _tool(
        "request_input",
        (
            "Ask a person for missing evidence on one unproven item. Allowed after every "
            "page is read, or on a filing with more than 20 pages after at least 3 "
            "searches that returned nothing new."
        ),
        _object(
            {
                "item_key": {"type": "string"},
                "question": {"type": "string"},
            }
        ),
    ),
    _tool(
        "submit_alert",
        "File the finding: summary, findings with citations, materiality. Ends the run.",
        _object(
            {
                "summary": {"type": "string"},
                "findings": {"type": "array", "items": FINDING},
                "material": {"type": "boolean"},
                "judgement_reason": {
                    **_NULLABLE_STRING,
                    "description": "Required only to upgrade materiality when code rules say no",
                },
            }
        ),
    ),
]

TOOL_NAMES: frozenset[str] = frozenset(t["name"] for t in TOOLS)


async def _dispatch_tool_body(
    session: Session,
    run: AgentRun,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    if name == "get_issuer_debt":
        return get_issuer_debt(session, run)
    if name == "set_checklist":
        return set_checklist(session, run, arguments["kind"], arguments["items"])
    if name == "list_documents":
        return list_documents(session, run)
    if name == "read_document":
        return read_document(
            session,
            run,
            arguments["filing_id"],
            arguments.get("page"),
        )
    if name == "search_document":
        return search_document(
            session,
            run,
            arguments["filing_id"],
            arguments["query"],
        )
    if name == "record_fact":
        return record_fact(
            session,
            run,
            arguments["item_key"],
            arguments["filing_id"],
            arguments["quote"],
            arguments["claim"],
        )
    if name == "propose_change":
        change = dict(arguments.get("change") or {})
        evidence = change.get("evidence")
        if isinstance(evidence, dict):
            change["evidence"] = {k: v for k, v in evidence.items() if v is not None}
        change = {k: v for k, v in change.items() if v is not None or k == "evidence"}
        return propose_change(session, run, change)
    if name == "compute_metrics":
        return compute_metrics(session, run)
    if name == "request_input":
        return request_input(session, run, arguments["item_key"], arguments["question"])
    if name == "submit_alert":
        judgement = arguments.get("judgement_reason")
        return await submit_alert(
            session,
            run,
            arguments["summary"],
            arguments["findings"],
            bool(arguments["material"]),
            None if judgement is None else str(judgement),
        )
    return {
        "ok": False,
        "refused": True,
        "reason": f"unknown tool {name!r}",
        "problems": [f"unknown tool {name!r}"],
    }


async def dispatch_tool(
    session: Session,
    run: AgentRun,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Execute one registered tool; never raises — exceptions become refusals."""
    try:
        return await _dispatch_tool_body(session, run, name, arguments)
    except Exception as exc:
        tb = traceback.format_exc()
        message = f"internal error in {name}: {exc}"
        run.turn_count = int(run.turn_count or 0) + 1
        count = bump_refusal(run, name)
        payload = {
            "ok": False,
            "refused": True,
            "error": message,
            "reason": message,
            "problems": [message],
            "refusal_count": count,
        }
        record_tool_call(
            session,
            run,
            tool_name=name,
            arguments=arguments,
            result={**payload, "traceback": tb},
            status="error",
            error=message,
            step_id=current_step_id.get(),
        )
        audit(
            session,
            run,
            actor="agent",
            action=f"tool:{name}:error",
            detail={"error": message, "traceback": tb, "refusal_count": count},
        )
        session.flush()
        return payload
