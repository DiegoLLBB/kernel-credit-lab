# Kernel Credit Lab — Complete Project Document

*What this project is, how it works in detail, architecture, modules, dashboard, and everything around it.
Compiled 31 Oct 2026 from `docs/CONTEXT.md`, `docs/ISSUERS.md`, `docs/PROGRESS.md`, `docs/DEPLOY.md`,
`docs/META_TOOLING.md`, the codebase, seed files, and the latest replay evaluation.
For the day-by-day build log see [PROGRESS.md](PROGRESS.md); for product/hackathon context see [CONTEXT.md](CONTEXT.md).*

---

## 1. What Kernel is (in one page)

**Kernel Credit Lab (Kernel)** is a **proactive, agentic credit monitor for USD investment-grade (IG) corporate bonds**.

An analyst sets a **watchlist once**. Kernel then watches SEC filings, issuer announcements, Treasury yields, and the maturity calendar **on its own**. Each event triggers a constrained agent run that:

1. reads the source document,
2. updates a structured, cited knowledge base,
3. recomputes the affected credit metrics with deterministic Python (never with the LLM),
4. has an **independent model verify every claim**,
5. critiques and corrects itself,
6. raises an **alert only when something material changed**.

**One-sentence pitch:** *Kernel watches your credits so you don't have to: it notices what changed, works out whether it matters, and shows its work.*

**Closing line (demo):** *"Kernel doesn't wait to be asked. It watches, verifies and tells you what matters."*

### Problem → solution

Bond terms live in prospectuses and 424B filings; financials in SEC filings and XBRL; events in 8-Ks and press releases; macro context in separate systems. Monitoring is manual, so changes are noticed late or missed, and the analysis behind them is hard to reproduce. Generic LLM tools answer questions but do not watch, keep structured state, or verify calculations.

Kernel's answer:

| Layer | What Kernel does |
|---|---|
| Watch | Watchlist + scheduled watchers (EDGAR, issuer newsrooms, FRED yields, maturity/par-call calendar) |
| Understand | One agent run per event inside a constrained state machine: read → update state → recompute → verify → critique → materiality |
| Compute | Deterministic financial tools for every number (maturity wall, rate-reset cost, pro-forma coverage, ranking) |
| Verify | Independent verifier (different model) + critic + quote/evidence/alert-rule checks that cannot be skipped |
| Tell | Material alerts + daily brief, each with before/after numbers, sources, and the full run trace; analyst confirms or dismisses |
| Drill down | Ad-hoc questions from an alert or issuer page, answered by the same engine with the same trace |

### Why it is not "Basic RAG"

"Basic RAG Applications" is an explicit anti-project of the Meta Global AI Developer Hackathon (Startup track). Kernel's defense is architectural and measured:

1. **Persistent structured state** — issuers, bonds, call terms, maturities, events, filings, metrics, runs live in Postgres. The vector index is a retrieval aid, never the system of record.
2. **Hybrid evidence retrieval** — SQL over structured facts, exact document lookup, pgvector semantic search.
3. **Tool-using reasoning** — Python computes everything. The LLM never does bond math.
4. **Independent verification + provenance** — every material claim links to a source; every number links to a tool call; claims are checked by a different model from the one that wrote them.
5. **Closed loop, triggered by events** — detect → update → compute → verify → critique → revise → alert, visible in the UI.
6. **Proactive** — Kernel decides when something is worth attention; it does not wait for a prompt.
7. **Measured proof** — the eval report includes a plain-RAG baseline (same questions over chunk retrieval alone, scored on numeric accuracy, citation coverage, missed facts).

### Hackathon framing

- **Event:** Meta Global AI Developer Hackathon (remote), building 27 Oct – 4 Nov 2026. Submission deadline Wed Nov 4, 9:00 PM ET.
- **Track:** Startup ($250K / $150K / $75K / $50K / $25K). Secondary targets: Best Agentic Use-Case ($5K), Best Build on Meta Open Weight Models ($10K), Most Viral / Community Vote via a 60-second X cut.
- **Judging:** Stage 1 pass/fail (fits a problem statement + reasonably uses Meta Model API / open-weight models). Stage 2 (0–10): Impact 30%, Use of Meta AI Tooling 25%, Technical Depth & Execution 20%, Demo 25%. Stage 3: top-5 live final.
- **Rules that shaped the build:** public repo, new work only (fresh repo, frequent commits, "Built during the hackathon" README section), 3-minute demo video + code + live publicly reachable instance, no unlicensed data (ratings, paid market data, earnings-call audio are out).

---

## 2. Users, scope, non-goals

**Primary users:** IG credit analysts, fixed-income researchers, IG bond investors, treasury / capital-structure teams, financial-intelligence platforms.
**Design user:** a credit analyst monitoring a universe of USD IG issuers for changes in refinancing cost, capital structure, and credit quality.

**Scope (v2, fixed):** complete across the research workflow, not across the bond market.

| Dimension | Scope |
|---|---|
| Asset class | USD investment-grade corporate bonds |
| Geography | United States |
| Issuers | 9 entities (Duke Energy Corp holdco + 3 opcos, Oracle, Verizon, Boeing, Intel, Amazon) |
| Instruments | ~33 instruments in seed (all 2026–2029 Duke debt + named bonds/loans for others), 21 bonds + 2 term loans originally curated, expanded for the wall |
| Documents | Latest 10-K/10-Q, recent 8-Ks, earnings releases, selected 424B supplements, 5 investor decks (for charts) |
| Events | Watched going forward; **replay window Jan–Oct 2026** for demo + eval (29 real events) |
| Macro | Treasury curve (FRED DGS1–DGS10), policy rate, inflation, FX (DEXUSEU/DEXUSUK/DEXCAUS); spread context only where data rights allow |
| Forward view | Maturities and par-call windows |

