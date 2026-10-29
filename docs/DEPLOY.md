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
