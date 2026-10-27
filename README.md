# Kernel Credit Lab

**Kernel turns fragmented fixed-income research into an auditable, tool-using AI research analyst.**

[![CI](https://github.com/DiegoLLBB/kernel-credit-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/DiegoLLBB/kernel-credit-lab/actions/workflows/ci.yml)

Kernel is an agentic research workbench for USD investment-grade corporate bonds. It turns SEC
filings, bond prospectuses and macro data into a structured, cited knowledge base, then runs a
verifiable research loop: **plan → gather → compute → verify → critique → revise → memo**.

> **Not basic RAG.** Postgres is the system of record, every number comes from a deterministic
> tool call, every claim is checked by an independent model, and the full trace (model, tokens,
> cost, latency, citations) is visible for every run.

Live demo: _coming soon_ · Demo video: _coming soon_

## Meta models

| Role | Model |
|---|---|
| Extraction, planning, synthesis | Muse Spark (Meta Model API) |
| Deep research runs | Muse Wonder (Meta Model API) |
| Table and region detection in prospectuses | SAM 3.1 (Meta Model API) |
| Bulk document processing and independent claim verification | Muse Spark Open (open weights, via Baseten) |

## Architecture

```
Next.js workbench ──► FastAPI ──► Agent orchestrator (state machine)
                         │              │
                         │              ├─► Deterministic tools (Python)
                         │              └─► LLMClient ─► Meta Model API / Baseten
                         ▼
                Postgres + pgvector  ◄── ARQ worker (ingestion, runs) ◄── Redis
```

Every research run, agent step, tool call and model call is persisted
(`research_run`, `agent_step`, `tool_call`, `model_call`).

## Quick start

```bash
cp .env.example .env      # add your API keys
make up                   # or: docker compose up --build -d
make smoke                # confirm model IDs and structured output
make test
```

- Web: http://localhost:3000
- API: http://localhost:8000 (docs at `/docs`, readiness at `/health/ready`)

## Repository layout

```
backend/   FastAPI app, agent, tools, LLM client, Alembic migrations, tests
web/       Next.js research workbench
.github/   CI (lint, migrations, tests, web build)
```

## Built during the hackathon

This repository was created on **27 October 2026** for the Meta Global AI Developer Hackathon.
All code in it was written during the event (27 Oct – 4 Nov 2026); see the commit history.

## Data sources and limitations

SEC EDGAR (filings, XBRL company facts) and FRED (macro series), used within their access
policies. Bond reference data is hand-curated from issuer filings, with provenance recorded per
field. No licensed ratings or pricing data.

**Kernel is research support, not investment advice.**