**Capability priority:**

| # | Capability | Status |
|---|---|---|
| 1 | Event monitor + alerts feed (watchers, per-event run, materiality, alerts, daily brief) | Flagship — the product |
| 2 | Refinancing-pressure engine (maturity wall, rate-reset cost, pro-forma coverage, ranking) | Core — recomputed on every event |
| 3 | Replay mode (run monitor over real Jan–Oct 2026 events) | Core for demo + eval |
| 4 | Structural-subordination view (holdco vs. opco, Duke) | Thin — issuer-page panel |
| 5 | Ad-hoc drill-down questions | Thin — from alert/issuer page |

**Non-goals:** global/multi-asset coverage; live executable prices; investment recommendations; trading/portfolio optimization; rating histories or licensed ratings data; auth/billing/multi-tenancy/Kubernetes; live dependence on Wonder or SAM during the judged demo (replay runs are pre-computed and cached); real-time streaming or paid news feeds (watchers poll public sources on a schedule).

---

## 3. Product capabilities in detail

### 3.0 Alerts feed + daily brief (flagship)

A feed of **material** findings across the watchlist, newest and most important first. Each alert card shows: what happened (with source document), what Kernel changed in its data, before/after metrics (e.g. 2028 maturities −$1.25bn; pressure rank 3 → 5), confidence, and a link to the run trace. Analysts **confirm** or **dismiss**; dismissals are recorded (audit actor `analyst`). A **daily brief** (Muse Wonder) summarizes overnight changes. Non-material events are logged on the issuer timeline without an alert.

### 3.0b Watchlist + watchers

The analyst adds issuers to a `Watchlist`. A background worker (ARQ + Redis, on a schedule; Cloud Scheduler daily job in live mode) polls:

- EDGAR new filings per issuer (8-K, 10-Q, 10-K, 424B),
- issuer announcement pages / newsrooms (per-issuer config, e.g. Verizon Financial & Investor RSS; robots.txt respected, ≤2 req/s),
- daily Treasury yields + FX (FRED),
- the maturity / par-call calendar (generated by code).

Each detection enqueues **one agent run per event** through the same queue that replay mode uses.

### 3.1 Issuer 360

Business profile; capital-structure summary **by issuing entity** (holdco vs. opco tree); bond inventory; maturity wall; par-call windows; rate-reset exposure; financial trend panel; event timeline; recent filings; cited credit thesis; data gaps; research-run history.

### 3.2 Bond / instrument view

Terms (coupon, maturity, make-whole spread, par-call date); seniority, security, issuing entity; refinancing context (coupon vs. current reset yield); related documents; comparables within the universe; source-linked evidence. Loans share the `instrument` table (`instrument_type = 'loan'`, floating-rate fields `rate_type/reference_rate/spread_bps`, `currency` with FX conversion).

### 3.3 Drill-down questions (secondary)

From an alert or issuer page the analyst can ask a follow-up ("why did Verizon's rank change?"). The same agent engine answers with the same tools, verifier, and trace.

### 3.4 Run trace (observability as a product feature)

For every step: model or tool, input summary, output summary, tokens, cost, latency, citations, confidence. Total cost + latency per run. This is how judges see production craft **in the product**, not just in logs. Includes every check, refusal, escalation, and human action (append-only `audit_entry`).

### 3.5 Citation + evidence layer

Each claim shows: source document, page/region, extracted field, confidence, linked calculation (`ToolCall`), data timestamp, limitations. Strict separation: a **fact** is what the document says (exact quote); the **claim** is what it means for the credit.

---

## 4. How the monitor works (IG-correct)

### 4.1 Event types and what Kernel does

| Trigger | Source | Agent action | Real 2026 example |
|---|---|---|---|
| Redemption / call notice | 8-K, press release | Mark bond redeemed; recompute wall + pressure | Verizon redeems $1.25bn 4.329% 2028 (announced 20 Aug, settled 21 Sep) |
| Tender offer results | 8-K, press release | Reduce outstanding | Verizon buys back 55% of 2.100% 2028 ($1,143M, June) |
| New debt in periodic filing | 10-Q/10-K debt note | Add missing instruments | Duke Carolinas + Progress $1.3bn term loans due Mar 2027 |
| New issuance | 424B, 8-K | Add bond; update assumptions | Intel $6.5bn notes (Q2), Oracle $25bn, Amazon USD/EUR/CAD/GBP tranches |
| Rate move | FRED daily yields | Recompute rate-reset cost for all maturing debt | Treasury curve Jan–Oct |
| Calendar | Bond data (by code) | Flag maturities / par-call windows entering 6/12-month horizons | Duke Florida 3.200% due Jan 2027 |
| Credit facility / equity | 8-K, prospectus | Log or alert by judgement rules | Boeing $3bn revolver (log); Intel $15–20bn equity (judgement alert) |

Replay source discipline: the agent sees **only what a monitor knew that day** — issuer, discovery date (`available_from`), and source documents published by then. Titles, explanations, and expected outcomes live only in `ReplayExpectation` and are never shown to the agent. The replay advances **one day at a time** so calendar events reflect what the agent already learned. Rate and calendar-maturity events are completed **by code** (no model call).

### 4.2 The refinancing-pressure engine (recomputed on every event)

