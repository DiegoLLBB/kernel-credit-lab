# Newsroom test fixtures

Recorded for offline unit tests (`tests/test_newsroom.py`). Do not hand-edit recorded
response bodies. If a test needs content that is not on the real page, change the test —
never alter a fixture to make the assertion pass.

| File | Source URL | Recorded | Trimmed? |
|---|---|---|---|
| `verizon_tender_launch_2026-05-11.html` | https://www.verizon.com/about/news/feed/verizon-debt-tender-offer-consent-solicitation | 2026-10-28 | **No** — full HTTP body (`NEWSROOM_LIVE=1`) |
| `verizon_tender_results_2026-06-17.html` | https://www.verizon.com/about/node/929969 | 2026-10-28 | **No** — full HTTP body |
| `verizon_redemption_2026-08-20.html` | https://www.verizon.com/about/news/verizon-redeems-debt-securities-september-2026 | 2026-10-28 | **No** — full HTTP body |
| `verizon_financial_news.xml` | https://www.verizon.com/about/rss/news/financial-news.xml | 2026-10-28 | **No** — full RSS body (Financial & Investor feed linked from https://www.verizon.com/about/news/rss-feeds) |
| `verizon_robots.txt` | https://www.verizon.com/robots.txt | 2026-10-28 | **No** — full robots.txt |

## Notes

- **One redaction.** Verizon's article pages embed Verizon's own Mapbox access token (a
  public `pk.` token used by its site's maps). The recorder replaces any such token with
  `REDACTED-THIRD-PARTY-TOKEN` (`redact_credentials` in `scripts/record_newsroom_fixtures.py`),
  so third-party credentials are never committed. Nothing else in the body is changed, and the
  article text and tables the tests read are untouched.

- Verizon’s financial category HTML listing (`/about/news-category/financial`) is a JS shell
  with no article rows in the static response, so ingestion prefers the RSS feed above.
- The live financial RSS is a short rolling window. As of 2026-10-28 it includes the
  20 Aug 2026 redemption notice but **not** the May/June cash-tender pages (they have
  aged out of the feed). Those tender pages are still recorded here for `fetch_text` tests.

## Re-recording

```bash
cd backend
NEWSROOM_LIVE=1 python scripts/record_newsroom_fixtures.py
```

Writes `response.content` only. Do not strip scripts, re-encode, or truncate.
