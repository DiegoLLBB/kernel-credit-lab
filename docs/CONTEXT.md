# Kernel Credit Lab — Product, Hackathon & Build Context

*Living document — the single source of truth for the project. Last updated 27 Oct 2026 (end of day 1). Supersedes the v1 and v2 PDFs. Dated changes and decisions are logged in [PROGRESS.md](PROGRESS.md).*

> **What changed from v1:** Startup track confirmed; official rules and rubric added; explicit defense against the "Basic RAG" anti-project; scope cut to a buildable size; investment-grade domain fixes (rate-reset refinancing, par-call windows, structural subordination instead of covenant comparison); Muse Spark Open promoted to a core role; Muse Voice Transcribe added as a stretch; Muse Image demoted; production readiness moved into the core build; 17 sequential phases replaced by a day-by-day schedule ending Nov 4.

---

# Part A — Product & Hackathon Context

## 1. Executive summary

Kernel Credit Lab is an agentic research workbench for USD investment-grade (IG) corporate bonds. It turns public filings, bond documentation and macro data into a structured, cited knowledge base, then runs a constrained research loop over it: plan, gather structured data, retrieve evidence, compute with deterministic tools, verify claims with an independent model, critique, revise and publish a reproducible memo.

- **Track:** Startup.
- **Problem statement:** Build for Your World (a firsthand fixed-income research problem), with a secondary Push the Frontier claim: giving an AI system the ability to conduct, verify, critique and revise credit research end to end.
- **Not basic RAG:** "Basic RAG Applications" is an explicit anti-project in the official guide. Kernel's defense is built into the product and the demo: persistent relational state, deterministic computation, independent verification, a visible critique-and-revise step, and a measured comparison against a plain-RAG baseline.

**One-sentence pitch:** Kernel turns fragmented fixed-income research into an auditable, tool-using AI research analyst.

**Core narrative:** Credit analysts lose time reconciling filings, bond terms, events and macro data across disconnected systems. Given a question such as *"Which issuers face the greatest refinancing pressure over the next 24 months?"*, Kernel plans the research, gathers evidence, computes rate-reset exposure, has an independent model check every claim, catches its own mistakes, revises, and produces a source-linked memo, with the full trace visible.

## 2. Hackathon alignment (official)

### Key facts

| Item | Detail |
|---|---|
| Event | Meta Global AI Developer Hackathon (remote) |
| Building period | Oct 27 – Nov 4, 2026 |
| Submission deadline | **Wed Nov 4, 9:00 PM ET (EST) = 03:00 Thu Nov 5, Madrid.** US clocks change Nov 1. Target submission: evening of Nov 4, Madrid time. |
| Last troubleshooting day | Tue Nov 3 |
| Judging | Nov 5–8; closing ceremony Mon Nov 9 (top 5 per track, live final round) |
| Track | **Startup** — $250K / $150K / $75K / $50K / $25K |
| Credits | $150 Meta Model API (claimed). Baseten $100 + 2M TPM (apply; reviewed, capped at 2,000). |
| Team size | Max 4; solo allowed |

### Rules that shape the build

- **Public repository** required.
- **New work only.** Everything presented must be built during the event, and judges must be able to identify it. Failure to do so means disqualification. Start a fresh repo; commit often; README section "Built during the hackathon" with commit history.
- **Demo shows only hackathon-built features.**
- **3-minute demo video** is a primary judging artifact, **together with the code and the live product.** A deployed, publicly reachable instance is required in practice, not optional.
- **Banned:** code, data or assets without rights. Applies to ratings data, paid market data and earnings-call audio.
- **Anti-projects include "Basic RAG Applications."**

### Judging

**Stage One (pass/fail):** fits a problem statement and reasonably uses the Meta Model API and/or Meta open-weight models.

**Stage Two (0–10 per category):**

| Category | Weight | Official question | Kernel's answer |
|---|---|---|---|
| Impact | 30% | Real-world potential; who benefits; could people actually use it? | A real analyst workflow (refinancing monitoring) with measured time and reproducibility gains; a credible enterprise path (self-hostable document processing). |
| Use of Meta AI Tooling | 25% | Creative, beyond basic integration, capabilities that surprise | Hosted + open-weight hybrid: SAM 3.1 locates charts in investor decks and its geometry verifies Spark's chart readings; Spark extracts and plans; Wonder investigates; Spark Open verifies claims independently. |
| Technical Depth & Execution | 20% | Sound, refined engineering; real craft | Relational source of truth, deterministic tools with tests, state machine, CI, eval report, observability. |
| Demo | 25% | Working, impressive, holds up live, cool to watch | Opens on the live agent trace; a scripted, real critique moment; live URL judges can use. |