For IG issuers pressure is mostly **rate-reset cost**, not default risk: 2–3% coupons issued in 2017–21 mature into a materially higher-rate environment.

**Two-layer maturity wall** (`app/tools/maturity.py`, `maturity_wall()`):

1. **Backbone — complete:** each issuer's scheduled principal repayments for the next five years + thereafter, from its 10-K (XBRL `LongTermDebtMaturitiesRepaymentsOfPrincipal*`), per registrant (Duke Carolinas/Progress/Florida report their own; Duke Corp's is consolidated).
2. **Named detail:** verified instruments (bonds, loans, convertibles) with coupons, dates, call terms, sources. For the Duke family, all 2026–2029 instruments are named.
3. **Unattributed remainder:** backbone minus named, per year, shown as "other scheduled maturities (per 10-K)". Its reset cost uses the issuer's average cost of debt (interest ÷ average total debt), labeled as estimate.
4. **Bucketing:** maturities assigned to the issuer's **fiscal-year** buckets (`bucket_for()` — half-open `(prev_end, end]`, first bucket open-left, undated "after" bucket; handles Oracle Jun–May, Intel late-Dec). Instruments already matured/redeemed/repaid at the evaluation date are excluded; `instrument_state_at()` undoes applied `StateChange`s after date `d` so any past date can be reconstructed.
5. **Reconciliation check (code):** named ≤ backbone in every year; breach or remainder change after an event is a critic finding (how missing debt is caught). Schedule set frozen at 10-K date; buckets return named-active / repaid-since-schedule / new-since-schedule / remainder / total; non-USD converted at FRED FX on event date (`NonUsdAmountError` if unconvertible).

Per issuer:

- **Maturity concentration:** debt maturing in 24 months (backbone) as share of total debt and liquidity.
- **Rate-reset cost:** named: Σ amount × (current Treasury at matching tenor + spread assumption − coupon); remainder: same with average cost of debt in place of coupon. Reported as incremental annual interest, named/estimated split shown (`calculate_rate_reset_cost`).
- **Coverage impact:** pro-forma interest coverage after reset vs. current (`calculate_proforma_coverage`).
- **Ranking:** transparent composite score, weights shown in UI (`rank_refinancing_pressure`).

**Call features:** IG bonds carry **make-whole + par call 1–3 months before maturity**. Kernel models the par-call window as the effective refinancing date. No HY-style step-down schedules.

### 4.3 Materiality (`app/agent/materiality.py`)

Applied per 2026–2029 wall bucket (decision 8 — a 24-month-only rule would miss 2029 issuance). Initial rules, configurable and shown in UI:

- any FY2026–FY2029 bucket changes by ≥10% or ≥$500M;
- rate-reset cost changes by ≥10%;
- issuer moves ≥2 places in pressure ranking;
- debt appears not in bond list (issue >7 days before discovery — always material; new issuance judged by bucket rules only);
- verifier/critic contradiction with Kernel's own data (always material);
- labelled judgement upgrades allowed except pure long-dated adds outside 2026–2029 (refused with "log it").

Everything else → issuer timeline (`logged`).

### 4.4 Replay mode (demo + evaluation)

Kernel runs the monitor over **real Jan–Oct 2026 events in date order**, starting from a **31 Dec 2025 snapshot** (year-end filings + issuer debt reports). Output = the alerts feed Kernel would have produced. Deterministic demo + eval: each expected catch is a test case, and **state after replaying to 30 Jun 2026 must match independently verified 30 Jun figures** (Duke debt details, Verizon debt portfolio). Latest measured result: **precision 1.00, recall 0.86** (12/14 expected alerts; 28/29 events completed; both misses Intel — April refinancing logged as not material, August equity raise expected to alert by judgement). Cost ≈ $0.25 (press release) to ≈ $1 (10-Q) per event; full 29-event replay ≈ $15; total replay spend ≈ $60 of $150 budget.

### 4.5 Structural subordination (issuer-page panel)

For the Duke family: compare a utility opco's **first-mortgage bonds** vs. the holdco's **senior unsecured notes** — same family, different claims on assets/cash flow. Pipeline: extract terms/security/issuing entity → normalize seniority/security/guarantees/call/covenants → retrieve entity financial context (holdco debt share, opco dividend dependence) → side-by-side matrix, every term cited. (IG covenant packages are thin — liens, sale-leaseback, merger — so the matrix is structural, not covenant-heavy.)

---

## 5. Agent architecture (verifiable agents)

Adopts Meta's Agents 101/102 workshop patterns (`meta-models/legal-agent-workshop`), re-implemented and credited in README. **Principle: the model reasons, the checks decide.** Checks live **inside the tools at fixed points**, never as optional tools the model could skip.

### 5.1 The per-event loop (`app/agent/loop.py`)

One agent run per event (or drill-down question). Each turn the model picks **exactly one tool**; code runs it; result goes back. Plain-text answers refused (3 consecutive no-tool turns → `paused`). Progress saved every turn; runs stop/resume (`run_event` / `resume_run`; stored Meta response IDs expire mid-run, so the **full transcript is always resent**, compacted for the model but complete in storage). Uses Meta Model API **`/responses`** with tool definitions. Statuses: `running → alerted | logged | needs_input | paused` (turn/cost/token limit or API failure; resumable). Budgets: 40 turns (50 for 10-Q/10-K or >20-page docs), 600k tokens (1.2M long), $2/run default, wall-clock 600s; budget warnings injected into tool results; transcript compaction keeps the 3 most recent read/search outputs in full and stubs older ones (stubs list already-recorded facts per page).

### 5.2 Tools and the checks inside them

