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
- Issuer shortlist drafted in [ISSUERS.md](ISSUERS.md): 9 entities (Duke holdco + 3 opcos, Oracle,
  Verizon, Boeing, Intel, Amazon), 21 bonds + 2 term loans, two critique traps (Verizon 4.329% 2028
  redeemed 21 Sep 2026; Duke opco term loans outside the bond list). Awaiting approval.
- Seed figures verified against primary sources (EDGAR filings, issuer documents). One correction:
  Verizon 2.100% 2028 is $925M outstanding, not $3.0bn, after a June 2026 tender offer bought back 55%.
  Lesson for the product: an automated extraction of Duke's PDF was correct, but a second, naive
  column-based parse misaligned rows — exactly the class of error Kernel's verifier must catch.
- No maturity-profile *chart* found yet (AEP and Duke publish tables).

### Next (Day 2 — Wed 28 Oct)
Full domain schema and migrations (issuers, bonds, call terms, filings, chunks, regions,
chart readings, curated sources); issuer shortlist; hand-curated seed for ~25 bonds with
provenance; seed loader with validation. Agents 102 (Verifiable Agents) at 17:00 Madrid.

---

## Day 2 — Wed 28 Oct 2026

### Decisions
1. **Product direction: proactive credit monitor.** Kernel watches a watchlist (SEC filings, issuer
   announcements, Treasury yields, maturity calendar), runs an agent per event, and raises only
   material alerts with before/after numbers and traces. Questions become a secondary drill-down.
   Reason: closer to what analysts need (being told what changed), further from "basic RAG", and a
   stronger agentic story. The engine is unchanged; the trigger changes.
2. **Replay mode** over real Jan–Oct 2026 events for a deterministic demo and as the evaluation set.
3. **Seed = pre-replay snapshot**, so the real events (Duke term loans and convertible, Intel's refinancing,
   Verizon tender and redemption) are discovered by Kernel during replay rather than staged.
4. **Agent design adopts Meta's verifiable-agent patterns** (from the Agents 101/102 workshop repo
   `meta-models/legal-agent-workshop`, read on 28 Oct): one tool per turn on `/responses`; checks
   **inside** the tools at fixed points (coverage on `set_checklist`, exact-quote check on
   `record_fact`, evidence check on `propose_change`, alert rules + independent claims check on
   `submit_alert`); 3 refusals escalate to a person; analyst confirm/dismiss as sign-off;
   append-only audit log; fake-model tests. Patterns re-implemented (repo has no license) and
   credited. See CONTEXT.md §9.
5. **Two-layer maturity wall.** Bond-level data alone understated the walls badly (Boeing has
   $4.4bn due in 2027; we named one $2bn bond). Backbone = each issuer's complete 10-K repayment
   schedule (XBRL); named instruments as detail; remainder shown as "other (per 10-K)" with an
   estimated rate. Named ≤ backbone in every year is a code check; the Duke holdco reconciles
   to within $1M ($5,224M derived vs $5,225M named for 2026).
6. **Seed expanded:** all 2026–2029 debt for the four Duke entities (33 instruments total, +11) and
   10-K maturity schedules for all 9 issuers.

