# XBRL companyfacts fixtures

Byte-for-byte recordings of the live SEC companyfacts API responses, used by the
offline test suite. **Never hand-edit these files** — re-record them with the
script below if the live data changes.

- Source: `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`
  (zero-padded 10-digit CIK; same URL shape as the EDGAR company concept files)
- Recorded: 2026-10-31 (`XBRL_LIVE=1 python scripts/record_xbrl_fixtures.py`,
  User-Agent `KernelCreditLab/1.0 contact credit-lab@example.com`, siblings of
  `.env`'s SEC user agent)
- Files: one per CIK, named `CIK##########.json` (~27 MB total):
  - `CIK0001326160` Duke Energy parent — full `us-gaap` facts.
  - `CIK0000017797` Duke Energy Progress (facts filer, used for metrics).
  - `CIK0000030371` Duke Energy Carolinas — live API returns only `ffd` facts
    (no `us-gaap` section), so metrics are missing-with-reason.
  - `CIK0000037637` Duke Energy Florida — live API returns a tiny payload with
    no fact sections at all (47 bytes); locks in the missing-with-reason path
    for non-filing subsidiaries.
  - `CIK0001341439` Oracle, `CIK0000732712` Verizon, `CIK0000012927` Boeing,
    `CIK0000050863` Intel, `CIK0001018724` Amazon.