| Tool | What it does | Check inside (cannot be skipped) | On failure |
|---|---|---|---|
| `set_checklist` | Agent lists what this event must settle, each item + evidence needed | **Coverage check:** vs. required items per event kind (e.g. redemption must cover instrument/amount/date/price/effect). In replay also scored vs. hidden expectation (scored, never refused — refusing would leak the answer) | Refused with gap feedback; accepted after 3 tries, score kept |
| `list_documents`, `read_document`, `search_document` | List/read event sources (8-K, release, 10-Q note, 424B). Text extracted once (HTML parsing; PDFs/images via Spark), cached by SHA-256. EDGAR index URLs resolve to primary doc + EX-99.x exhibits; `search_document` finds terms with snippets without counting as a read | — | — |
| `get_issuer_debt` | Point-in-time records + maturity wall (so the agent can name the bond and notice missing debt) | — | — |
| `record_fact` | One evidence per checklist item: document, location, exact quote, claim | **Quote check (code):** quote must appear word-for-word in cached text of a read document; min length; coupon+maturity/CUSIP identification; chart facts only from verified `ChartReading`s; max 3 facts/item | Refused: "quote not found on that page" (+ parsed values, citable facts) |
| `propose_change` | Structured change (mark redeemed/matured/repaid, reduce outstanding, add loan/bond) | **Evidence check (code):** every changed field cites a recorded fact; internal consistency (reduction ≤ outstanding; maturity after issue; table "(in millions)" scale; month-precision maturities; FX for non-USD) | Refused with problems |
| `compute_metrics` | Deterministic tools (wall incl. loans, reset cost, coverage, ranking) before/after proposed changes (rolled-back savepoint) | Pure code; every output is a `ToolCall` | — |
| `read_chart` | Returns verified chart values (SAM+Spark+geometry); only verified values are citable | Verified-only gate | Refuses unverified |
| `request_input` | Asks a person for missing evidence on one item | Allowed only if item real + unproven + every available document read (long-doc escape after 3 nothing-new searches on >20-page filing) | Refused with reason |
| `submit_alert` | Files finding: summary, one finding per item + citations, before/after metrics, materiality | ① **Alert rules (code):** every item settled/escalated; every citation matches a recorded fact; **every number matches a `compute_metrics` output** (with table scale); materiality recomputed by code. ② **Claims check (Muse Spark Open, independent):** does each finding say more than its quotes+numbers support? Fails closed | Refused with problems; **3 refusals → `needs_input`** |

On `submit_alert` pass: changes applied (`StateChange` applied + `effective_date`), `MetricSnapshot` stored, alert published (material) or timeline-logged (not material). Analyst **confirm/dismiss** is the sign-off; dismissal with note can be sent back.

### 5.3 Models in the loop

| Role | Model |
|---|---|
| Event classification, document triage (bulk) | Muse Spark Open |
| Agent loop (tools, reading, facts, changes, alert draft) | Muse Spark |
| Coverage check + **claims check** (independent of agent model) | Muse Spark Open (Muse Spark fallback; judging model recorded on alert) |
| Daily brief across alerts | Muse Wonder |
| Charts in decks | SAM 3.1 + Spark, verified by geometry |

### 5.4 Records and limits

- **Append-only audit log** per run (`audit_entry`, DB trigger-enforced): every tool call, check, refusal, escalation, human action with actor (`agent`/`verification`/`analyst`) + timestamp.
- **Evidence vs. interpretation** kept in separate fields everywhere; **counter-evidence** checklist item per run (e.g. "is any part conditional?").
- **Limits:** max turns/tokens/cost, max facts per item, citations per finding, bounded retries before escalation. No LLM-generated prices/ratings/numbers.
- **Testing:** fake model drives the loop in unit tests (no API calls) so gates are tested deterministically in CI.

### 5.5 The critique moments (real, not staged)

Seed = pre-replay snapshot with the stale facts real analysts had: **Verizon 4.329% 2028** listed outstanding → replay delivers redemption notice → Kernel removes it and recomputes ($13,032M → $11,782M FY2028 in the flagship run; 19 turns, checklist refusals corrected, claims check refused twice before passing). **Duke term loans** absent → replay delivers 10-Q → critic notices bond-vs-filing gap (DEC $1bn 364-day loan, $1bn drawn, 4.522%, Mar 2027; FY2027 $67M → $1,067M; 35 turns over ~150 pages via search/read/compaction). The trace shows checklist, quotes, proposed change, every check (passed/refused), before/after numbers.

---

## 6. Technical architecture

```
Next.js dashboard (alerts, brief, issuer pages, trace)
        │  REST /api/*, /health*
        ▼
FastAPI API  ──►  Agent orchestrator (state machine / per-event loop)
        │                    ├─► Deterministic tools (Python)
        │                    └─► LLMClient ──► Meta Model API / Baseten
        ▼
Postgres 16 + pgvector  ◄── ARQ worker (ingestion, runs, replay) ◄── Redis
        ▲
  FRED / EDGAR / newsrooms / decks
```

