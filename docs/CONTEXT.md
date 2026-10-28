# Kernel Credit Lab — Product, Hackathon & Build Context

*Living document — the single source of truth for the project. Last updated 28 Oct 2026 (day 2: product reframed as a proactive monitor). Supersedes the v1 and v2 PDFs. Dated changes and decisions are logged in [PROGRESS.md](PROGRESS.md).*

> **28 Oct 2026 — direction change:** Kernel is a **proactive credit monitor**, not a question-driven research tool. It watches a watchlist of issuers, reacts to events (filings, redemptions, tenders, rate moves, approaching maturities), updates its data, recomputes, verifies and raises only material alerts. Questions remain as a secondary drill-down. The engine (state, tools, verifier, critic, trace) is unchanged; the trigger is. See §1, §3, §7–9, §14 and Part C.
>
> **What changed from v1:** Startup track confirmed; official rules and rubric added; explicit defense against the "Basic RAG" anti-project; scope cut to a buildable size; investment-grade domain fixes (rate-reset refinancing, par-call windows, structural subordination instead of covenant comparison); Muse Spark Open promoted to a core role; Muse Voice Transcribe added as a stretch; Muse Image demoted; production readiness moved into the core build; 17 sequential phases replaced by a day-by-day schedule ending Nov 4.

---

# Part A — Product & Hackathon Context

## 1. Executive summary

Kernel Credit Lab is a **proactive credit monitor** for USD investment-grade (IG) corporate bonds. An analyst sets a watchlist once; Kernel then watches SEC filings, issuer announcements, Treasury yields and the maturity calendar on its own. Each event triggers a constrained agent run that reads the document, updates a structured, cited knowledge base, recomputes the affected credit metrics with deterministic tools, has an independent model verify every claim, critiques and corrects itself, and raises an alert **only when something material changed**.

- **Track:** Startup.
- **Problem statement:** Build for Your World (a firsthand fixed-income research problem), with a secondary Push the Frontier claim: giving an AI system the ability to conduct, verify, critique and revise credit research end to end.
- **Not basic RAG:** "Basic RAG Applications" is an explicit anti-project in the official guide. Kernel's defense is built into the product and the demo: persistent relational state, deterministic computation, independent verification, a visible critique-and-revise step, and a measured comparison against a plain-RAG baseline.

**One-sentence pitch:** Kernel watches your credits so you don't have to: it notices what changed, works out whether it matters, and shows its work.

**Core narrative:** Credit analysts monitor dozens of issuers across filings, bond terms, announcements and rates in disconnected systems, and important changes slip through: a bond redeemed early, a new term loan, a tender that halves an issue, a rate move that changes refinancing costs. Kernel catches these as they happen. When Verizon announces it will redeem $1.25bn of 2028 notes, Kernel reads the notice, removes the bond, recomputes Verizon's maturity wall and refinancing pressure, verifies the result, and posts an alert with before/after numbers, sources and the full agent trace.

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
| Impact | 30% | Real-world potential; who benefits; could people actually use it? | Continuous monitoring is what analysts actually need (being told what changed, not asking); measured catches on real 2026 events; credible enterprise path (self-hostable document processing). |
| Use of Meta AI Tooling | 25% | Creative, beyond basic integration, capabilities that surprise | Hosted + open-weight hybrid: SAM 3.1 locates charts in investor decks and its geometry verifies Spark's chart readings; Spark extracts and plans; Wonder investigates; Spark Open verifies claims independently. |
| Technical Depth & Execution | 20% | Sound, refined engineering; real craft | Relational source of truth, deterministic tools with tests, state machine, CI, eval report, observability. |
| Demo | 25% | Working, impressive, holds up live, cool to watch | Replay of real Jan–Oct 2026 events producing a live alerts feed; each alert opens its agent trace; live URL judges can use. |

**Stage Three:** top 5 per track; judges watch videos live and rank with the same rubric.

**Production readiness** for the startup track is not a separate box. It is scored through Technical Depth ("real craft"), Demo ("holds up live") and Impact ("could this become something people use"). It is therefore part of the core build from the first commit, not a final hardening phase.

### Secondary targets (never at the expense of the main prize)

- **Best Agentic Use-Case ($5K):** strong fit: an autonomous, event-driven agent that decides what matters.
- **Best Build on Meta Open Weight Models ($10K):** Spark Open has a real core role (see Part B).
- **Most Viral ($20K) and Community Vote ($10K):** post a 60-second cut on X immediately after submission.
- Not targeted: Best Creative Build.

