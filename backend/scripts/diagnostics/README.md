# Diagnostics (one-off)

Not deployed as Cloud Run jobs. Run from `backend/` with the API image or local venv.

| Script | Purpose |
|---|---|
| `diag_accuracy.py` | Print evidence for caught-but-wrong Kernel cases and failed baseline answers (stored outputs only). |
| `reconcile_kernel_accuracy.py` | Recompute Kernel accuracy groups from the latest eval without model calls. |
| `rescore_eval.py` | Rescore agent + baseline `ReplayEvaluation` rows (number accuracy + wall checks). |
| `probe_tools.py` | Wire probe of Meta `/responses` function tools (weather tool). |
| `chart_demo.py` | Run SAM + Spark chart reading on a single slide image. |
| `smoke_models.py` | Ping every configured model; optional `--image` for SAM 3.1. |
| `dump_charts.py` | Export chart crop/overlay PNGs from stored ChartReading rows. |
| `smoke_live.py` | Hit every live page/API route after deploy (`--base-url`). |

```bash
python -m scripts.diagnostics.smoke_models
python -m scripts.diagnostics.smoke_live --base-url https://kernel-credit-lab.web.app
python -m scripts.diagnostics.rescore_eval
```