**Backend:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0, Alembic (19 migrations 0001–0019), PostgreSQL 16 + pgvector (+ Cloud SQL Postgres 18 in prod), Redis, ARQ, pandas, pdfplumber, pytest, httpx.
**Frontend:** Next.js App Router, TypeScript, Tailwind, shadcn/ui, TanStack Query, Zod, ECharts (`MaturityWallChart`).
**Ops:** Docker Compose one-command locally (db, redis, api, worker, web); GitHub Actions CI (ruff, `alembic check`, pytest incl. Postgres/Redis integration, web lint/typecheck/build); public read-only instance on Google Cloud (Cloud Run api+web scale-to-zero max-2, Cloud SQL via socket, Firebase Hosting routing + TLS + custom domain `credit.kernelterminal.com`, Secret Manager, Artifact Registry, keyless GitHub deploys; Cloud Run jobs `kernel-seed`/`kernel-replay`/`kernel-decks`; $5 budget alert). Live: https://kernel-credit-lab.web.app. Firebase Hosting 60s timeout → long runs start async + poll.

---

## 7. Data model (Postgres is the source of truth)

| Entity | Purpose |
|---|---|
| `Issuer` | Company/obligor; `parent_id` links opcos → holdco; cik/ticker/sector |
| `Instrument` | Bond or loan (`instrument_type`), issuing entity, coupon/rate_type/reference_rate/spread, maturity (+precision day/month), issue date, amount, currency, seniority/security/CUSIP, status (active/redeemed/matured) |
| `CallTerms` | Make-whole spread + par-call date (IG-style) |
| `Filing` | SEC/issuer document: accession, url (unique), filed_at, hash, title, `body_text`, `content_bytes` (deck PDFs for SAM) |
| `DocumentChunk` / `DocumentRegion` | Searchable text segment + located region (page, label, confidence) |
| `ChartReading` | SAM+Spark extraction: filing/page/bbox/title/unit/categories/values/verified + verification_detail + crop/overlay PNGs |
| `Metric` / `RateObservation` | Time-series financial/macro metrics; daily FRED yields/FX |
| `Event` | Typed corporate/macro event: type/date/detected_at/source filing/status (pending/processing/processed/failed)/run |
| `EventFiling` | Many-to-many event↔source filings |
| `Loan` | Modeled as `Instrument` loan (same table) |
| `Watchlist` | One row per watched issuer |
| `StateChange` | Proposed/applied/rejected diff + evidence + verifier result + run + `effective_date` (powers time travel) |
| `ChecklistItem` | What a run must settle: key/question/evidence_needed/status (open/proven/escalated) |
| `Fact` | Recorded evidence: filing/location/exact quote/claim/item (+ chart_reading type with `{chart_id,category,value}` meta) |
| `CheckResult` | Each in-tool check outcome (coverage/quote/evidence/alert-rules/claims): passed/problems/attempt |
| `AuditEntry` | Append-only log (agent/verification/analyst, action, detail, timestamp) |
| `Alert` | Material finding: event/issuer/run/title/summary/findings/before-after metrics/materiality reasons/claims_check/status (new/confirmed/dismissed) |
| `MetricSnapshot` | Per-issuer computed metrics at a point in time (before/after) |
| `MaturitySchedule` | 10-K repayment schedule per issuer+as-of (basis consolidated/registrant, buckets, source) — the wall backbone |
| `Covenant` | Extracted contractual term |
| `CuratedSource` | Provenance per hand-entered seed field |
| `ResearchRun` (`AgentRun`) | Agent run (event/question trigger): status/result/coverage/cost/latency/turns |
| `AgentStep` | Individual state execution |
| `ToolCall` / `ModelCall` | Deterministic execution (I/O) / model call (provider/model/task/prompt version/tokens/cost/latency/status) |
| `Citation` | Claim→source link (chunk/region/tool call) |
| `EvaluationCase` / `ReplayExpectation` / `ReplayEvaluation` | Benchmark reference (never agent-visible) / persisted eval summary (`per_event`, precision/recall/cost/git_sha/prompt_version) |

**Rule:** relational model is the source of truth; vector index is a retrieval aid.

---

## 8. Backend modules (`backend/app/`)

