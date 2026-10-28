# Issuer Universe

*Drafted 27 Oct 2026; figures verified the same evening against primary sources (details under
"Verification"). Status: **proposed**, awaiting approval. Once approved, the bond table becomes
the hand-curated seed, with a `CuratedSource` record per field.*

## Design

**9 issuing entities, 21 bonds, 2 term loans.** Every issuer earns its place in a demo workflow:

| Role in the demo | Issuers |
|---|---|
| **Workflow 2: structural subordination** (holdco notes vs. opco first mortgage bonds) | Duke Energy Corporation + Duke Energy Carolinas, Progress, Florida |
| **Workflow 1: rate-reset pressure** (cheap 2017–21 debt maturing 2026–29) | Duke family, Oracle, Verizon |
| **Workflow 1: weaker credits** (refinancing cost matters more) | Boeing, Intel |
| **Control case** (lowest coupon, negligible pressure: coverage decides, not coupon) | Amazon |
| **Critique trap 1**: bond redeemed early, still listed in an older source | Verizon 4.329% 2028 |
| **Critique trap 2**: debt that is not in the bond list | Duke Carolinas / Progress term loans |
| **Bonus event**: partial buyback via tender offer | Verizon 2.100% 2028 (55% bought back, June 2026) |

## Issuers

| # | Entity | Type | SEC CIK |
|---|---|---|---|
| 1 | Duke Energy Corporation | Utility holding company | 1326160 |
| 2 | Duke Energy Carolinas, LLC | Utility operating company | 30371 ✓ |
| 3 | Duke Energy Progress, LLC | Utility operating company | 17797 ✓ |
| 4 | Duke Energy Florida, LLC | Utility operating company | 37637 ✓ |
| 5 | Oracle Corporation | Technology | 1341439 |
| 6 | Verizon Communications Inc. | Telecom | 732712 |
| 7 | The Boeing Company | Aerospace | 12927 |
| 8 | Intel Corporation | Semiconductors | 50863 |
| 9 | Amazon.com, Inc. | Technology / retail | 1018724 |

✓ = confirmed in EDGAR's company index on 27 Oct 2026.

## Seed bonds and loans

"As of" is the date of the source the amount comes from. Confidence: **High** = amount read for
this exact instrument from an issuer document; **Medium** = derived (see notes).