## 3. Product definition

**Name:** Kernel (Kernel Credit Lab). **Category:** proactive, agentic credit monitoring.

**Primary users:** IG credit analysts, fixed-income researchers, IG bond investors, treasury and capital-structure teams, financial intelligence platforms.

**Design user:** a credit analyst monitoring a universe of USD IG issuers for changes in refinancing cost, capital structure and credit quality.

**Problem:** bond terms live in prospectuses and 424B filings; financials in SEC filings and XBRL; events in 8-Ks and press releases; macro context in separate systems. Monitoring is manual, so changes are noticed late or missed, and the analysis behind them is hard to reproduce. Generic LLM tools answer questions but do not watch, keep structured state or verify calculations.

**Solution:**

1. A **watchlist** of issuers and a structured issuer / bond / loan / event knowledge base (relational source of truth).
2. **Event watchers** for SEC filings, issuer announcements, Treasury yields and the maturity calendar.
3. An **agent run per event**, inside a constrained state machine: read, update state, recompute, verify, critique, assess materiality.
4. Deterministic financial tools for every number.
5. An independent verifier and a critic.
6. **Material alerts and a daily brief**, each linked to sources and the full run trace; analysts confirm or dismiss.
7. Ad-hoc questions as a secondary drill-down.

## 4. What makes Kernel different (and not basic RAG)

**Basic RAG:** documents → chunks → embeddings → retrieval → LLM answer. It loses structure, cannot compute reliably, has weak provenance, does not separate evidence from inference, keeps no state, and cannot verify numbers.

**Kernel:** events → structured extraction → validated knowledge base (state updated) → deterministic tools → independent verification → critique → revision → materiality decision → alert with trace. Nobody has to ask.

### Five differentiators

1. **Persistent structured state.** Issuers, bonds, call terms, maturities, events, filings, metrics and research runs live in Postgres. The vector index is a retrieval aid, never the system of record.
2. **Hybrid evidence retrieval.** SQL over structured facts, exact document lookup, pgvector semantic search.
3. **Tool-using reasoning.** Python computes maturity walls, rate-reset cost, leverage, coverage and rankings. The LLM never does bond math.
4. **Independent verification and provenance.** Every material claim links to a source; every number links to a tool call; claims are checked by a different model from the one that wrote them.
5. **Closed loop, triggered by events.** Detect → update → compute → verify → critique → revise → alert, visible in the UI.
6. **Proactive.** Kernel decides when something is worth an analyst's attention; it is not waiting for a prompt.

### Measured proof

The evaluation report includes a **plain-RAG baseline**: the same flagship questions answered by Spark over chunk retrieval alone, scored on numeric accuracy, citation coverage and missed facts. This is the most direct evidence against the anti-project label.

**Key claim:** Kernel does not retrieve passages and summarize them. It keeps research state current as the world changes, executes analysis, checks its evidence and tells you what matters.

## 5. Scope

**Principle:** complete across the research workflow, not across the bond market.

| Dimension | Scope (v2) |
|---|---|
| Asset class | USD investment-grade corporate bonds |
| Geography | United States |
| Issuers | **9 entities** (see [ISSUERS.md](ISSUERS.md)) |
| Sectors | Utilities (Duke holdco + 3 opcos), technology, telecom, aerospace, semiconductors |
| Instruments | **21 bonds + 2 term loans**, hand-curated and verified against primary sources |
| Documents | Latest 10-K/10-Q, recent 8-Ks, earnings releases, selected 424B prospectus supplements |
| Events | Watched going forward; **replay window Jan–Oct 2026** for the demo |
| Macro | Treasury curve, policy rate, inflation; spread context where data rights allow |
| Forward view | Maturities and par-call windows |

### Capability priority

| Capability | Depth |
|---|---|
| 1. **Event monitor + alerts feed** (watchers, per-event agent run, materiality, alerts, daily brief) | **Flagship.** The product. |
| 2. **Refinancing-pressure engine** (maturity wall, rate-reset cost, pro-forma coverage, ranking) | **Core.** Recomputed on every event; powers the alerts. |
| 3. **Replay mode** (run the monitor over real Jan–Oct 2026 events) | **Core for the demo.** |
| 4. Structural-subordination view (holdco vs. opco) | **Thin.** Issuer page panel for Duke. |
| 5. Ad-hoc questions | **Thin.** Drill-down from an alert or issuer page. |

### Non-goals

