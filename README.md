# Kernel Credit Lab

**Credit monitoring you can audit.** Kernel watches USD investment-grade issuers and alerts when
their debt maturity profile changes. Every alert carries the exact quote from the filing, the
recomputed maturity wall and an independent model's check.

**Live:** https://kernel-credit-lab.web.app · **Video:** _link_ ·
[![CI](https://github.com/DiegoLLBB/kernel-credit-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/DiegoLLBB/kernel-credit-lab/actions/workflows/ci.yml)

> **Principle.** Numbers come from filings and are computed in code. Models read documents and
> write sentences, and an independent model refuses any sentence or change the evidence does not
> support. No model ever computes a number.

## The problem

A credit analyst covering investment-grade issuers has to notice when a company's debt changes:
a new bond, a term loan disclosed in a 10-Q, an early redemption, a tender that shrinks an issue.
These arrive across 8-Ks, 10-Qs, prospectus supplements and press releases, and the ones that
matter are easy to miss. Kernel reads them as they are published, updates its records and tells
the analyst only what changed the picture, with the source for every number.

## What it does

- **Watches** 6 companies (9 issuers: Duke Energy and three of its operating subsidiaries,
  Oracle, Verizon, Boeing, Intel, Amazon). Live mode polls SEC EDGAR and issuer newsrooms twice
  a day.
- **Reads each new document with an agent** (Muse Spark) whose tools carry the checks:
  - every fact must be an exact quote from the document
  - every change to the debt records must cite those facts
  - materiality is decided by code
  - nothing is published until Muse Spark Open, a different model, has checked the alert's claims
- **Recomputes the maturity wall** (debt due per year, 2026–2029) before and after each change,
  at any past date.
- **Explains it in plain English.** For each company:
  - **credit metrics from XBRL:** total debt, debt due in 24 months, cash cover, leverage,
    interest coverage
  - **a checked snapshot** and an impact sentence per alert
  - **a refinancing panel:** Treasury yield plus issuer spread, with a 0–300bp shock slider
  - **a daily brief** written by Muse Wonder
- **Reads charts in investor decks.** SAM 3.1 locates bar charts and bars, Muse Spark reads the
  printed values, and bar geometry must match them. Unverifiable charts are refused
  (26 of 34 verified across 5 decks in our test run).
- **Shows its work.** Every run has a trace: each tool call, each check that passed or refused,
  tokens, cost and model.

## Results

**Replay of 29 real January–October 2026 events**, scored against expectations hidden from the
agent, compared with a one-shot model given the same documents:

| | Caught | Right year and amount | False alerts | Unsupported claims | Keeps records | Cost |
|---|---|---|---|---|---|---|
| Kernel agent | 9 of 14 | 7 of 14 | 1 | 0 | yes (16 of 23 wall checks) | $19.42 |
| One-shot model | 8 of 14 | 3 of 14 | 1 | 0 | no | $0.28 |

The agent was tuned on these events, so this is an in-sample result. The scorer was corrected
once after the first comparison (tranche matching, years read from quotes, FX at the event date),
with the same rules for both rows; see [PROGRESS.md](docs/PROGRESS.md).

**Live, out of sample.** The agent was frozen at commit `f997487` on 2 Nov 2026, 12:39 UTC, and
has run unchanged on every new filing since, twice a day. Its first alert shows why that matters.
Amazon's Q3 10-Q, filed on 30 Oct, discloses a **$17.5bn term loan signed in June and drawn in
September, due September 2029**. That loan is not in our hand-built list of 29 events for 2026:
we missed it when we built the test set. The frozen agent found it on its own, quoted the filing
("In June 2026, we entered into a $17.5 billion unsecured delayed single draw term loan … which we
drew on in September 2026. The Term Loan matures in September 2029"), passed the independent
check, and moved Amazon's 2029 maturities from $12.9bn to $30.4bn. Every live document and its
outcome (alert, no alert, needs an analyst, paused) is on the live overview, misses included.

## Meta models

| Model | Role in Kernel |
|---|---|
| **Muse Spark** (`muse-spark-1.3`) | The per-event agent: reads filings, records quote-checked facts, proposes record changes, drafts alerts. Reads chart values. Writes snapshots and impact sentences from computed facts. |
| **Muse Spark Open** (`muse-spark-open-1026`, open weights on Baseten) | Independent checker: reviews every alert, snapshot sentence and brief sentence against the evidence and refuses what it does not support. Open weights give firms a self-hostable path for checking confidential documents. |
| **Muse Wonder** (`muse-wonder-1.0`) | The daily brief across the watchlist, from computed facts only, each sentence checked by Spark Open. |
| **SAM 3.1** | Locates charts and bars in investor presentations; the bar geometry verifies Spark's readings. |

How Meta's coding models were used to build Kernel, task by task, with what review caught:
[META_TOOLING.md](docs/META_TOOLING.md).

## Architecture

```
SEC EDGAR · issuer newsrooms · FRED · XBRL
        │  (Cloud Scheduler, 07:00 and 19:00 Madrid)
        ▼
Cloud Run jobs: live · replay · seed · decks · insight · baseline
        │
        ├─► Agent (Muse Spark) ─ tools with checks ─► records, facts, changes
        │        └─► claims check (Muse Spark Open)
        ├─► Deterministic Python: maturity wall, XBRL metrics, refinancing, materiality
        └─► Insight (Spark / Wonder) ─► claims check ─► snapshot, impacts, brief
        ▼
Cloud SQL (PostgreSQL) ◄── FastAPI (Cloud Run) ◄── Next.js dashboard (Cloud Run)
                                    ▲
                     Firebase Hosting (routing, TLS)
```

Every agent run, step, tool call, check and model call is stored and shown in the trace. CI runs
lint, migrations and about 300 tests; every deploy runs a smoke test of the live pages. Details:
[DEPLOY.md](docs/DEPLOY.md).

## Limitations

- **Coverage:** 6 companies, with bond lists curated from their filings. The rest of each year's
  debt comes from the 10-K maturity schedule.
- **Replay misses:** 5 of 14 expected changes were not caught, including Intel's April notes
  and August equity raise. Events that paused or failed count as misses; the live overview lists
  each one under Details.
- **Two caught changes have wrong numbers:**
  - a parent-company term loan repayment that was never applied (`duk_parent_tl_repaid`)
  - a subsidiary term loan recorded as a convertible bond (`dep_term_loan`)
- **Live:** long 10-Qs can hit the agent's turn limit (Boeing's Q3 10-Q is paused). Limits are not
  raised after the freeze.
- **Liquidity is cash only.** Undrawn credit lines are not in the XBRL data used.
- **Refinancing is illustrative.** It assumes all maturing debt rolls at one tenor at Treasury plus
  the issuer's own spread.
- **No ratings, prices or spreads** (licensed data).

## Next

1. Per-tranche completeness checks against each deal's stated total.
2. Credit-line capacity extracted from filings with quote checks, for true liquidity cover.
3. Any SEC-registered issuer by ticker, with the wall built from XBRL.
4. SAM-read debt-maturity charts cross-checked against the filing-based wall.

## Run it locally

```bash
cp .env.example .env      # add your keys (never commit them)
make up                   # docker compose up --build -d
make migrate && make seed
make test
```

`make seed` always loads the Dec 2025 bond snapshot. Treasury/FX rates load only when
`.env` has a real 32-character `FRED_API_KEY` (dummy keys are skipped with a warning; unit
tests use recorded fixtures). Model keys can stay empty for `make test`.

Web http://localhost:3000 · API http://localhost:8000 (`/docs`). Cloud jobs (live, replay, seed,
decks, baseline) run from GitHub Actions → **Run job**; see [DEPLOY.md](docs/DEPLOY.md).

## Built during the hackathon

Repository created on 27 October 2026 for the Meta Global AI Developer Hackathon. Everything in it
was built during the event (27 Oct – 4 Nov 2026); see the commit history and
[PROGRESS.md](docs/PROGRESS.md) for the day-by-day decisions. The domain `kernelterminal.com` was
registered earlier; everything served on it was built during the event.

## Data and disclaimer

SEC EDGAR (filings, XBRL company facts) with a declared User-Agent, issuer press releases (robots.txt
respected), FRED (Treasury yields, FX). No licensed ratings or pricing data. The verifiable-agent
patterns credit Meta's Agents 101/102 workshop.

**Kernel is research support, not investment advice.**