| Entity | Instrument | Coupon | Maturity | Outstanding (USD) | As of | Issued | Par call | MW | CUSIP | Conf. | Src |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Duke Energy Corp | Senior unsecured | 4.850% | 2027-01-05 | 600,000,000 | 2026-06-30 | 2024-01-05 | | | 26441CCB9 | High | D1 |
| Duke Energy Corp | Senior unsecured | 3.150% | 2027-08-15 | 750,000,000 | 2026-06-30 | 2017-08-10 | | | 26441CAX3 | High | D1 |
| Duke Energy Corp | Senior unsecured | 5.000% | 2027-12-08 | 500,000,000 | 2026-06-30 | 2022-12-08 | | | 26441CBW4 | High | D1 |
| Duke Energy Corp | Senior unsecured | 4.300% | 2028-03-15 | 900,000,000 | 2026-06-30 | 2022-08-11 | | | 26441CBS3 | High | D1 |
| Duke Energy Corp | Senior unsecured | 3.400% | 2029-06-15 | 600,000,000 | 2026-06-30 | 2019-06-07 | | | 26441CBE4 | High | D1 |
| Duke Energy Corp | Convertible senior | 3.000% | 2029-03-15 | 1,500,000,000 | 2026-06-30 | 2026-03-12 | | | 26441CCL7 | High | D1 |
| Duke Energy Carolinas | First mortgage bond | 2.950% | 2026-12-01 | 600,000,000 | 2026-06-30 | 2016-11-17 | | | 26442CAS3 | High | D1 |
| Duke Energy Carolinas | First mortgage bond | 3.950% | 2028-11-15 | 650,000,000 | 2026-06-30 | 2018-11-08 | | | 26442CAX2 | High | D1 |
| Duke Energy Carolinas | First mortgage bond | 2.450% | 2029-08-15 | 450,000,000 | 2026-06-30 | 2019-08-14 | | | 26442CAY0 | High | D1 |
| Duke Energy Carolinas | **Term loan, unsecured, floating** | 4.475%† | 2027-03-12 | 1,000,000,000 | 2026-06-30 | 2026-03-13 | | | — | High | D1 |
| Duke Energy Progress | First mortgage bond | 4.350% | 2027-03-06 | 500,000,000 | 2026-06-30 | 2025-03-06 | | | 26442UAT1 | High | D1 |
| Duke Energy Progress | First mortgage bond | 3.700% | 2028-09-01 | 500,000,000 | 2026-06-30 | 2018-08-09 | | | 26442UAG9 | High | D1 |
| Duke Energy Progress | First mortgage bond | 3.450% | 2029-03-15 | 600,000,000 | 2026-06-30 | 2019-03-07 | | | 26442UAH7 | High | D1 |
| Duke Energy Progress | **Term loan, unsecured, floating** | 4.475%† | 2027-03-12 | 300,000,000 | 2026-06-30 | 2026-05-15 | | | — | High | D1 |
| Duke Energy Florida | First mortgage bond | 3.200% | 2027-01-15 | 650,000,000 | 2026-06-30 | 2017-01-06 | | | 26444HAC5 | High | D1 |
| Duke Energy Florida | First mortgage bond | 3.800% | 2028-07-15 | 600,000,000 | 2026-06-30 | 2018-06-21 | | | 26444HAE1 | High | D1 |
| Duke Energy Florida | First mortgage bond | 2.500% | 2029-12-01 | 700,000,000 | 2026-06-30 | 2019-11-26 | | | 26444HAH4 | High | D1 |
| Oracle | Senior unsecured | 2.300% | 2028-03-25 | 2,000,000,000 | 2026-05-31 | 2021-03-24 | 2028-01-25 | T+15 | 68389XCD5 | High | O1, O2 |
| Verizon | Senior unsecured | 2.100% | 2028-03-22 | **925,154,000** | 2026-06-22 | 2021-03-22 | 2028-01-22 | T+15 | 92343VGH1 | High | V1, V3, V4 |
| Verizon | Senior unsecured — **redeemed 2026-09-21** | 4.329% | 2028-09-21 | 1,250,000,000 | 2026-06-30 | 2018 | — | T+25 | 92343VER1 | High | V2, V4 |
| Boeing | Senior unsecured | 5.040% | 2027-05-01 | 2,000,000,000 | issue | 2020-05-04 | 2027-03-01 | T+50 | 097023CU7 | Medium | B1, B2 |
| Intel | Senior unsecured | 4.875% | 2028-02-10 | 1,750,000,000 | 2026-06-27 | 2023-02-10 | 2028-01-10 | T+20 | 458140CE8 | High | I1, I2 |
| Amazon | Senior unsecured | 1.650% | 2028-05-12 | 2,250,000,000 | 2025-12-31 | 2021-05-12 | 2028-03-12 | T+7 | 023135BY1 | Medium | A1, A2 |

† Floating rate; 4.475% is the rate shown at 30 Jun 2026.
Duke call terms (par call, make-whole spread) are not in the debt-details file; pull them from each
bond's 424B prospectus supplement during seeding. They don't change amounts or maturities.

**Optional addition:** Intel 1.60% notes due August 2028, $1,000M at 27 Dec 2025 (Intel FY2025
10-K) — an even sharper rate-reset example. Exact maturity day and CUSIP to pull from the 424B.

## Verification (27 Oct 2026)