| Path | Responsibility |
|---|---|
| `agent/loop.py` | Per-event `/responses` loop, budgets, compaction, pause/resume, `run_event`/`resume_run`, CLI |
| `agent/tools/` | Gated tools: `checklist.py` (`set_checklist`+coverage), `documents.py` (list/read/search), `facts.py` (`record_fact`+quote check), `changes.py` (`propose_change`+evidence), `metrics.py` (`compute_metrics`), `alert.py` (`submit_alert`+rules), `charts_tool.py` (`read_chart`), `input_request.py`, `debt.py` (`get_issuer_debt`) |
| `agent/` | `checklists.py` (per-kind required items), `claims.py` (Spark Open claims check, fail-closed), `materiality.py` (bucket rules), `run.py` (run lifecycle/supersede), `registry.py`/`base.py` (dispatch), `audit.py`, `normalize.py`/`text.py` (quotes/pages), `chart_evidence.py`, `fake.py` (tests), `prompts/` (`event_v1`–`v10`, `claims_v1`–`v2`; current `PROMPT_VERSION`) |
| `tools/maturity.py` | `bucket_for`, `instrument_state_at`, `maturity_wall` (two-layer wall + FX + breach) |
| `tools/charts.py` + `charts/` | Verifiable chart reader (SAM→Spark→geometry `verify_proportional`, `merge_split_charts`, `assign_bars`, crops/overlays) |
| `tools/fx.py` | FX conversion via `RateObservation` (`latest_on_or_before`, never future data) |
| `llm/client.py` | Unified Meta/Baseten OpenAI-compatible client: `chat`, `responses` (full-transcript, sticky/fallback `tool_choice`, store=false), `structured_json` (strict-schema conversion, one repair attempt), retry-only-transient with backoff + Retry-After, `ModelCallRecord` per call, pricing/token ceilings |
| `llm/sam.py` | SAM 3.1 via streamed `/v1/responses`, box/mask token parsing |
| `llm/recorder.py` | Model-call persistence |
| `sources/edgar.py` | Submissions + overflow pages, filing index (`DocumentRef`), HTML→text (one line per table row), idempotent upsert, declared User-Agent + real email, ≤8 req/s, retries; `fetch_filing_text` sets SHA-256 |
| `sources/newsroom.py` | Per-issuer RSS/announcement config, robots check, ≤2 req/s, releases as filings; Verizon feed is a short rolling window → must poll daily (May/Jun tender releases already aged out; replay pins URLs) |
| `sources/fred.py` | DGS1–DGS10 + DEXUSEU/DEXUSUK/DEXCAUS, key redacted, missing days as None, `RateObservation` upsert |
| `ingest/replay.py` | Replay list → `Event` rows day-by-day; sources fetched + stored; maturity/rate events; `agent_visible_payload` (leak-free); ARQ job + CLI |
| `ingest/pdf.py` | PDF/HTML→text, table-aware |
| `replay/runner.py` | Day-by-day driver: `run_replay`/`process_one_event`, resumable, cost-capped, `--only`/`--rerun` (supersede), by-code rate/maturity completion, per-event isolation, JSON log + console lines |
| `eval/replay_eval.py` | Precision/recall vs. expectations, change matching (±1%), end-state checks, coverage, variance (multiple runs), cost/tokens/turns, markdown+JSON reports, `ReplayEvaluation` row for the dashboard |
| `api/` | `alerts.py` (feed, detail with quotes/before-after/applied changes/claims, confirm/dismiss+audit), `issuers.py` (detail + walls + timeline), `runs.py` (trace), `documents.py`, `charts.py` (readings + PNGs), `replay.py` (status/eval/control), `health.py` (liveness/readiness), `schemas.py`, `common.py` (deltas, paging) |
| `models/` | `credit.py`, `verification.py`, `trace.py` |
| `core/logging.py`, `db.py`, `config.py`, `main.py`, `worker.py` | Structured JSON logs (request/run/step IDs); session management; settings (models, pricing, guardrails, SEC/FRED); FastAPI app; ARQ worker |
| `seed/` | `snapshot_2025-12-31.json` (9 issuers, 33 instruments, 9 schedules, watchlist, sources), `replay_events_2026.json` (29 events + end-state checks), `decks.yaml`, `newsrooms.yaml` |
| `scripts/` | `seed_job`, `run_replay`, `decks_job`/`fetch_decks`/`process_decks`/`dump_charts`, `run_one_event`, `load_seed`, `smoke_models`, `chart_demo` |
| `alembic/versions/` | 0001 run-trace → 0019 replay-evaluation (incl. verification tables, instrument key/currency/maturity-precision/effective-date, filing-url uniqueness, rate observations, replay expectations, agent runs, alert claims, chart readings) |

---

## 9. Deterministic finance tools (the LLM never does math)

All in `app/tools/` + `app/agent/tools/metrics.py`, unit-tested in CI: `calculate_maturity_wall` (two-layer, fiscal buckets, remainder, breach), `calculate_rate_reset_cost` (named + estimated split), `calculate_proforma_coverage`, `calculate_leverage_trend` / `calculate_coverage_trend`, `calculate_metric_delta`, `rank_refinancing_pressure` (visible weights), `compare_bonds` (subordination matrix), `snapshot_metrics`/`diff_metrics`, `assess_materiality`, `get_macro_series`, `get_issuer`/`get_bonds`/`get_entity_structure`/`get_maturity_schedule`/`get_financial_metrics`/`get_recent_events`, `search_documents`/`get_citations`, `render_chart`. Every output is a persisted `ToolCall`; every alert number must match one.

---

## 10. Meta model usage (hosted + open-weight hybrid)

| Layer | Tool | Purpose / status |
|---|---|---|
| Runtime extraction/planning/synthesis | Muse Spark (`muse-spark-1.3`, chat + strict `json_schema`) | Agent loop, bond/covenant/metric extraction, chart reading, memos |
| Deep research | Muse Wonder (`muse-wonder-1.0`) | Flagship refinancing scan, comparison memo, daily brief, Deep-Research toggle (10–20 runs, cached) |
| Chart vision | SAM 3.1 (`sam-3.1`, only via `/v1/responses` streamed) | Locates charts + bars; geometry verifies Spark |
| Bulk + independent verification | Muse Spark Open (`meta-models/muse-spark-open-1026` on Baseten) | Classification, chunk tagging, first-pass event classification, **coverage + claims checks** |
| Stretch | Muse Voice Transcribe (`muse-voice-transcribe-1.0`) | Earnings-call audio only with rights |
| Minimal | Muse Image (`muse-image-1.0`) | At most one labeled visual, never data/charts |
| Dev | Cursor (primary IDE), Muse Code (3–4 documented tasks in `META_TOOLING.md` with commits + review findings) | Model client/validation, state-machine refactor, finance-tool edge tests, eval runner |

**Confirmed day-1:** base `https://api.meta.ai/v1` (Bearer); Spark/Wonder strict JSON needs `additionalProperties:false` + all fields required (client converts Pydantic automatically); SAM on prospectus text/tables finds nothing ("page" = whole image) → tables come from filing HTML/pdfplumber, SAM only for charts where prompts matter ("bar chart"/"bar" work, "chart" alone returns nothing).