**Stage Three:** top 5 per track; judges watch videos live and rank with the same rubric.

**Production readiness** for the startup track is not a separate box. It is scored through Technical Depth ("real craft"), Demo ("holds up live") and Impact ("could this become something people use"). It is therefore part of the core build from the first commit, not a final hardening phase.

### Secondary targets (never at the expense of the main prize)

- **Best Agentic Use-Case ($5K):** natural fit.
- **Best Build on Meta Open Weight Models ($10K):** Spark Open has a real core role (see Part B).
- **Most Viral ($20K) and Community Vote ($10K):** post a 60-second cut on X immediately after submission.
- Not targeted: Best Creative Build.

## 3. Product definition

**Name:** Kernel (Kernel Credit Lab). **Category:** agentic fixed-income research.

**Primary users:** IG credit analysts, fixed-income researchers, IG bond investors, treasury and capital-structure teams, financial intelligence platforms.

**Design user:** a credit analyst monitoring a universe of USD IG issuers for changes in refinancing cost, capital structure and credit quality.

**Problem:** bond terms live in prospectuses and 424B filings; financials in SEC filings and XBRL; events in 8-Ks and press releases; macro context in separate systems. Analysis is manual and hard to reproduce. Generic LLM tools retrieve text but do not maintain structured state or verify calculations.

**Solution:**

1. A structured issuer / bond / event knowledge base (relational source of truth).
2. An evidence-linked research interface.
3. An agent that plans and executes research inside a constrained state machine.
4. Deterministic financial tools for every number.
5. An independent verifier and a critic.
6. Reproducible, analyst-ready memos with full run traces.

## 4. What makes Kernel different (and not basic RAG)

**Basic RAG:** documents → chunks → embeddings → retrieval → LLM answer. It loses structure, cannot compute reliably, has weak provenance, does not separate evidence from inference, keeps no state, and cannot verify numbers.

**Kernel:** documents + APIs → structured extraction → validated knowledge base → hybrid retrieval → planning → deterministic tools → independent verification → critique → revision → reproducible memo.

### Five differentiators

1. **Persistent structured state.** Issuers, bonds, call terms, maturities, events, filings, metrics and research runs live in Postgres. The vector index is a retrieval aid, never the system of record.
2. **Hybrid evidence retrieval.** SQL over structured facts, exact document lookup, pgvector semantic search.
3. **Tool-using reasoning.** Python computes maturity walls, rate-reset cost, leverage, coverage and rankings. The LLM never does bond math.
4. **Independent verification and provenance.** Every material claim links to a source; every number links to a tool call; claims are checked by a different model from the one that wrote them.
5. **Closed research loop.** Plan → compute → verify → critique → revise → report, visible in the UI.

### Measured proof

The evaluation report includes a **plain-RAG baseline**: the same flagship questions answered by Spark over chunk retrieval alone, scored on numeric accuracy, citation coverage and missed facts. This is the most direct evidence against the anti-project label.

**Key claim:** Kernel does not retrieve passages and summarize them. It builds research state, executes analysis, checks its evidence and revises its conclusion.

## 5. Scope

**Principle:** complete across the research workflow, not across the bond market.

| Dimension | Scope (v2) |
|---|---|
| Asset class | USD investment-grade corporate bonds |
| Geography | United States |
| Issuers | **8–10** |
| Sectors | **Utilities** (holdco/opco structures for Workflow 2) plus **1–2 sectors with a visible 2027–28 maturity wall**, chosen from the data |
| Bonds | **~25** instruments, hand-curated |
| Documents | Latest 10-K/10-Q, recent 8-Ks, earnings releases, selected 424B prospectus supplements |
| Events | Trailing 24 months |
| Macro | Treasury curve, policy rate, inflation; spread context where data rights allow |
| Forward view | Maturities and par-call windows |

### Workflow priority

| Workflow | Depth |
|---|---|
| 1. Refinancing-pressure scanner | **Flagship.** Built deep; the demo centerpiece. |
| 2. Structural-subordination comparison | **Thin but memorable.** One strong utility holdco/opco comparison. |
| 3. Event-impact memo | **Stretch / cached example.** Built only if Workflows 1–2 are solid by Nov 2. |

### Non-goals

- Global or multi-asset coverage; live executable prices; investment recommendations; trading or portfolio optimization.
- Rating histories or any licensed ratings data.
- Auth, billing, multi-tenancy, Kubernetes.
- **Live dependence on Wonder or SAM during the judged demo.** Flagship runs are pre-computed and cached; the live product replays them and runs Spark-level interactions live.

## 6. Data strategy