7. **Replay event list built** (`backend/seed/replay_events_2026.json`; see ISSUERS.md). It has
   29 real events from Feb to Sep 2026, each with its primary source, the changes Kernel should
   make and whether it should alert (15 alerts, 14 log-only). This is both the demo script and
   the evaluation set. Three of the events are critique traps: debt that appears only in a 10-Q
   (Duke Carolinas and Duke Progress term loans, the parent's early repayment). The flagship
   event is Verizon's 20 Aug redemption notice.
8. **Horizon for the reference:** additions to 2029 maturities count as material. The §8 rule
   "24-month maturities ±10% / $500M" would miss most of Amazon's and Oracle's 2029 issuance, so
   the rule should apply to each bucket of the 2026–2029 wall. To settle when writing the
   materiality tool.

9. **Issuer press releases are a source in their own right.** Verizon's cash tender (launch on 11 May,
   results on 17 Jun) and its 20 Aug redemption notice appear only on verizon.com, not on EDGAR.
   Its 8-Ks on those dates cover a separate Frontier exchange offer. Found in review on 28 Oct, when
   the 17 Jun exhibit turned out not to contain the 2.100% 2028 row. The replay sources and seed now
   cite Verizon's own releases, and Kernel's ingestion needs an issuer-newsroom source besides EDGAR.
   Built the same day (`app/sources/newsroom.py`, Verizon's Financial & Investor RSS feed). Limit:
   the feed is a short rolling window, and the May/June tender releases have already dropped out.
   Live monitoring must poll it at least daily and store each release when first seen. Replay
   is unaffected because it reads the release URLs from `replay_events_2026.json`.

### Built and reviewed
- Muse Wonder: schema (0002–0004), seed loader (0005) — see [META_TOOLING.md](META_TOOLING.md).
- Review fix: Postgres integration tests no longer leave rows behind (append-only table).

## Day 3 — Thu 29 Oct 2026

### Built and reviewed
- **All four data sources are done:** EDGAR (`8bf6f9d`, `2dc107c`), issuer newsrooms (`53b068d`), FRED
  re-recorded from the live API (`4d19e4f`, `2eadff7`), and the year-end seed.
- **Replay driver** (`e1e046f`): replay list → events day by day, with no answers visible to the
  agent (checked over the full replay), and only documents already published on each date.

### Decisions
10. **The agent sees only what a monitor knew that day:** issuer, discovery date, and the source
    documents published by then. Event types come from the source (8-K, 10-Q, press release,
    maturity, rate update). Titles, explanations and expected outcomes are for evaluation only.
11. **The replay advances one day at a time.** The agent processes each day's events before the next
    day, so calendar events reflect what the agent has already learned (e.g. no maturity event for
    a loan the agent found repaid).
12. **Pull the agent loop forward.** Extraction tools and a minimal loop come together, so one event
    (the Verizon redemption notice) runs end to end by Saturday; Sunday onwards is improvement.
13. **Deployed on Google Cloud at about $0** (`16de4b2`, `a71d19c`; see DEPLOY.md). Cloud Run (API and
    web, scale to zero, max 2 instances), Cloud SQL Postgres 18 + pgvector on the Firebase SQL Connect
    free trial (3 months), Firebase Hosting for routing and the custom domain, Secret Manager, and
    keyless GitHub deploys after CI passes. Live at https://kernel-credit-lab.web.app. Separate project
    from any other product. Render was considered ($20–37/month) and rejected on cost.
14. **CI caught model/migration drift** in 0011 (`json` vs `jsonb`, constraints vs unique indexes),
    which local review had missed because it ran `alembic upgrade` but not `alembic check`. Fixed with
    migration 0013; reviews now run `alembic check`.
15. **Coverage check in replay is scored, not refused.** Refusing a checklist because it misses an
    item from the hidden expected outcome would leak the answer through the feedback. The coverage
    check refuses only on the per-kind required items (public rules); the comparison with the
    expected outcome is recorded as a score for evaluation.
16. **First real agent run: Verizon redemption notice → `alerted`** (`5b7bc8c`). Muse Spark, real
    press release, 19 turns in 4.5 minutes: Kernel's records → documents → checklist → 7 quoted facts
    → change → before/after → alert. `vz_4329_2028` marked redeemed (21 Sep 2026); FY2028 wall
    $13,032M → $11,782M; checklist coverage 1.0 against the hidden reference. The checks refused the
    checklist once and the proposed change twice; the model corrected both. The run exposed: model
    calls not recorded (foreign-key ordering), Meta expiring stored responses mid-run (now the full
    transcript is always sent), an inactive cost ceiling (no prices configured; a token ceiling was
    added) and filing text never stored. All fixed in the same commit.
17. **SEC sources must resolve to documents, and long filings need search.** Replay sources for SEC
    filings are EDGAR index pages, so the agent would read a file list (affects ~20 of 29 events);
    and a 10-Q is ~150+ pages against a 30-turn limit. Index URLs now resolve via
    `EdgarClient.filing_documents` to the primary document plus EX-99.x exhibits (skipping XBRL,
    images, graphics, R*.htm). `search_document` finds terms with page snippets without counting
    as a read; `request_input` allows a long-doc escape after 3 nothing-new searches on a filing
    with more than 20 pages. Prompt bumped to `event_v3`.
18. **Second flagship case works on a real 10-Q: Duke Energy Carolinas → `alerted`, material**
    (`ada4ff0`, run `0729f0bf`). From the ~150-page combined Q1 10-Q, with no hint, the agent
    searched, read the debt notes, quoted the new $1.5bn 364-day term loan ($1bn drawn, 4.522%,
    March 2027), added it to Kernel's records and filed an alert. Code-computed materiality:
    "debt appears that was not in the bond list" and FY2027 wall $67M → $1,067M. 35 turns, 2
    minutes, 652k tokens. Getting there took five rounds, each driven by a real run: transcript
    compaction (input per turn was growing with every page read), per-session limits on resume,
    actionable refusals that list the run's facts, table scale ("in millions") and month-precision
    maturities in the evidence check, and compact metrics for the model. No check was loosened;
    the refusals that remained caught real mistakes (quote not on the page, a summary figure the
    agent computed itself).
    Open: the alert number check does not yet apply table scale; token use per 10-Q event is high
    (Meta pricing unknown, so check credits before a full replay).
19. **Claims check live with an independent model** (`d92e1ac`, `ecef85c`). Muse Spark Open on
    Baseten reviews every alert's summary and findings against only the cited quotes and metric
    numbers; it fails closed (no review → no alert), with Muse Spark as fallback. Verizon rerun
    (run `1ed2647c`, prompt v7): **alerted**, FY2028 $13,032M → $11,782M; the reviewer refused twice
    before passing: an unsupported link to Kernel's internal instrument key, and an overstated
    "no conditions stated" (absence inferred, not quoted). The agent reworded to the quoted
    language. First attempt failed closed because the claims call ran on a second event loop
    (tests used a fake model with no HTTP); fixed by making tools async, with a test over a mocked
    HTTP transport. Prompt v7 adds the scheduled-changes rule: certain future changes (called
    redemptions) are proposed now with the settlement date.

### Plan of record from Thu 29 Oct evening (replaces CONTEXT.md Part C day-by-day)

Full scope kept; calendar adjusted (no work on Saturday, little on Sunday). Meta budget ~$150;
measured cost ≈ $0.26 per press-release event and ≈ $0.90 per 10-Q event (Spark at $1.25 / $4.25
per M tokens, third-party figures to confirm in the Meta console), so a full 29-event replay ≈ $15.

| Day | Work |
|---|---|
| Fri 30 | Replay runner (all 29 events, day by day) + evaluation report vs the 15 expected alerts, with cost per event; API endpoints (alerts, runs/traces, issuers, walls, documents, charts, replay); SAM chart reader in the product (issuer presentations → charts → values verified by bar geometry; agent tool `read_chart`) |
| Sat 31 | Off. Full replay runs unattended on the local machine |
| Sun 1 | Check results and evaluation; load replay results into the cloud database |
| Mon 2 | Dashboard 1: alerts feed, alert detail (quotes, before/after wall, applied change, confirm/dismiss), trace (every turn, check and refusal) |
| Tue 3 | Dashboard 2: full issuer page (records, wall, timeline, verified charts with SAM overlays), replay control with evaluation score; "Run agent" with Cloud Tasks and rate limits; **live mode** (daily Cloud Scheduler job polling EDGAR and newsrooms for the watchlist); Wonder daily brief; headline, custom domain, README. Stretch: add any issuer by ticker (XBRL onboarding) |
| Wed 4 | Walkthrough fixes, 3-minute video, submission by evening (deadline 03:00 Thu Madrid) |

## Day 4 — Fri 30 Oct 2026

### Replay result (final for the demo)
20. **Full 29-event replay: precision 1.00, recall 0.86** (12 of 14 expected alerts; 28 of 29 events
    completed; `replay_runs/latest_eval.md`, latest non-superseded run per event). Both misses are Intel:
    the April notes refinancing (logged as not material) and the August equity raise (the one event
    expected to alert by judgement). Duke's debt-details PDF stopped at its token limit (a log-only
    event). Cost per event ≈ $0.25 (press release) to ≈ $1 (10-Q); total model spend across all replay
    rounds ≈ $60 of a $150 budget.
