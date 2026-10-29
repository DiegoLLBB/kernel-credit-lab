# EDGAR test fixtures

Recorded for offline unit tests (`tests/test_edgar.py`). Do not hand-edit recorded
response bodies. If a test needs content that is not in the real filing, change the
test (or add a different real source) — never alter a fixture to make the assertion pass.

| File | Source URL | Recorded | Trimmed? |
|---|---|---|---|
| `verizon_ex99_1.htm` | https://www.sec.gov/Archives/edgar/data/732712/000119312526273410/d15574dex991.htm | 2026-10-28 | **No** — full HTTP body as returned by SEC (`EDGAR_LIVE=1`), byte-for-byte |
| `verizon_d15574d8k.htm` | *(minimal stub for `d15574d8k.htm`)* | 2026-10-29 | **Yes** — short HTML stand-in so index→document replay tests stay offline |
| `duke_duk-20260331.htm` | *(minimal stub for `duk-20260331.htm`)* | 2026-10-29 | **Yes** — short HTML stand-in for the Q1 10-Q primary document |
| `verizon_0001193125-26-273410-index.htm` | https://www.sec.gov/Archives/edgar/data/732712/000119312526273410/0001193125-26-273410-index.htm | 2026-10-28 | **No** — full filing index HTML |
| `duke_0001326160-26-000026-index.htm` | https://www.sec.gov/Archives/edgar/data/1326160/000132616026000026/0001326160-26-000026-index.htm | 2026-10-28 | **No** — full filing index HTML |
| `verizon_submissions.json` | https://data.sec.gov/submissions/CIK0000732712.json | 2026-10-28 | **Yes** — kept only a short `filings.recent` slice (5 rows) that includes accession `0001193125-26-273410`, plus a stub `filings.files[]` entry pointing at the overflow fixture; dropped company metadata not needed for `list_filings` |
| `duke_submissions.json` | https://data.sec.gov/submissions/CIK0001326160.json | 2026-10-28 | **Yes** — kept only a short `filings.recent` slice (5 rows) that includes accession `0001326160-26-000026`; `filings.files` empty |
| `CIK0000732712-submissions-001.json` | *(synthetic overflow page)* | 2026-10-28 | **Yes** — not a live SEC dump. Two columnar rows (`0001193125-20-000001` 8-K, `0001193125-20-000002` 10-Q) with 2020 dates so offline tests can exercise `filings.files` pagination without shipping a multi‑MB archive |

## Re-recording HTML exhibits

```bash
cd backend
EDGAR_LIVE=1 python -c "
from pathlib import Path
from app.config import get_settings
from app.sources.edgar import EdgarClient
get_settings.cache_clear()
url = 'https://www.sec.gov/Archives/edgar/data/732712/000119312526273410/d15574dex991.htm'
with EdgarClient(settings=get_settings()) as c:
    Path('tests/fixtures/edgar/verizon_ex99_1.htm').write_bytes(c._get(url).content)
"
```

Write `response.content` only. Do not strip scripts, re-encode, or truncate.