**Three tiers:** public structured data as backbone; hand-curated bond reference data for precision; document intelligence as the AI layer.

| Layer | Source | Role | Notes |
|---|---|---|---|
| Corporate financials | SEC EDGAR XBRL company facts | Debt, cash, interest expense, operating income, EBITDA proxy | Start with a small set of well-understood concepts |
| Filings | EDGAR submissions API | 10-K, 10-Q, 8-K, 424B | Store accession number, URL, filing date, content hash |
| Bond reference data | **Hand-curated from 10-K debt footnotes and 424B supplements** | Coupon, maturity, par-call date, make-whole terms, seniority, security, issuing entity, outstanding amount | Cross-checked against XBRL `DebtInstrument*` tags where present; every field has a `CuratedSource` record |
| Events | EDGAR 8-Ks, issuer IR releases | Redemptions, new issuance, M&A, guidance, rating changes **as disclosed by the issuer** | Typed taxonomy with confidence and citations |
| Macro | FRED | Treasury yields, policy rate, inflation | **Verify current availability and history limits of ICE BofA OAS series before relying on them**; fall back to a clearly labeled sector-spread assumption |
| Pricing | None | — | Out of scope |

### Ratings

No rating history in the MVP. Agency releases are copyrighted and licensed data is out of scope. At most, Kernel cites ratings the issuer discloses in its own filings, labeled as such.

### Data quality rules

- Every fact has a source; every curated field has a `CuratedSource` record.
- Every number is computed in code and linked to a `ToolCall`.
- Every extracted field has a confidence score; missing data is shown, never hidden.
- Every research run stores inputs, outputs, model calls, tool calls and citations.
- No LLM-generated prices, ratings or recommendations.

### Licensing and compliance

Use EDGAR within its access policy (declared User-Agent, rate limits) and FRED within its API terms. No scraping of paid providers. Label curated and assumed data. Disclaimer everywhere: *research support, not investment advice.*

## 7. Product capabilities

### 7.1 Issuer 360
Business profile; capital-structure summary **by issuing entity** (holdco vs. opco); bond inventory; maturity wall; par-call windows; rate-reset exposure; financial trend panel; event timeline; recent filings; cited credit thesis; data gaps; research-run history.

### 7.2 Bond view
Terms (coupon, maturity, make-whole spread, par-call date); seniority, security and issuing entity; refinancing context (coupon vs. current reset yield); related documents; comparables within the universe; source-linked evidence.

### 7.3 Research workbench (frontier component)
The analyst asks a question. Kernel classifies it, plans, gathers data, runs tools, verifies claims independently, critiques, revises and writes a memo, with each step visible live.

### 7.4 Run trace (observability as a product feature)
For every step: model or tool, input summary, output summary, tokens, cost, latency, citations, confidence. Total cost and latency per run. This is how judges see production craft in the product, not just in logs.

### 7.5 Citation and evidence layer
Each claim shows: source document, page or region, extracted field, confidence, linked calculation, data timestamp and limitations.

## 8. Flagship workflows (IG-correct)

### Workflow 1 — Refinancing-pressure scanner (flagship)

**Question:** *Which issuers face the most material refinancing pressure over the next 24 months?*

For IG issuers, refinancing pressure is mostly **rate-reset cost**, not default risk: debt issued at 2–3% coupons in 2020–21 matures into a materially higher rate environment.

**Pipeline:**

1. Load maturities and par-call windows within the horizon.
2. Load debt, cash, interest expense and operating-income/EBITDA proxies from XBRL.
3. Compute, per issuer:
   - **Maturity concentration:** debt maturing in 24 months as a share of total debt and of liquidity.
   - **Rate-reset cost:** Σ maturing amount × (current Treasury yield at matching tenor + spread assumption − coupon). Reported as incremental annual interest expense.
   - **Coverage impact:** pro-forma interest coverage after reset vs. current.
   - Leverage and coverage trends.
4. Retrieve recent filings and events (new issuance, redemptions, guidance).
5. Rank with a transparent composite score; weights shown in the UI.
6. Independent verification, critique, revision.
7. Output: ranked watchlist, maturity-wall chart, issuer risk cards, evidence table, memo, monitoring triggers, limitations.

**Call features:** IG bonds typically carry a **make-whole call plus a par call 1–3 months before maturity**. Kernel models the par-call window as the effective refinancing date. It does not model high-yield-style step-down call schedules.

### Workflow 2 — Structural-subordination comparison

**Question:** *Compare these two bonds and explain the structural differences that matter to creditors.*

