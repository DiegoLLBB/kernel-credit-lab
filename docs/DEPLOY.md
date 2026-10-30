# Deployment

Kernel runs on Google Cloud in its own project (`kernel-credit-lab`), separate from any other
product. Live at https://kernel-credit-lab.web.app (custom domain: `credit.kernelterminal.com`,
registered before the hackathon; everything served there was built during it).

| Piece | Service | Notes |
|---|---|---|
| Dashboard (Next.js) | Cloud Run `kernel-web` | scales to zero, max 2 instances |
| API (FastAPI) | Cloud Run `kernel-api` | scales to zero, max 2 instances, 15-min request timeout |
| Migrations | Cloud Run job `kernel-migrate` | runs `alembic upgrade head` before every deploy |
| Database | Cloud SQL for PostgreSQL 18 + pgvector (Firebase SQL Connect trial instance) | reached over the Cloud SQL socket, never a public IP |
| Routing, TLS, custom domain | Firebase Hosting | `/api/**` and `/health*` → API, everything else → web |
| Secrets | Secret Manager | `DATABASE_URL`, `MODEL_API_KEY`, `SEC_USER_AGENT`, `FRED_API_KEY` |
| Images | Artifact Registry `kernel` | keeps the 5 most recent, deletes others after 7 days |
| CI/CD | GitHub Actions `deploy.yml` | runs after CI passes on `main`; keyless auth (Workload Identity Federation) |
| Data jobs | Cloud Run jobs `kernel-seed`, `kernel-replay`, `kernel-decks` | same image, service account, Cloud SQL instance, secrets and env vars as `kernel-api`; see below |

**Least privilege.** Three service accounts: `kcl-runtime` (API: Cloud SQL client, secret
accessor), `kcl-web` (no roles), `kcl-deployer` (GitHub only, main branch only: deploy Cloud Run
and Hosting, push images). No Google key file exists anywhere.

**Cost controls.** Free allowances cover demo traffic: scale to zero, max 2 instances per
service, a $5 budget alert, image cleanup. The database is the free trial instance (3 months);
after that it bills at standard Cloud SQL rates, so it is deleted or downsized before then.

**Known limit.** Requests through Firebase Hosting time out after 60 seconds, so long agent
runs are started asynchronously and polled, not held open by the browser.

## One-time setup

1. New Firebase project `kernel-credit-lab` on the Blaze plan with a $5 budget alert.
2. SQL Connect free-trial instance `kernel-credit-lab-instance` (europe-west1), database `kernel`,
   user `kernel`, pgvector enabled, `DATABASE_URL` secret (`...@/kernel?host=/cloudsql/<connection name>`).
3. Firebase console → Hosting → Get started (creates the default site).
4. `gcloud config configurations activate kcl && ./deploy/setup_gcp.sh`
5. GitHub repository variable `GCP_PROJECT_NUMBER` (printed by the script).
6. Push to `main`, or run the Deploy workflow by hand.

## Running jobs in the cloud

Every job that produces data locally also runs in Google Cloud, writing to Cloud SQL
through the same code paths: the jobs share the API's image, service account
(`kcl-runtime`), Cloud SQL instance, secrets and env vars (defined once as
`JOB_SECRETS`/`JOB_ENV_VARS` in `deploy.yml`). They are deployed on every deploy but
never executed by it — only `kernel-migrate` keeps `--execute-now`. The local database
is never copied into the cloud; the cloud database is built by running the jobs.

| Job | Command | What it does | Cost |
|---|---|---|---|
| `kernel-seed` | `python -m scripts.seed_job` | Loads `seed/snapshot_2025-12-31.json` (idempotent upsert: re-running changes no row counts) then `load_rates` (Treasury + FX from FRED) | ~$0 (no model calls) |
| `kernel-replay` | `python -m scripts.run_replay` | Runs the agent over the replay events day by day, then `--eval-latest`: writes `replay_evaluation`, the database row the dashboard reads. Defaults: full window, `--max-cost-usd 30`. Resumable: re-executing continues where it stopped | ~$0.25 per press-release event, ~$1 per 10-Q event; ~$15 for a full 29-event replay |
| `kernel-decks` | `python -m scripts.decks_job` | `fetch_decks` (5 investor decks into `Filing` rows) then `process_decks` (SAM locates charts, Spark reads values, geometry verifies) | ~$0.40 per full pass |

Resources (with the reason in `deploy.yml`): seed 1 CPU / 1 GiB / 30 min; replay 1 CPU /
1 GiB / 6 h with `--max-retries=0`; decks 2 CPU / 2 GiB (PDF rendering) / 2 h with
`--max-retries=0`.

### From GitHub (one click)

Actions → **Run job** → Run workflow: pick `seed`, `replay` or `decks`. The `args` field
overrides the job's default arguments (space-separated, e.g.
`--only vz_redemption_notice --max-cost-usd 2 --eval-latest`); empty keeps the defaults.
`wait` (default true) waits for the execution to finish. The run summary shows the job
name, the arguments used, the execution id and the link to its logs.

### From a terminal

```bash
gcloud run jobs execute kernel-seed --region=europe-west1 --wait
gcloud run jobs execute kernel-replay --region=europe-west1 \
  --args=-m,scripts.run_replay,--only,vz_redemption_notice,--max-cost-usd,2,--eval-latest \
  --wait
gcloud run jobs execute kernel-decks --region=europe-west1 --wait
```

(`--args` is comma-separated; each comma becomes one container argument. It replaces the
deployed defaults entirely, so include `-m scripts.<job>` when passing `--args` from a
terminal. The GitHub workflow adds the module automatically — there, only script flags
are needed.)

### Reading the logs

Each job prints one line per step — seed counts, each replay event with its outcome and
cost, each deck's charts found and verified — plus a final summary with total cost. A
nonzero exit marks the execution as failed. Logs live at Cloud Console → Cloud Run →
Jobs → `kernel-<job>` → Logs (the Run job workflow prints the direct link), or:

```bash
gcloud logging read \
  'resource.type="cloud_run_job" AND resource.labels.job_name="kernel-replay"' \
  --project=kernel-credit-lab --limit=100 --format='value(textPayload)' --freshness=2h
```