| Item | Result | How |
|---|---|---|
| Duke family bonds and loans | ✅ All 17 rows confirmed (coupon, maturity, amount, CUSIP), plus issue dates added | Re-read the source PDF row by row with a PDF parser |
| Oracle 2.30% 2028 | ✅ $2,000M at 31 May 2026. Q1 FY27 10-Q (to 31 Aug 2026): $4.2B of debt repaid, consistent with scheduled maturities; no early redemption disclosed | FY2026 10-K debt table; Q1 FY27 10-Q |
| Verizon 2.100% 2028 | ⚠️ **Corrected: $925M, not $3,000M.** $2,068.1M was outstanding before Verizon's June 2026 tender, which bought back $1,143.0M (55%); the remainder matches Verizon's own 30 Jun portfolio ($925M) | Tender results; Verizon debt portfolio 30 Jun 2026; Q2 2026 10-Q |
| Verizon 4.329% 2028 | ✅ Maturity 21 Sep 2028; $1,250M at 30 Jun 2026; redeemed in full 21 Sep 2026 | Verizon debt portfolio; redemption notice |
| Intel 4.875% 2028 | ✅ $1,750M at 27 Dec 2025; 2026 10-Qs show only the Feb and May 2026 notes retired | FY2025 10-K; Q1 and Q2 2026 10-Qs |
| Amazon 1.650% 2028 | ✅ (derived) The May 2021 issue shows $15,000M outstanding at 31 Dec 2025 = $18,500M issued − 2023 and 2024 maturities, so no buybacks of the 2028 tranche | FY2025 10-K debt table |
| Boeing 5.040% 2027 | 🟡 Boeing doesn't itemize notes. No tender or early redemption found in 2026 10-Qs or news; 2026 repayments ($8.4B) match scheduled 2026 maturities ($8.35B). Amount assumed unchanged since issue | FY2025 10-K; Q3 2026 10-Q; search |
| Opco CIKs | ✅ 30371, 17797, 37637 | EDGAR company index |

## The critique traps

**Trap 1: Verizon 4.329% notes due 2028.** Verizon announced on 20 Aug 2026 that it would redeem
all $1.25bn on 21 Sep 2026 at the make-whole price (T+25). The seed deliberately lists the bond as
outstanding using the 30 Jun 2026 portfolio as its source. In the demo the critic finds the
redemption notice, the agent removes the bond and recomputes, and Verizon's 2028 maturities fall by
$1.25bn.

**Trap 2: Duke term loans.** Duke Energy Carolinas and Progress borrowed $1.3bn of floating-rate
term loans in 2026, due 12 Mar 2027. A bond-only maturity wall understates 2027 refinancing
needs; the critic catches the gap between bond-level and filing-level totals.

**Bonus: Verizon's June 2026 tender.** Shows why issuer-level data must be refreshed: the 2.100%
2028 notes shrank from $2.07bn to $0.93bn in one transaction.

## Seed snapshot vs. replay (added 28 Oct)

With the product reframed as a proactive monitor (see CONTEXT.md), the seed is a **snapshot as of
31 Dec 2025**, and 2026 changes are discovered by Kernel during replay. The verified 30 Jun 2026
figures above become the **expected end state** of the replay at that date.

| Instrument | In the 31 Dec 2025 snapshot | Discovered in replay |
|---|---|---|
| Duke Corp 3.000% convertible 2029 | absent | new issuance, settled 12 Mar 2026 |
| Duke Carolinas term loan 2027 | absent | new loan, settled 13 Mar 2026 (Q1 10-Q) |
| Duke Progress term loan 2027 | absent | new loan, settled 15 May 2026 (Q2 10-Q) |
| Verizon 2.100% 2028 | $2,068,135,000 | tender: −$1,142,981,000 (June) |
| Verizon 4.329% 2028 | $1,250,000,000 | redemption announced 20 Aug, settled 21 Sep |
| Duke Corp 4.125% convertible due 15 Apr 2026 | $1,725,000,000 | matures 15 Apr 2026 |
| Duke Corp term loan due Sep 2026 (floating) | $2,000,000,000 | repaid early (absent at 30 Jun 2026) |
| Other Duke instruments | same amounts at 31 Dec 2025 and 30 Jun 2026 (verified) | — |