IG covenant packages are thin (liens, sale-leaseback, merger), so a covenant matrix alone undersells the workflow. The demo compares a **utility operating company's first-mortgage bonds** with its **holding company's senior unsecured notes**: same corporate family, different claims on assets and cash flow.

**Pipeline:** extract terms, security and issuing entity; normalize seniority, security, guarantees, call features and covenants; retrieve each entity's financial context (e.g. holdco debt as a share of consolidated debt, dividend dependence on opcos); build a side-by-side matrix; cite every term.

**Output:** comparison matrix, structural-risk notes, covenant summary, source-linked evidence, comparison memo. Bond terms come from parsed filing HTML tables, each cited to its source table.

### Workflow 3 — Event-impact memo (stretch)

**Question:** *What is the credit impact of this filing or earnings release for issuer X and its bonds?*

Ingest the event, classify it, retrieve prior state, compute metric deltas, assess affected bonds, write the memo with monitoring triggers. Optional: earnings-call audio via Muse Voice Transcribe, **only for audio Kernel has the rights to use.**

## 9. Agent architecture

### State machine

INTAKE → CLASSIFY_TASK → PLAN → GATHER_STRUCTURED_DATA → RETRIEVE_EVIDENCE → EXTRACT_FACTS → RUN_TOOLS → VERIFY_CLAIMS → CRITIQUE → REVISE → SYNTHESIZE → GENERATE_MEMO → COMPLETE

The state machine is **code-driven**: transitions are decided by Python, and LLM calls are nodes within states. The agent chooses tools and arguments inside a state; it does not choose arbitrary next states.

| State | Responsibility | Model / engine |
|---|---|---|
| Classify, Plan | Workflow type, required data, explicit plan | Spark |
| Gather, Retrieve | SQL, exact lookup, pgvector | Code |
| Extract facts | Strict JSON with citations; chart readings verified by geometry | Spark (+ SAM for charts) |
| Run tools | Deterministic Python | Code |
| **Verify claims** | Check each claim against citations and tool outputs | **Spark Open (independent model)** |
| Critique | Unsupported claims, alternative explanations, gaps | Spark; Wonder for flagship |
| Revise | Re-query, recompute or soften claims | Spark / Wonder |
| Synthesize, Memo | Cited narrative and artifact | Spark; Wonder for flagship |

### Guardrails

Allowlisted tools; sandboxed Python; max iterations; token and cost ceilings per run; mandatory citation check before COMPLETE; no LLM-generated prices or ratings; visible plan and trace; explicit uncertainty and data gaps.

### The critique moment (scripted, but real)

The flagship demo depends on a genuine catch. Seed the corpus with a real trap, for example:

- a bond in the curated seed data that was **redeemed early per a later 8-K**, or
- **commercial paper or a term loan** outstanding that is absent from the bond list but visible in the 10-K.

The critic flags the inconsistency, the agent re-queries, recomputes and changes the ranking. The trace shows before and after.

## 10. Technical architecture

Next.js dashboard → FastAPI API → ingestion service, Postgres + pgvector, agent orchestrator, sandboxed Python runtime, background worker. Model calls go through one client layer that serves both the Meta Model API and Baseten (OpenAI-compatible).

**Backend:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0, Alembic, PostgreSQL 16 + pgvector, Redis, ARQ, pandas, pdfplumber, pytest, httpx.

**Frontend:** Next.js App Router, TypeScript, Tailwind, shadcn/ui, TanStack Query, Zod, ECharts.

**Ops:** Docker Compose (one command locally), GitHub Actions CI, deployment of a public read-only instance (Fly.io, Railway or Render) with pre-ingested corpus and cached flagship runs.

## 11. Data model

| Entity | Purpose |
|---|---|
| Issuer | Company or obligor; parent/child relationships for holdco/opco |
| Bond | Instrument, with issuing entity, seniority, security, coupon, maturity |
| CallTerms | Make-whole spread and par-call date |
| Filing | SEC or issuer document, with accession number and hash |
| DocumentChunk | Searchable text segment with page locator |
| **DocumentRegion** | Located region (SAM chart/bar box or parsed table), page, label, confidence |
| **ChartReading** | Chart values read by Spark, SAM bar boxes, geometry-check status and deviations |
| Metric | Time-series financial or macro metric |
| Event | Typed corporate or macro event |
| Covenant | Extracted contractual term |
| **CuratedSource** | Provenance for hand-entered seed fields |
| ResearchRun | Agent session |
| AgentStep | Individual state execution |
| ToolCall | Deterministic function execution, inputs and outputs |
| **ModelCall** | Model, provider, prompt version, tokens, cost, latency, status |
| Citation | Claim-to-source link (chunk, region or tool call) |
| EvaluationCase | Benchmark question or extraction field |

