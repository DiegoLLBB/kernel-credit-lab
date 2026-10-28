# Meta AI Tooling

How Meta's models are used **to build** Kernel and **inside** Kernel. Every development task done
with a Meta model is listed with its commit and what review found, so it is clear what the model
did and what was checked or corrected by a person.

## Building Kernel: Muse Wonder (in Cursor)

| Date | Task | Commit | Review |
|---|---|---|---|
| 28 Oct | Credit-domain and verification schema: 14 tables (issuers, instruments, call terms, curated sources, filings, events, state changes, alerts, watchlist, metric snapshots; checklist items, facts, check results, audit log), migrations 0002–0003, 14 tests | `4464998` | Clean code following existing patterns; migrations apply on Postgres with no model/DB drift. Review found 6 gaps for later work: alert body and findings, short checklist keys for the agent, event processing status, append-only audit enforced in the database, floating-rate fields, issuer metadata |
| 28 Oct | Migration 0004 closing those 6 gaps, including a Postgres trigger that makes `audit_entry` append-only | `bc5274f` | All tests pass on Postgres (40); downgrade to 0003 and re-upgrade verified |
| 28 Oct | Seed loader (`scripts/load_seed.py`): validates the whole file before writing, idempotent upserts, one provenance record per field; migration 0005 (`instrument.key`); `make seed`; tests | `ce1a988` | Loaded the real year-end snapshot on Postgres (9 issuers, 22 instruments, 151 source records); second load changes nothing. **Issue caught in review:** the earlier append-only test (from `bc5274f`) committed its test rows, which can never be deleted from an append-only table, so every run left rows behind and broke another test on re-runs (hidden in CI, which starts from an empty database). Fixed by a person in `5500057`: the test now runs in a rolled-back transaction and also checks that deletes are blocked |
| 28 Oct | `MaturitySchedule` model, migration 0006, loading of 10-K maturity schedules, reconciliation test (named instruments ≤ schedule per year) | `2d5144c` | Migration, load/reload and downgrade verified on Postgres; 9 schedules loaded. **Issue caught in review:** the reconciliation test bucketed bonds by calendar year, but schedules use each issuer's fiscal year (Oracle June–May, Intel late-December), so e.g. an Aug 2028 Oracle bond would be counted in FY2028 instead of FY2029; Duke's consolidated schedule was compared with the holdco's bonds only |
| 28 Oct | Fix for that finding: `app/tools/maturity.py` `bucket_for` (fiscal-year ranges, "after" bucket), unit tests for Oracle and Intel year ends, reconciliation across the whole Duke family | `dd997b4` | Edge cases checked (31 May → FY2026, 1 Jun → FY2027, no "after" bucket → none); 48 tests pass on Postgres. Note for the maturity-wall tool: exclude matured or redeemed debt before bucketing |
| 28 Oct | `maturity_wall` tool: per fiscal-year bucket, the 10-K amount, named active instruments, unattributed remainder and a breach flag; subsidiaries included for consolidated schedules | `960c0ae` | Year-end output matches the hand reconciliation for every issuer. **Issue caught in review:** the remainder was recomputed from instruments active on the query date, so repaid debt reappeared as "other": Duke's FY2026 remainder rose from $1,290M to $3,015M at 30 Jun 2026 after the 4.125% convertible matured. New issuance after the 10-K would also have shown as a false breach |
| 28 Oct | Fix: the schedule set is frozen at the 10-K date; buckets return named active, repaid and new-since-schedule debt, the remainder and a total; `Instrument.currency` (migration 0007) with a non-USD guard | `25d6048` | Migration up/down and seed reload verified; 53 tests pass on Postgres. **Two further issues caught in review, by replaying real events:** (1) a partial buyback still moved debt into "other" (Verizon's June tender: FY2028 total stayed at $13,032M instead of $11,889M), because amounts at the 10-K date weren't kept; (2) a query dated before a bond's issue date included that bond. Both are being fixed with point-in-time instrument state built from recorded state changes |

Development with other tools (the initial scaffold, model clients, chart reader, documentation)
is not listed here and is not attributed to Meta's models.

## Inside Kernel: models used at runtime

| Model | Role | Status |
|---|---|---|
| Muse Spark (`muse-spark-1.3`) | The per-event agent: reads documents, records quote-checked facts, proposes data changes, drafts alerts; reads charts from images | Client and strict structured output working (27 Oct) |
| Muse Wonder (`muse-wonder-1.0`) | Daily brief across alerts | Client working (27 Oct) |
| SAM 3.1 (`sam-3.1`) | Locates charts and bars in issuer presentations; bar geometry verifies Spark's chart readings | Verified chart reader working (27 Oct) |
| Muse Spark Open (`meta-models/muse-spark-open-1026`, Baseten) | Event classification; independent claims and coverage checks on the agent's work | Pending Baseten credits |

See [CONTEXT.md](CONTEXT.md), Part B, for the full model plan.