- Global or multi-asset coverage; live executable prices; investment recommendations; trading or portfolio optimization.
- Rating histories or any licensed ratings data.
- Auth, billing, multi-tenancy, Kubernetes.
- **Live dependence on Wonder or SAM during the judged demo.** Replay runs are pre-computed and cached; the live product shows them and runs Spark-level drill-downs live.
- Real-time streaming feeds or paid news data: watchers poll public sources on a schedule.

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

### 7.0 Alerts feed and daily brief (flagship)
A feed of **material** findings across the watchlist, newest and most important first. Each alert card shows: what happened (with the source document), what Kernel changed in its data, the before/after metrics (e.g. 2028 maturities −$1.25bn; refinancing-pressure rank 3 → 5), confidence, and a link to the run trace. Analysts can **confirm** or **dismiss** an alert; dismissals are recorded. A **daily brief** summarizes overnight changes. Non-material events are logged on the issuer timeline without an alert.

### 7.0b Watchlist and watchers
The analyst adds issuers to a watchlist. Watchers run on a schedule in the background worker: EDGAR new filings per issuer (8-K, 10-Q, 10-K, 424B), issuer announcement pages where available, daily Treasury yields (FRED), and the maturity / par-call calendar.

### 7.1 Issuer 360
Business profile; capital-structure summary **by issuing entity** (holdco vs. opco); bond inventory; maturity wall; par-call windows; rate-reset exposure; financial trend panel; event timeline; recent filings; cited credit thesis; data gaps; research-run history.

### 7.2 Bond view
Terms (coupon, maturity, make-whole spread, par-call date); seniority, security and issuing entity; refinancing context (coupon vs. current reset yield); related documents; comparables within the universe; source-linked evidence.

### 7.3 Drill-down questions (secondary)
From an alert or issuer page, the analyst can ask a follow-up ("why did Verizon's rank change?"). The same agent engine answers with the same tools, verifier and trace.

### 7.4 Run trace (observability as a product feature)
For every step: model or tool, input summary, output summary, tokens, cost, latency, citations, confidence. Total cost and latency per run. This is how judges see production craft in the product, not just in logs.

### 7.5 Citation and evidence layer
Each claim shows: source document, page or region, extracted field, confidence, linked calculation, data timestamp and limitations.

## 8. How the monitor works (IG-correct)

### 8.1 Event types and what Kernel does

| Trigger | Source | Agent action | Example (real, 2026) |
|---|---|---|---|
| Redemption / call notice | 8-K, issuer press release | Mark bond redeemed; recompute maturity wall and pressure | Verizon redeems $1.25bn 4.329% 2028 notes (announced 20 Aug, settled 21 Sep) |
| Tender offer results | 8-K, press release | Reduce outstanding amounts | Verizon buys back 55% of its 2.100% 2028 notes (June) |
| New debt in a periodic filing | 10-Q / 10-K debt note | Add instruments missing from the bond list | Duke Carolinas and Progress $1.3bn term loans due Mar 2027 |
| New issuance | 424B, 8-K | Add bond; update refinancing assumptions | Intel $6.5bn of new notes (Q2) |
| Rate move | FRED daily Treasury yields | Recompute rate-reset cost for all maturing debt | Treasury curve changes over Jan–Oct |
| Calendar | Bond data | Flag maturities and par-call windows entering 6/12-month horizons | Duke Energy Florida 3.200% due Jan 2027 |

### 8.2 The refinancing-pressure engine (recomputed on every event)

For IG issuers, refinancing pressure is mostly **rate-reset cost**, not default risk: debt issued at 2–3% coupons in 2017–21 matures into a materially higher rate environment. Per issuer:

- **Maturity concentration:** debt maturing in 24 months as a share of total debt and of liquidity (bonds **and** loans).
- **Rate-reset cost:** Σ maturing amount × (current Treasury yield at matching tenor + spread assumption − coupon), reported as incremental annual interest expense.
- **Coverage impact:** pro-forma interest coverage after reset vs. current.
- **Ranking:** transparent composite score; weights shown in the UI.

**Call features:** IG bonds typically carry a **make-whole call plus a par call 1–3 months before maturity**. Kernel models the par-call window as the effective refinancing date. It does not model high-yield-style step-down call schedules.

### 8.3 Materiality

An event becomes an alert when it changes something an analyst would act on. Initial rules (configurable, shown in the UI):

- issuer's 24-month maturities change by ≥ 10% or ≥ $500M;
- rate-reset cost changes by ≥ 10%;
- the issuer moves ≥ 2 places in the pressure ranking;
- debt appears that was not in the bond list (always material);
- the verifier or critic finds a contradiction with Kernel's own data (always material).

