# FRED test fixtures

Used by offline unit tests (`tests/test_fred.py`). Do not hand-edit observation
bodies. If a test needs a value the series does not have, change the test — never alter
a fixture to make the assertion pass.

| File | Source | Obtained | Trimmed? |
|---|---|---|---|
| `DGS5_2026-01-01_2026-10-31.json` | FRED series `DGS5`, observation window 2026-01-01 … 2026-10-31 | 2026-10-28, **built from FRED's graph CSV, not an API recording** (see below) | **No** — full observation list for the window, in the API JSON shape |
| `DEXUSEU_2026-01-01_2026-10-31.json` | FRED series `DEXUSEU`, same window | 2026-10-28, **built from FRED's graph CSV, not an API recording** (see below) | **No** — full observation list for the window, in the API JSON shape |

## How these were obtained (2026-10-28)

`FRED_API_KEY` was empty in the local `.env`, so the St. Louis Fed **HTTP API could not be
called**. The observation dates and values were downloaded from FRED’s public graph CSV
export (same series and dates the API returns):

- https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS5&cosd=2026-01-01&coed=2026-10-31
- https://fred.stlouisfed.org/graph/fredgraph.csv?id=DEXUSEU&cosd=2026-01-01&coed=2026-10-31

Empty CSV cells (market holidays) were mapped to FRED’s missing sentinel `"."` and wrapped
in the standard `/fred/series/observations` JSON envelope. **Re-record with the API when a
key is available** so fixtures become byte-for-byte API responses:

```bash
cd backend
# set FRED_API_KEY in ../.env first
FRED_LIVE=1 python scripts/record_fred_fixtures.py
```

The recorder writes response JSON only and strips `api_key` from any saved URL metadata.
Never commit a fixture or log line that contains a live API key.


Reviewed 2026-10-29: values and missing days checked against the live graph CSV (same 211/212 rows and 7 missing days per series; spot dates 2 Jan, 31 Mar, 30 Jun, 30 Sep identical).
