# FRED test fixtures

Recorded for offline unit tests (`tests/test_fred.py`). Do not hand-edit observation
bodies. If a test needs a value the series does not have, change the test — never alter
a fixture to make the assertion pass.

| File | Source URL (api_key stripped) | Recorded | Trimmed? |
|---|---|---|---|
| `DGS5_2026-01-01_2026-10-31.json` | `https://api.stlouisfed.org/fred/series/observations?series_id=DGS5&observation_start=2026-01-01&observation_end=2026-10-31&file_type=json&sort_order=asc` | 2026-10-29 | **No** — full API JSON body |
| `DEXUSEU_2026-01-01_2026-10-31.json` | same endpoint with `series_id=DEXUSEU` | 2026-10-29 | **No** — full API JSON body |
| `DGS5_2026-01-01_2026-10-31.source.txt` | URL sidecar with `api_key` removed | 2026-10-29 | n/a |
| `DEXUSEU_2026-01-01_2026-10-31.source.txt` | URL sidecar with `api_key` removed | 2026-10-29 | n/a |

## Re-recording

```bash
cd backend
# FRED_API_KEY must be set (e.g. in ../.env)
FRED_LIVE=1 python scripts/record_fred_fixtures.py
```

Writes response JSON only and strips `api_key` from any saved URL metadata.
Never commit a fixture or log line that contains a live API key.