**Verifiable chart pipeline** (`app/tools/charts.py`, `chart_reader_v2`, ~5k tokens / ~10s per chart): SAM `"bar chart"` → chart boxes; SAM `"bar"` → bar boxes (legend swatches dropped); crop + full slide → Spark strict JSON (every bar left-to-right, null when unlabelled, never midpoint a range like "1.5%–2%"); **geometry check** (shared zero baseline; proportional heights; 8% + 2px slack; ≥2 printed values; stacked/offset/non-positive → "unverified"; single-bar → "unverified"). Result on Duke Q2 EPS slide: 8/8 within 1.3% → VERIFIED. Across 5 real decks: **26/34 verified**; 8 refusals reviewed (stacked, ranges, missing bars/values) + 2 false passes found and closed. Only verified values are stored/citable; agent `read_chart` + `record_fact` refuse the rest. Next test: dense maturity-profile charts.

**Client policy:** temperature 0 for extraction/classification/verification; validate every response; one repair attempt then fail loudly; separate extraction vs. synthesis prompts; cache by (doc hash + pages + prompt version + model); models never overwrite source-of-truth without validation; keys only in `.env`/Secret Manager, never committed/browser/pasted; model calls only from backend. Versioned prompts: `document_classifier_v1`, `chunk_tagger_v1`, `bond_term_extractor_v1`, `covenant_extractor_v1`, `financial_metric_extractor_v1`, `event_classifier_v1`, `research_planner_v1`, `tool_selector_v1`, `claim_verifier_v1`, `coverage_check_v1`, `event_agent_v1` (→ v10), `critic_v1`, `memo_writer_v1`, `deep_research_v1`.

---

## 11. Data: seed, sources, replay events

**Three tiers:** public structured data (backbone) + hand-curated bond reference (precision) + document intelligence (AI layer). EDGAR within access policy; FRED within API terms; no scraping paid providers; curated/assumed data labeled; *research support, not investment advice* everywhere. No rating history (licensed); at most issuer-disclosed ratings labeled as such. Quality rules: every fact has a source; every curated field has `CuratedSource`; every number links to a `ToolCall`; every extraction has confidence; missing data shown, never hidden; every run stores inputs/outputs/calls/citations.

**Seed = 31 Dec 2025 snapshot** (`backend/seed/snapshot_2025-12-31.json`): 9 issuers, 33 instruments (all 2026–2029 Duke debt + named bonds for others), per-field sources, 9 10-K maturity schedules, watchlist. Verified row-by-row against primary sources (Duke debt-details PDFs, 10-K debt footnotes, 424B supplements, XBRL `DebtInstrument*`, Verizon tender/redemption releases + 30 Jun portfolio, Oracle/Intel/Amazon/Boeing filings). Reconciliation: named ≤ schedule every year (e.g. Duke holdco derived $5,224M vs. $5,225M named for 2026). Gaps are expected and meaningful: Verizon names $3.3bn of a $13bn 2028 backbone; the remainder is "other (per 10-K)".

**2026 discovered in replay** (not in seed): Duke $1.5bn 3.000% convertible 2029 (12 Mar) → $1.725bn 4.125% convertible matured (15 Apr) → $2bn holdco term loan repaid early → $1bn Carolinas loan (13 Mar) + $0.3bn Progress loan (15 May); Verizon 2.100% 2028 $2,068M → −$1,143M tender (June) → 4.329% $1,250M redeemed (21 Sep); Intel bridge/notes/equity; Oracle/Amazon tranches (USD/EUR/CAD/GBP incl. FX).

**Replay list** (`replay_events_2026.json`): 29 real events Feb–Sep 2026, each with `event_date`, `available_from` (filing date = replay order), sources (8-K/10-Q/FWP on EDGAR + Verizon releases), `expected_changes` + `expected_alert` + `why` (eval-only). 15 alerts / 14 log-only. Plus weekly FRED yields/FX and code-generated maturities; two 30 Jun end-state check blocks.

---

## 12. Dashboard (`web/` — Next.js App Router + TS + Tailwind + shadcn/ui + TanStack Query + Zod + ECharts)

| Route | What it shows |
|---|---|
| `/` (Overview) | Tagline "Credit monitoring you can audit."; replay window + events-by-status + precision/recall + eval cost; latest-8 alerts (issuer, date, material badge, status chip, title → detail); watchlist grid (issuer → alert counts); system line (`/health/ready`: api/db/redis/worker + provider configured/missing) |
| `/alerts` | Full feed (filter by status/issuer, paging; `include_superseded` hides superseded/abandoned runs by default) |
| `/alerts/[id]` | Title/summary/materiality reasons/status; findings with cited facts (exact quote, claim, doc title/url, page); applied changes (action/key/field/before/after/effective date); affected buckets + before/after wall deltas; claims-check (model/provider/status/problems); confirm/dismiss (with note → audit) |
| `/issuers`, `/issuers/[key]` | Header (ticker/sector/CIK/key, parent/subsidiary links); **maturity wall** (issuer + consolidated group charts) with **as-of date slider** (weekly Jan–Oct 2026, event dates marked) — walls reconstructed via `instrument_state_at`; sortable/filterable instrument table (name/coupon/maturity/amount/status/key/currency); event timeline (date/type/status/run + alert/trace links); **verified charts** (SAM overlay/crop image, verified/unverified chip, filing/page/unit, per-bar value + geometry deviation, refusal reason; toggle unverified) |
| `/runs/[id]` | Full trace: every turn, tool call + args/result, check pass/refusals, model (tokens/cost/latency), citations, confidence, audit |
| `/documents/[filingId]` | Source text with page locators behind each quote |
| `/api/replay` (via API) | Replay status, eval summary (`ReplayEvaluation` row — what prod reads; local JSON files are for inspection), replay control, "Run agent" (async via Cloud Tasks-style jobs + rate limits) |