Everything else goes to the issuer timeline.

### 8.4 Replay mode (demo and evaluation)

Kernel runs the monitor over the **real events of Jan–Oct 2026** in date order, starting from a **snapshot as of 31 Dec 2025** (year-end filings and issuer debt reports). The output is the alerts feed Kernel would have produced. This makes the demo deterministic and doubles as evaluation: each expected catch (redemption, tender, term loans, new issuance) is a test case, and **Kernel's state after replaying to 30 Jun 2026 must match the independently verified 30 Jun figures** in [ISSUERS.md](ISSUERS.md) (Duke debt details, Verizon debt portfolio).

### 8.5 Structural subordination (issuer page panel)

For the Duke family, the issuer page explains the structural differences that matter to creditors.

IG covenant packages are thin (liens, sale-leaseback, merger), so a covenant matrix alone undersells the workflow. The demo compares a **utility operating company's first-mortgage bonds** with its **holding company's senior unsecured notes**: same corporate family, different claims on assets and cash flow.

**Pipeline:** extract terms, security and issuing entity; normalize seniority, security, guarantees, call features and covenants; retrieve each entity's financial context (e.g. holdco debt as a share of consolidated debt, dividend dependence on opcos); build a side-by-side matrix; cite every term.

**Output:** comparison matrix and structural-risk notes, each term cited to its source.

Earnings-call audio via Muse Voice Transcribe remains a stretch trigger type, **only for audio Kernel has the rights to use.**

## 9. Agent architecture

### State machine

EVENT_RECEIVED → CLASSIFY_EVENT → PLAN → GATHER_STRUCTURED_DATA → RETRIEVE_EVIDENCE → EXTRACT_FACTS → PROPOSE_STATE_CHANGES → RUN_TOOLS → VERIFY_CLAIMS → CRITIQUE → REVISE → ASSESS_MATERIALITY → PUBLISH_ALERT | LOG_ONLY → COMPLETE

Drill-down questions enter at CLASSIFY_EVENT with type `question` and end with an answer instead of an alert. State changes (e.g. marking a bond redeemed) are **proposals** recorded with their evidence; they are applied only after verification, and every change is reversible and auditable.

The state machine is **code-driven**: transitions are decided by Python, and LLM calls are nodes within states. The agent chooses tools and arguments inside a state; it does not choose arbitrary next states.

| State | Responsibility | Model / engine |
|---|---|---|
| Classify event, Plan | Event type, affected issuers/instruments, explicit plan | Spark Open (bulk) / Spark |
| Gather, Retrieve | SQL, exact lookup, pgvector | Code |
| Extract facts | Strict JSON with citations; chart readings verified by geometry | Spark (+ SAM for charts) |
| Run tools | Deterministic Python | Code |
| **Verify claims** | Check each claim against citations and tool outputs | **Spark Open (independent model)** |
| Critique | Unsupported claims, alternative explanations, gaps | Spark; Wonder for flagship |
| Revise | Re-query, recompute or soften claims | Spark / Wonder |
| Propose state changes | Structured diffs to bonds/loans with evidence | Spark |
| Assess materiality | Apply rules to before/after metrics | Code |
| Publish alert / brief | Cited alert card; daily brief | Spark; Wonder for the brief |

### Guardrails

Allowlisted tools; sandboxed Python; max iterations; token and cost ceilings per run; mandatory citation check before COMPLETE; no LLM-generated prices or ratings; visible plan and trace; explicit uncertainty and data gaps.

### The critique moments (real events, not staged)

The seed is a snapshot built from sources dated **before** the replay window, so it contains the stale facts real analysts would have had:

- **Verizon 4.329% 2028** listed as outstanding; the replay delivers the redemption notice, and Kernel removes it and recomputes.
- **Duke term loans** absent from the bond list; the replay delivers the 10-Q, and the critic notices the gap between bond-level and filing-level debt.

The trace shows the evidence, the proposed change, the verifier's check and the before/after numbers.

## 10. Technical architecture

Next.js dashboard (alerts feed, brief, issuer pages, trace) → FastAPI API → Postgres + pgvector, agent orchestrator, sandboxed Python runtime. **Background worker (ARQ) runs the watchers on a schedule** and enqueues one agent run per event; replay mode feeds historical events through the same queue. Model calls go through one client layer that serves both the Meta Model API and Baseten (OpenAI-compatible).

