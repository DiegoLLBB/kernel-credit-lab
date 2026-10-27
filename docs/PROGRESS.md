# Progress Log

Dated record of what was built, decided and tested during the Meta Global AI Developer
Hackathon (building period 27 Oct – 4 Nov 2026). The plan itself lives in
[CONTEXT.md](CONTEXT.md); commit history is the authoritative record of the code.

---

## Day 1 — Tue 27 Oct 2026

### Built
- Fresh public repository; one-command stack with Docker Compose: FastAPI API, ARQ worker,
  Postgres 16 + pgvector, Redis, Next.js web.
- GitHub Actions CI: ruff lint/format, Alembic migrations + `alembic check`, pytest (unit +
  integration against Postgres/Redis), web lint/typecheck/build.
- Run-trace data model from the first migration: `research_run`, `agent_step`,
  `tool_call`, `model_call`.
- Unified model client (`app/llm/client.py`) for the Meta Model API and Baseten: retries only
  transient errors (with Retry-After), one repair attempt on invalid JSON, strict-schema
  conversion, a `ModelCall` record per call (tokens, cost, latency, status).
- SAM 3.1 client (`app/llm/sam.py`) via the streamed Responses API, parsing box/mask tokens.
- Verifiable chart reader (`app/tools/charts.py`): SAM locates charts and bars, Spark reads
  values, Python checks values against bar geometry.
- Smoke-test script (`scripts/smoke_models.py`) and chart demo (`scripts/chart_demo.py`).
- Structured JSON logging with request/run/step IDs; liveness and readiness endpoints;
  status page in the web app.

### Tested
| Test | Result |
|---|---|
| Model list (`GET /v1/models`) | 9 models: `muse-spark-1.3`, `muse-wonder-1.0`, `sam-3.1`, `muse-voice-transcribe-1.0`, `muse-image-1.0`, older Spark versions |
| Spark chat + strict `json_schema` | ✅ Bond-terms extraction correct incl. par-call date and make-whole spread |
| Wonder chat + strict `json_schema` | ✅ Same extraction correct |
| SAM on prospectus-style page ("table", "text", "paragraph", "chart", …) | ❌ No detections ("page" = whole image only) |
| SAM on Duke Energy Q2 2026 EPS slide ("bar chart", "bar") | ✅ Both charts and all 8 bars located |
| Chart reader end to end on the same slide | ✅ 8/8 values read correctly, all within 1.3% of geometry → VERIFIED |
| Backend test suite | 24 passing |
| CI | Green from the second commit (first run failed on a lint error, fixed) |

### Decisions
1. **Meta API settings confirmed:** base URL `https://api.meta.ai/v1`; Spark and Wonder via
   chat completions; SAM 3.1 only via `/v1/responses`.
2. **Strict structured outputs:** the API requires `additionalProperties: false` and all
   fields required; the client converts Pydantic schemas automatically.
3. **SAM 3.1 role changed:** not used for document tables or text (it does not detect them).
   Tables come from parsed filing HTML (pdfplumber for PDFs). SAM is used for **charts** in
   issuer presentations, where it works well.
4. **Verifiable chart reading adopted** as a distinctive Meta-tooling feature: geometry
   checks Spark's reading of each chart, so a misread value cannot pass silently. Target use:
   debt maturity profile charts, cross-checked against the filing-based maturity wall.
5. **Track:** Startup. **Open weights:** Muse Spark Open via Baseten for bulk processing and
   independent claim verification (credits applied for).

### Open items
- Test the chart reader on real **debt maturity profile** charts (denser, sometimes stacked).
- Baseten credits → smoke-test `meta-models/muse-spark-open-1026`.
- Issuer shortlist (8–10 IG issuers, anchored on Duke Energy holdco/opco structure).

### Next (Day 2 — Wed 28 Oct)
Full domain schema and migrations (issuers, bonds, call terms, filings, chunks, regions,
chart readings, curated sources); issuer shortlist; hand-curated seed for ~25 bonds with
provenance; seed loader with validation. Agents 102 (Verifiable Agents) at 17:00 Madrid.