21. **How we got there (Thu night → Fri afternoon), each step from a real run:** runner and evaluation
    (`bdca06c`); PDF extraction and `--only` that leaves other events pending (`60d054a`); tools that
    never raise and isolated event failures (`72ef8f7`); replay continues past paused runs, calendar
    maturities by code, prompt v8 (`f7754ec`); prompt v9 with focus and a visible budget, claims check v2,
    year-precision maturities and a guard against judgement upgrades for long-dated issuance (`df0caa3`);
    per-run cost limit, metrics auto-run, document terms accepted by the claims check (`cf51376`); FX for
    CAD, size-based limits, one event per shared filing (Duke's combined 10-Q), latest-run evaluation
    (`c58aa0d`); higher limits, event metadata as trusted claims evidence, FRED rate loader (`7c4dc7b`);
    event kinds for credit facilities and equity issuance (`5789e54`).
22. **Stop tuning.** Model choices vary between runs (the same event alerted in one run and logged in
    another), so the report lists every run per event. Remaining days go to the SAM chart reader, the
    dashboard, live mode and the submission. Before the final deploy, one fresh full replay with the final
    code gives the number of record.

### SAM chart reader on real decks
23. **26 of 34 charts verified across five Q2/Q3 2026 decks** (Duke, Intel, Verizon, Boeing, Amazon;
    `scripts/fetch_decks.py`, `scripts/process_decks.py`, ≈ $0.40 per full pass). SAM 3.1 finds charts
    and bars, Muse Spark reads the printed values, and bar heights must be proportional to them. Only
    printed values that passed are stored as citable; the agent's `read_chart` and `record_fact` refuse
    anything else. The 8 refusals were reviewed: stacked share-count bars (3), guidance ranges such as
    "1.5% – 2%" (2), a chart where only one bar was found (1) and regions with no printed bar values (2).
24. **Fixes from reviewing real overlays** (`scripts/dump_charts.py` exports crop, overlay and readings
    per issuer): SAM sometimes boxes each bar of a sparse chart as its own chart, so neighbouring
    single-bar boxes on one baseline are joined; a short bar cannot be measured to 8%, so 2 px of bar
    height is also allowed (a misread label still fails); Amazon prints only the first and last values,
    so Spark returns every bar with null for unlabelled ones (prompt `chart_reader_v2`) and only printed
    bars are checked; a printed 0.0% with no bar is dropped before matching. **Two false passes found
    and closed:** a single bar is trivially proportional to itself, so verification now needs at least
    two printed values; and the v1 prompt let Spark turn a printed range into its midpoint (1.75 for
    "1.5% – 2%"), a number that appears nowhere on the slide. First pass: 15 of 32 verified, three of
    them falsely; final: 26 of 34, none of them single-bar.