**Backend:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0, Alembic, PostgreSQL 16 + pgvector, Redis, ARQ, pandas, pdfplumber, pytest, httpx.

**Frontend:** Next.js App Router, TypeScript, Tailwind, shadcn/ui, TanStack Query, Zod, ECharts.

**Ops:** Docker Compose (one command locally), GitHub Actions CI, deployment of a public read-only instance (Fly.io, Railway or Render) with pre-ingested corpus and cached replay runs.

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
| Event | Typed corporate or macro event, with source document and detection time |
| **Loan** | Term loans and other non-bond debt (or `Bond` with `instrument_type`) |
| **Watchlist** | Issuers the analyst monitors |
| **StateChange** | Proposed/applied change to an instrument, with evidence, verifier result and run |
| **Alert** | Material finding: event, before/after metrics, status (new / confirmed / dismissed), run |
| **MetricSnapshot** | Per-issuer computed metrics at a point in time (for before/after) |
| Covenant | Extracted contractual term |
| **CuratedSource** | Provenance for hand-entered seed fields |
| ResearchRun | Agent run (triggered by an event or a question) |
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
| **Replay catches** (expected alerts over Jan–Oct 2026) | ~8 events: redemption, tender, term loans, issuances, rate moves |
| Drill-down questions with expected evidence and calculations | ~8 |
| **Plain-RAG baseline** | Same events/questions, Spark over chunk retrieval only (no state, no tools) |

| Metric | Target |
|---|---|
| Bond-term extraction precision | ≥ 90% |
| Financial-metric accuracy on curated fields | ≥ 95% |
| Material claims with citation | ≥ 95% |
| Numeric claims traced to a tool call | 100% |
| Replay recall | Expected material events alerted / total expected |
| Replay precision | Alerts that are genuinely material / total alerts |
| Detection-to-alert latency | Per event (minutes) |
| Median agent run | < 3 minutes (Spark mode) |
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
| 0:00–0:15 | Hook: "Credit analysts miss things. Here's what Kernel caught in 2026 so far, on its own." The analyst's watchlist of 9 issuers. |
| 0:15–1:00 | **Replay** of Jan–Oct 2026: events stream in; alerts appear in the feed as Kernel processes each one. |
| 1:00–1:45 | Open the **Verizon redemption alert**: notice → proposed change → verifier check → maturity wall before/after → rank change. Then the **Duke term-loan alert**: the critic finds debt missing from the bond list. |
| 1:45–2:15 | Issuer page: maturity wall, rate-reset cost, SAM-verified chart from the issuer deck, holdco/opco panel. One drill-down question answered with the trace. |
| 2:15–2:40 | Meta pipeline panel (Spark Open classifies and verifies, Spark extracts, Wonder writes the brief, SAM reads charts); cost and latency per alert. |
| 2:40–3:00 | Eval: replay recall/precision vs. the plain-RAG baseline; live URL; close. |

**Closing line:** *"Kernel doesn't wait to be asked. It watches, verifies and tells you what matters."*

**Rules:** pre-ingest the corpus; cache replay runs; record a fallback; citations on screen; never a chat-only shot; show only hackathon-built features. Produce a separate **60-second cut** for X (the Verizon alert is the hook).

## 15. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Judges read it as basic RAG | Open video on the trace; README first line; plain-RAG baseline in eval |
| Scope overrun | Capability priority fixed; drill-down questions and subordination panel are the first cuts |
| Watchers miss or misparse events | Replay uses a curated event list with source documents; live watchers are a bonus on top |
| Alert noise | Materiality rules in code, shown in the UI; precision measured in replay |
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

Kernel Credit Lab addresses a problem I know firsthand: credit analysts monitor many issuers across filings, bond documentation, announcements and rates, and important changes are noticed late or missed. Kernel is a proactive monitor for USD investment-grade bonds that watches these sources itself.

When something happens — a bond redeemed early, a tender offer, a new term loan in a quarterly filing, a move in Treasury yields — Kernel reads the source, proposes the change to its structured, cited data, recomputes each issuer's maturity wall, rate-reset cost and coverage in deterministic Python, has an independent open-weight model verify every claim, critiques itself, and raises an alert only when the change is material, with before/after numbers and the full trace. Replayed over real events from January to October 2026, it catches [measured results].