**Rule:** the relational model is the source of truth; the vector index is a retrieval aid.

## 12. Evaluation plan

| Benchmark | Size |
|---|---|
| Documents | ~20 |
| Human-verified extraction fields | ~30 |
| Research questions with expected evidence and calculations | ~10 |
| End-to-end workflows | Workflow 1 + Workflow 2 |
| **Plain-RAG baseline** | Same questions, Spark over chunk retrieval only |

| Metric | Target |
|---|---|
| Bond-term extraction precision | ≥ 90% |
| Financial-metric accuracy on curated fields | ≥ 95% |
| Material claims with citation | ≥ 95% |
| Numeric claims traced to a tool call | 100% |
| Flagship workflow completion | 100% on benchmark questions |
| Median research run | < 3 minutes (Spark mode) |
| Cost per research run | Reported (Spark mode and Wonder mode) |
| Kernel vs. plain-RAG | Numeric accuracy, citation coverage, missed facts |

Report measured results only. Include a failure taxonomy.

## 13. Production readiness (built in from day one)

- One-command Docker Compose; `.env.example` with no secrets; Alembic migrations; seed script.
- **GitHub Actions CI** running tests on financial calculations and extraction schemas; badge in README.
- Health checks for API, database, Redis and worker.
- Structured JSON logs with request, run, step and model-call IDs.
- Retry with backoff for external APIs; EDGAR rate limiting; input validation.
- **Run-trace UI** with cost and latency per step.
- UI error, empty and loading states; graceful degradation to cached runs.
- **Deployed public read-only instance.**
- Data-source and licensing documentation.
- **"Built during the hackathon" README section** linking commit history.
- Disclaimer: research support, not investment advice.

## 14. Demo plan (exactly 3 minutes)

| Time | Content |
|---|---|
| 0:00–0:20 | **Open on the agent trace already running**: plan, tool calls, model badges. One line on the user and problem. |
| 0:20–1:20 | Flagship run: data gathered, maturity wall and rate-reset cost computed, ranking forms. |
| 1:20–1:45 | **Critique moment:** independent verifier/critic catches the redeemed bond or missing CP; agent re-queries; ranking changes. |
| 1:45–2:15 | Memo with click-through citations; SAM-verified maturity chart from the issuer deck next to the filing-based maturity wall; Workflow 2 subordination matrix. |
| 2:15–2:40 | Meta pipeline panel; architecture diagram; run cost and latency. |
| 2:40–3:00 | Eval numbers including the plain-RAG comparison; live URL; close. |

**Closing line:** *"Kernel doesn't retrieve passages. It conducts, verifies and explains research."*

**Rules:** pre-ingest the corpus; cache flagship runs; record a fallback; citations on screen; never a chat-only shot; show only hackathon-built features. Produce a separate **60-second cut** for X.

## 15. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Judges read it as basic RAG | Open video on the trace; README first line; plain-RAG baseline in eval |
| Scope overrun | Workflow priority fixed; Workflow 3 cut if behind on Nov 2 |
| Model IDs / API differ from assumptions | Smoke-test every model on day 1 against the official model list |
| SAM 3.1 weak on document pages | **Confirmed day 1:** no detections on text/tables. Tables come from filing HTML (pdfplumber for PDFs); SAM used only for charts, where it works |
| Chart reader fails on dense or stacked maturity charts | Geometry check returns "unverified" rather than a false pass; test on real maturity-profile charts before relying on it |
| Bond reference data errors | Hand-curated with `CuratedSource`; cross-check XBRL |
| Spread data unavailable | Labeled spread assumption, shown in UI |
| Credit budget runs out | Spark Open for bulk; cache by hash; Wonder only for flagship runs |
| Live demo fails | Cached runs, deployed instance, recorded fallback |
| Disqualification on new-work rule | Fresh repo, frequent commits, explicit README section |

## 16. What to avoid

- Positioning as a trading bot, portfolio manager or advice engine.
- Generating prices or ratings; using licensed data without rights.
- High-yield assumptions in an IG product (step-down call schedules, covenant-heavy framing).
- Letting the LLM do bond math.
- An unconstrained agent or vector search as the data layer.
- Claiming Muse Code (or any tool) did more than it did.
- Submitting without a deployed instance, video, architecture diagram, eval results and setup instructions.

## 17. Submission narrative

Kernel Credit Lab addresses a problem I know firsthand: fixed-income research is slowed by fragmented filings, bond documentation and macro context. Kernel turns these inputs into a structured, cited knowledge base for USD investment-grade corporate bonds and runs a verifiable research loop over it.

