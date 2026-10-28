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