Kernel runs on a hybrid Meta stack: Muse Spark Open classifies events and independently verifies claims (a path that can be self-hosted by firms whose documents cannot leave their perimeter), Muse Spark extracts and reasons, Muse Wonder writes the daily brief, and SAM 3.1 locates charts in issuer presentations so their bar geometry can verify every value Spark reads. Built with FastAPI, PostgreSQL/pgvector, a background event pipeline and a Next.js dashboard, with CI, an evaluation report and a deployed instance.

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
| propose_state_change | Structured diff to an instrument with evidence (applied after verification) |
| snapshot_metrics, diff_metrics | Before/after metrics for an event |
| assess_materiality | Rule results and alert decision |
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

Visible **Meta pipeline panel** per alert: Spark Open (classify event) → Spark (extract, propose change) → Kernel tools → Spark Open (verify) → Spark (alert) / Wonder (daily brief); SAM 3.1 for charts on issuer pages. For each step: model, provider, task, input and output summary, tokens, cost, latency, citations, confidence.

## 13. Development tooling (honest attribution)

Cursor is the primary IDE. Muse Code is used for **3–4 specific, documented tasks** (e.g. the model client and validation layer, the state-machine refactor, edge-case tests for financial tools, the eval runner), each linked to commits in `META_TOOLING.md`. No blanket claims.

---

# Part C — Implementation Plan (Oct 27 – Nov 4)

Times are Madrid unless noted (ET sessions are 17:00 Madrid until US clocks change on Nov 1, then 18:00). Day-by-day status lives in [PROGRESS.md](PROGRESS.md). Production signals (CI, structured logs, `ModelCall`/`ToolCall` tables, error states) are built into each day, not deferred.

| Day | Deliverables | Exit condition |
|---|---|---|
| **Tue Oct 27** ✅ | Fresh public repo; Docker Compose (API, Postgres+pgvector, Redis, worker, web); Cursor rules; `.env.example`; CI skeleton; unified model client; **smoke tests for Spark, Wonder, SAM 3.1 (on a prospectus page), Spark Open on Baseten**; apply for Baseten credits; Agents 101 (17:00 Madrid) | All models answer with confirmed IDs; CI green; key only in `.env` |
| **Wed Oct 28** | Direction change to proactive monitor (this document); full schema + migrations incl. `Watchlist`, `Event`, `StateChange`, `Alert`, `MetricSnapshot`, loans, `CuratedSource`; seed as a **31 Dec 2025 snapshot** (Duke year-end debt details; Verizon pre-tender amounts; no 2026 loans or convertible); seed loader with validation; Agents 102 (17:00) | Universe loaded and queryable |
| **Thu Oct 29** | EDGAR submissions + XBRL client; FRED client; filing ingestion with hashes; **curated replay event list (Jan–Oct 2026) with source documents** | Filings, metrics, yields and replay events stored |
| **Fri Oct 30** | HTML parsing, chunking, embeddings; Spark Open event classification; Spark extraction with citations; `propose_state_change`; chart reader on issuer charts | A redemption notice and a 10-Q debt note produce verified, cited state changes |
| **Sat Oct 31** | Deterministic tools + tests (maturity wall incl. loans, rate reset, pro-forma coverage, ranking, metric diff, materiality) | All calculation tests pass in CI |
| **Sun Nov 1** | Event-driven state machine end to end; verifier and critic; **replay runner** through the worker queue; alerts persisted | Replay produces the expected alerts (Verizon redemption, Duke loans) |
| **Mon Nov 2** | Dashboard: alerts feed (confirm/dismiss), alert detail with before/after and trace, issuer pages, daily brief; drill-down question (thin) | Replay → alerts → trace works in the browser |
| **Tue Nov 3** | Wonder daily brief (cached); eval (replay recall/precision, plain-RAG baseline); live watchers on a schedule; deploy; README | Live URL works; eval numbers measured |
| **Wed Nov 4** | Record 3-minute video and 60-second X cut; final checks; **submit in the evening Madrid time**; post on X | Submitted well before 03:00 Nov 5 Madrid |

### Security rule (non-negotiable)

API keys (Meta and Baseten) live only in `.env` or deployment secrets. They are never hardcoded, committed, exposed to the browser or pasted into chat. Model calls happen only from the backend.

### README skeleton

1. One-line pitch + GIF of the replay producing alerts, with one alert opened to its trace.
2. Live demo URL, video link, CI badge.
3. Architecture diagram.
4. Evaluation results (measured), including plain-RAG baseline.
5. Meta AI tooling (hosted + open-weight roles; Muse Code tasks with commit links).
6. Built during the hackathon (commit history, dates).
7. Data sources, licensing and limitations.
8. Local setup (one command).
9. Disclaimer: research support, not investment advice.