Asked which issuers face the greatest refinancing pressure, Kernel plans the investigation, gathers structured and document evidence, computes each issuer's rate-reset cost and coverage impact in deterministic Python, has an independent open-weight model verify every claim, catches and corrects its own mistakes, and returns a source-linked memo with its full trace, cost and latency.

Kernel runs on a hybrid Meta stack: SAM 3.1 locates charts in issuer presentations and its bar geometry verifies every value Muse Spark reads from them, Muse Spark extracts, plans and synthesizes, Muse Wonder runs deep investigations, and Muse Spark Open handles bulk document processing and independent verification, a path that can be self-hosted by firms whose documents cannot leave their perimeter. Built with FastAPI, PostgreSQL/pgvector, a sandboxed tool runtime and a Next.js workbench, with CI, an evaluation report and a deployed instance.

---

# Part B — Meta Model Usage Plan

## 1. Layers

| Layer | Tool | Purpose |
|---|---|---|
| Development | Cursor (primary IDE), Muse Code | Build Kernel; Muse Code for 3–4 documented tasks |
| Hosted runtime | Muse Spark, Muse Wonder, SAM 3.1 (Meta Model API) | Extraction, planning, deep research, chart vision |
| Open-weight runtime | **Muse Spark Open** via Baseten (`meta-models/muse-spark-open-1026`) | Bulk processing and independent verification |
| Stretch | Muse Voice Transcribe | Earnings-call audio for Workflow 3, rights permitting |
| Minimal | Muse Image | At most one labeled visual, or none |

**Confirmed on day 1** (smoke test against `GET /v1/models`):

| Setting | Value |
|---|---|
| Base URL | `https://api.meta.ai/v1` (OpenAI-compatible; Bearer auth) |
| Muse Spark | `muse-spark-1.3` — chat completions; strict `json_schema` works (schemas need `additionalProperties: false`, all fields required) |
| Muse Wonder | `muse-wonder-1.0` — chat completions; strict `json_schema` works |
| SAM 3.1 | `sam-3.1` — **only via `/v1/responses`** (streamed); output is box/mask tokens in `output_text` |
| Also available | `muse-voice-transcribe-1.0`, `muse-image-1.0`, older Spark versions |
| Muse Spark Open | `meta-models/muse-spark-open-1026` on Baseten — pending credits |

## 2. Model ownership

| Capability | Model |
|---|---|
| Document classification, chunk tagging (bulk) | **Spark Open** |
| Table extraction | Parsed filing HTML (pdfplumber for PDFs) + Spark |
| Chart location and bar geometry | SAM 3.1 (`"bar chart"`, `"bar"` prompts) |
| Chart value reading | Spark (vision, strict JSON), verified by SAM geometry |
| Bond, covenant and metric extraction | Spark |
| Event classification | Spark Open (bulk) / Spark (ambiguous) |
| Research planning and tool selection | Spark |
| **Independent claim verification** | **Spark Open** |
| Critique and revision | Spark; Wonder for flagship |
| Deep research runs | Wonder |
| Memo generation | Spark; Wonder for flagship |

## 3. Muse Spark — the reasoning workhorse

Extraction from regions and pages into strict JSON with page/region citations, confidence scores and explicit nulls; research planning; tool orchestration; synthesis and memos.

**Pattern:** Spark decides what to compute, Python computes it, Spark Open verifies the claim against the result, Spark interprets.

**Example extraction schema (IG-correct):**

```json
{
  "document_id": "doc_123",
  "issuing_entity": {"name": "Example Utility Holdings Inc.", "cik": "0000000000", "role": "holdco"},
  "bonds": [{
    "instrument_name": "4.750% Senior Notes due 2031",
    "coupon": 4.75,
    "maturity_date": "2031-06-15",
    "seniority": "senior_unsecured",
    "security": null,
    "outstanding_amount_usd": 600000000,
    "call_terms": {
      "make_whole_spread_bps": 25,
      "par_call_date": "2031-03-15"
    },
    "covenants": [
      {"type": "limitation_on_liens", "source_page": 48, "confidence": 0.93}
    ]
  }],
  "citations": [
    {"field": "coupon", "page": 12, "locator": "cover page, line 2"},
    {"field": "par_call_date", "page": 15, "region_id": "reg_88"}
  ]
}
```

## 4. Muse Wonder — deep research mode

Used for the flagship refinancing scan, the Workflow 2 comparison memo and a "Deep Research" toggle. Budget for roughly 10–20 runs. Outputs: plan, evidence, requested calculations, draft, critique, revision, final memo, source appendix, limitations. All flagship Wonder runs are cached for the demo and the live instance.

## 5. SAM 3.1 — verifiable chart reading