Design: dark ink theme, mono for numbers/dates, loading/empty/error states everywhere, graceful degradation to cached runs, accessibility labels on charts/sliders.

---

## 13. API (`backend/app/api/`)

`GET /api/alerts` (status/issuer/material/limit/offset) · `GET /api/alerts/{id}` (detail) · `POST /api/alerts/{id}/review` (confirm/dismiss + analyst audit) · issuers (list/detail `?as_of=`, walls, events) · runs/traces · documents · charts (list + PNG crop/overlay) · replay (status/eval/control) · `GET /health`, `/health/ready` (api/db/redis/worker + providers). All responses typed (`schemas.py`, Zod on the client).

---

## 14. Evaluation

| Benchmark | Size |
|---|---|
| Documents | ~20 |
| Human-verified extraction fields | ~30 |
| Replay catches (expected alerts Jan–Oct 2026) | ~8 core (29 events total: 15 alert / 14 log) |
| Drill-down questions + expected evidence/calcs | ~8 |
| Plain-RAG baseline | Same events/questions, Spark over chunks only |

| Metric | Target |
|---|---|
| Bond-term extraction precision | ≥90% |
| Financial-metric accuracy (curated) | ≥95% |
| Material claims with citation | ≥95% |
| Numeric claims traced to tool call | 100% |
| Replay recall / precision | Expected alerts raised / expected; genuinely-material / raised |
| Detection-to-alert latency | Per event (minutes) |
| Median run | <3 min (Spark) |
| Cost per run | Reported (Spark + Wonder) |
| Kernel vs. plain-RAG | Numeric accuracy, citation coverage, missed facts |

**Latest (`backend/replay_runs/latest_eval.md`, `--eval-latest`): precision 1.000, recall 0.857** — 12 TP / 0 FP / 2 FN (Intel April notes, Intel equity) / 12 TN / 1 paused (Duke 2.650% maturity, not a miss) / 0 failed. Change-match and end-state tables + per-event cost/tokens/turns/coverage/variance in the report; only measured numbers are reported, with a failure taxonomy. Note: model choices vary run-to-run (same event alerted once, logged once) — the report lists every run per event.

---

## 15. Production readiness + deployment (built in from day one)

One-command Compose; `.env.example` with no secrets; Alembic migrations + `alembic check` in CI; idempotent seed loader with validation; CI (ruff, migrations, pytest incl. Postgres/Redis, web build) + README badge; health checks (api/db/redis/worker); structured JSON logs (request/run/step/model-call IDs); retry-with-backoff + EDGAR throttling + input validation; run-trace UI with cost/latency; error/empty/loading states; deployed public read-only instance; data-source/licensing docs; "Built during the hackathon" README with commit history. See [DEPLOY.md](DEPLOY.md) for GCP specifics (Cloud Run, Cloud SQL + pgvector over socket, Firebase Hosting, Secret Manager, Artifact Registry, keyless deploys, `kernel-seed`/`kernel-replay`/`kernel-decks` jobs, cost controls, 60s Hosting timeout).

---

## 16. Demo (exactly 3 minutes), risks, repo

**Demo:** 0:00–0:15 hook ("analysts miss things — here's what Kernel caught in 2026, on its own"; 9-issuer watchlist) → 0:15–1:00 replay Jan–Oct (events stream in, alerts appear) → 1:00–1:45 Verizon redemption alert (notice → change → verifier → before/after wall → rank) + Duke term-loan alert (critic finds off-list debt) → 1:45–2:15 issuer page (wall, reset cost, SAM-verified chart, holdco/opco) + one drill-down with trace → 2:15–2:40 Meta pipeline panel (Spark Open classifies/verifies, Spark extracts, Wonder briefs, SAM charts; cost/latency per alert) → 2:40–3:00 eval (recall/precision vs. plain-RAG) + live URL + close. Rules: pre-ingest corpus, cache replay runs, recorded fallback, citations on screen, never chat-only, only hackathon-built features. Plus a 60-second X cut (Verizon hook).

**Top risks → mitigations:** read-as-basic-RAG → open on trace + README + baseline; scope overrun → fixed priority (drill-down/subordination first cuts); watcher misses → curated replay list + live watchers as bonus; alert noise → code materiality + measured precision; model/API drift → day-1 smoke tests; SAM-on-text failure → HTML/pdfplumber for tables, SAM only for charts; dense/stacked charts → "unverified" not false pass; bond-data errors → `CuratedSource` + XBRL cross-check; spread gaps → labeled assumption; budget → Spark Open bulk + hash caching + Wonder only for flagships; live failure → cached runs + deployed instance + recording; disqualification → fresh repo + commit history.

**Repository layout:** `backend/` (FastAPI/agent/tools/LLM/migrations/tests) · `web/` (dashboard) · `.github/` (CI + deploy) · `deploy/` (GCP setup) · `docs/` (this set) · `scripts/` · `docker-compose.yml` · `Makefile` (`up`, `smoke`, `test`, `seed`).

**Quick start:** `cp .env.example .env` (add keys) → `make up` (or `docker compose up --build -d`) → `make smoke` → `make test`. Web http://localhost:3000, API http://localhost:8000 (`/docs`, `/health/ready`).

**Security rule (non-negotiable):** API keys only in `.env`/deployment secrets — never hardcoded, committed, exposed to the browser, or pasted into chat. Model calls only from the backend.

*Kernel is research support, not investment advice.*