**Done (28 Oct):** Duke's [31 Dec 2025 debt details](https://s201.q4cdn.com/583395453/files/doc_downloads/fixed-income-investors/2026/02/LT-Debt-Information-Final-Debt-Details-12-31-25.pdf)
read row by row: all 15 Duke bonds have the same amounts as at 30 Jun 2026; the 2026 convertible
and opco term loans are absent; the holdco term loan and 4.125% convertible are present (added to
the seed). Verizon does not itemize notes at year-end, so the 2.100% 2028 snapshot amount is the
$2,068,135,000 stated in its 11 May 2026 tender announcement (dated accordingly in the seed).

**Duke's first half of 2026, as the replay should narrate it:** new $1.5bn 3.000% convertible
(12 Mar) → $1.725bn 4.125% convertible repaid at maturity (15 Apr) → $2.0bn holdco term loan
repaid early → $1.0bn Carolinas term loan (13 Mar) and $0.3bn Progress term loan (15 May) added.

Seed file: `backend/seed/snapshot_2025-12-31.json` (9 issuers, 21 bonds, 1 loan, sources per
instrument).

## Open items

1. **Maturity-profile chart** for the chart reader: not found yet (AEP and Duke publish tables).
2. **Duke call terms** from each bond's 424B (during seeding).
3. **Boeing 5.040% 2027**: confirm outstanding via a bond data source if one becomes available.
4. **Ratings:** only as disclosed in each issuer's own filings.

## Sources

- **D1** Duke Energy, [Debt Details as of 30 Jun 2026](https://s201.q4cdn.com/583395453/files/doc_downloads/2026/08/Debt-Details-06-30-26-FINAL.pdf)
- **O1** Oracle, [pricing term sheet, 22 Mar 2021](https://www.sec.gov/Archives/edgar/data/1341439/000119312521089955/d132146dfwp.htm)
- **O2** Oracle, [FY2026 Form 10-K](https://www.sec.gov/Archives/edgar/data/0001341439/000119312526277521/orcl-20260531.htm) and Q1 FY2027 10-Q (filed 11 Sep 2026)
- **V1** Verizon, [pricing term sheet, 11 Mar 2021](https://www.sec.gov/Archives/edgar/data/732712/000119312521078281/d120674dfwp.htm)
- **V2** Verizon, [redemption announcement, 20 Aug 2026](https://www.verizon.com/about/news/verizon-redeems-debt-securities-september-2026)
- **V3** Verizon, [final tender offer results, June 2026](https://www.stocktitan.net/news/VZ/verizon-announces-expiration-and-final-results-of-its-tender-offers-gsva5gtxsh10.html)
- **V4** Verizon, [debt portfolio as of 30 Jun 2026](https://www.verizon.com/about/sites/default/files/Verizon-IR-debt-portfolio-63026.pdf)
- **B1** Boeing, [pricing term sheet, 30 Apr 2020](https://www.sec.gov/Archives/edgar/data/12927/000119312520129342/d848621dfwp.htm)
- **B2** Boeing, FY2025 10-K and [Q3 2026 10-Q](https://www.sec.gov/Archives/edgar/data/0000012927/000162828026067508/ba-20260930.htm)
- **I1** Intel, [pricing term sheet, 7 Feb 2023](https://www.sec.gov/Archives/edgar/data/50863/000119312523027126/d448265dfwp.htm)
- **I2** Intel, FY2025 10-K and [Q2 2026 10-Q](https://www.sec.gov/Archives/edgar/data/0000050863/000005086326000157/intc-20260627.htm)
- **A1** Amazon, [pricing term sheet, 10 May 2021](https://www.sec.gov/Archives/edgar/data/1018724/000119312521156519/d504007dfwp.htm)
- **A2** Amazon, FY2025 10-K