**Day-1 findings.** On a prospectus-style page SAM found nothing for "table", "document table", "text", "paragraph" or "chart" (only "page" = whole image). On a real investor-deck slide (Duke Energy Q2 2026 EPS charts) it worked: "bar chart" returned both charts with tight boxes (conf ≈ 0.78); "bar" returned all 8 bars plus 2 legend swatches. Prompt wording matters ("chart" alone returned nothing).

**Pipeline (built, `app/tools/charts.py`):**

1. SAM segments `"bar chart"` → one box per chart.
2. SAM segments `"bar"` → bar boxes; bars outside every chart (legend swatches) are dropped.
3. Each chart is cropped and sent to Spark together with the full slide (for legend and titles); Spark returns bars left to right with printed values, as strict JSON.
4. **Geometry check (deterministic):** with a shared zero baseline, bar heights must be proportional to the values read. Bars deviating more than 8% are flagged; mismatched bar counts, stacked or offset charts return "unverified" instead of a false pass.

**Result on the Duke slide:** 2 charts, 8/8 bars read correctly, all within 1.3% of geometry → VERIFIED. About 5k input tokens and ~10 s per chart.

**Role in Kernel:** read the **debt maturity profile** chart from each issuer's fixed-income/investor deck, verify it geometrically, then cross-check it against the filing-based maturity wall. Disagreements become critic findings. Next test: real maturity-profile charts (denser, sometimes stacked).

**Not used for:** document tables or text regions (SAM does not detect them); those come from filing HTML.

## 6. Muse Spark Open — bulk processing and independent verifier

**Why it matters for the main prize:** buy-side and bank users often cannot send documents to hosted APIs. Kernel's architecture shows a self-hostable path for document processing, keeps costs low for volume work, and makes verification independent of the generating model.

**Roles:**

1. Document classification and chunk tagging across the whole corpus.
2. First-pass event classification.
3. **Claim verifier:** for each claim, receives the claim, its cited chunk or tool output, and returns `supported / unsupported / partially_supported` with a reason.

**Serving:** Baseten, `meta-models/muse-spark-open-1026` (not the ID in Baseten's docs). Same OpenAI-compatible client as the Meta API. Apply for Baseten credits immediately.

## 7. Muse Voice Transcribe (stretch)

Earnings-call audio → transcript → Workflow 3 event extraction. Only with audio Kernel has the right to use.

## 8. Muse Image (minimal)

At most one clearly labeled memo header. Never used for anything resembling data or charts.

## 9. API integration

**Environment variables:**

```
MODEL_API_KEY=
META_MODEL_API_BASE_URL=https://api.meta.ai/v1
DEFAULT_MODEL=muse-spark-1.3
DEEP_RESEARCH_MODEL=muse-wonder-1.0
VISION_SEGMENTATION_MODEL=sam-3.1
BASETEN_API_KEY=
BASETEN_BASE_URL=                 # from Baseten quickstart
OPEN_MODEL=meta-models/muse-spark-open-1026
```

**Client pattern:** one wrapper serving both providers, with `structured_json()` (Pydantic validation), `retry_with_backoff()` on transient errors only, and a `ModelCall` row per call (provider, model, prompt version, tokens, cost, latency, run ID, status). Cache by document hash + page range + prompt version + model.

**Rules:** temperature 0 for extraction, classification and verification; validate every response; never retry invalid JSON blindly; separate extraction from synthesis prompts; models never overwrite source-of-truth data without validation.

## 10. Prompts (versioned)

`document_classifier_v1` (Spark Open), `chunk_tagger_v1` (Spark Open), `bond_term_extractor_v1` (Spark), `covenant_extractor_v1` (Spark), `financial_metric_extractor_v1` (Spark), `event_classifier_v1` (Spark Open/Spark), `research_planner_v1` (Spark), `tool_selector_v1` (Spark), `claim_verifier_v1` (**Spark Open**), `critic_v1` (Spark/Wonder), `memo_writer_v1` (Spark/Wonder), `deep_research_v1` (Wonder).

Principles: JSON output; page/region/chunk citations; confidence scores; explicit null; no invented identifiers, prices, ratings or dates; negative examples for ambiguous fields (e.g. make-whole vs. par call).

## 11. Tools

| Tool | Output |
|---|---|
| get_issuer, get_bonds, get_entity_structure | Profiles, bond list, holdco/opco tree |
| get_maturity_schedule | Maturities and par-call windows |
| get_financial_metrics, get_recent_events | Metric series, event timeline |
| get_macro_series | Treasury yields by tenor, policy rate |
| calculate_maturity_wall | Concentration by period |
| **calculate_rate_reset_cost** | Incremental annual interest expense |
| **calculate_proforma_coverage** | Coverage before/after reset |
| calculate_leverage_trend, calculate_coverage_trend | Trends |
| calculate_metric_delta | Before/after deltas |
| rank_refinancing_pressure | Ranked list with visible weights |
| compare_bonds | Subordination comparison matrix |
| search_documents, get_citations | Chunks and sources |
| render_chart | Chart artifact from computed data |

**Rule:** the LLM decides what to compute; Python computes; the verifier checks.

## 12. Meta usage in the demo

Visible **Meta pipeline panel**: SAM 3.1 (charts) → Spark → Kernel tools → Spark Open (verify) → Spark/Wonder → cited memo. For each step: model, provider, task, input and output summary, tokens, cost, latency, citations, confidence.

## 13. Development tooling (honest attribution)

Cursor is the primary IDE. Muse Code is used for **3–4 specific, documented tasks** (e.g. the model client and validation layer, the state-machine refactor, edge-case tests for financial tools, the eval runner), each linked to commits in `META_TOOLING.md`. No blanket claims.

---

# Part C — Implementation Plan (Oct 27 – Nov 4)

Times are Madrid unless noted (ET sessions are 17:00 Madrid until US clocks change on Nov 1, then 18:00). Day-by-day status lives in [PROGRESS.md](PROGRESS.md). Production signals (CI, structured logs, `ModelCall`/`ToolCall` tables, error states) are built into each day, not deferred.

| Day | Deliverables | Exit condition |
|---|---|---|
| **Tue Oct 27** ✅ | Fresh public repo; Docker Compose (API, Postgres+pgvector, Redis, worker, web); Cursor rules; `.env.example`; CI skeleton; unified model client; **smoke tests for Spark, Wonder, SAM 3.1 (on a prospectus page), Spark Open on Baseten**; apply for Baseten credits; Agents 101 (17:00 Madrid) | All models answer with confirmed IDs; CI green; key only in `.env` |
| **Wed Oct 28** | Full schema + migrations (incl. `ModelCall`, `DocumentRegion`, `CuratedSource`); issuer shortlist; hand-curated seed for ~25 bonds; seed loader with validation; Agents 102 (17:00 Madrid) | Universe loaded and queryable |
| **Thu Oct 29** | EDGAR submissions + XBRL client (rate-limited, declared User-Agent); FRED client; filing ingestion with hashes; metric normalization | Filings, metrics and macro series ingested |
| **Fri Oct 30** | PDF/HTML parsing, chunking, embeddings, pgvector; hybrid retrieval; Spark Open classification/tagging; Spark extraction with citations; HTML table parsing (pdfplumber for PDFs); chart reader on issuer maturity-profile charts | A 424B and a 10-K produce validated terms and citations |
| **Sat Oct 31** | Deterministic tools + pytest (maturity wall, rate reset, pro-forma coverage, ranking); tool registry with `ToolCall` logging | All calculation tests pass in CI |
| **Sun Nov 1** | State machine end to end; Spark Open verifier; critic and revise loop; seed the critique trap; Workflow 1 complete; SSE progress endpoint | Flagship run completes with a real critique-driven revision |
| **Mon Nov 2** | Workbench UI: live trace with cost/latency, plan, citations popover, maturity-wall chart, memo view, issuer page; Workflow 2 (thin); verified maturity chart beside the maturity wall. **Cut Workflow 3 if behind.** | Question → trace → memo works in the browser |
| **Tue Nov 3** | Wonder deep-research runs (cached); eval runner + plain-RAG baseline; deploy public instance; README (first-line non-RAG claim, architecture diagram, eval table, "Built during the hackathon", Meta tooling, setup); last day for organizer troubleshooting | Live URL works; eval numbers measured |
| **Wed Nov 4** | Record 3-minute video and 60-second X cut; final checks (public repo, video link, team listed); **submit in the evening Madrid time**; post on X | Submitted well before 03:00 Nov 5 Madrid |

### Security rule (non-negotiable)

API keys (Meta and Baseten) live only in `.env` or deployment secrets. They are never hardcoded, committed, exposed to the browser or pasted into chat. Model calls happen only from the backend.

### README skeleton

1. One-line pitch + "Not basic RAG: here is the trace" GIF.
2. Live demo URL, video link, CI badge.
3. Architecture diagram.
4. Evaluation results (measured), including plain-RAG baseline.
5. Meta AI tooling (hosted + open-weight roles; Muse Code tasks with commit links).
6. Built during the hackathon (commit history, dates).
7. Data sources, licensing and limitations.
8. Local setup (one command).
9. Disclaimer: research support, not investment advice.
