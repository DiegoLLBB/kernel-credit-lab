#!/usr/bin/env bash
# One-time Google Cloud setup for Kernel Credit Lab. Safe to re-run: every step checks first.
# Creates: Artifact Registry repo (with cleanup policy), service accounts with least-privilege
# roles, keyless GitHub -> Google auth (Workload Identity Federation), and the app's secrets
# (values are typed at hidden prompts and go straight to Secret Manager).
set -euo pipefail

PROJECT=kernel-credit-lab
REGION=europe-west1
GITHUB_REPO=DiegoLLBB/kernel-credit-lab

active=$(gcloud config get-value project 2>/dev/null || true)
if [ "$active" != "$PROJECT" ]; then
  echo "Active gcloud project is '$active', expected '$PROJECT'."
  echo "Run: gcloud config configurations activate kcl"
  exit 1
fi
NUM=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
here=$(cd "$(dirname "$0")" && pwd)

echo "== Artifact Registry"
gcloud artifacts repositories describe kernel --location="$REGION" >/dev/null 2>&1 ||
  gcloud artifacts repositories create kernel --repository-format=docker --location="$REGION" \
    --description="Kernel Credit Lab images"
gcloud artifacts repositories set-cleanup-policies kernel --location="$REGION" \
  --policy="$here/ar-cleanup.json" --no-dry-run >/dev/null

echo "== Service accounts"
sa() { echo "$1@$PROJECT.iam.gserviceaccount.com"; }
for pair in "kcl-runtime:Kernel API runtime" "kcl-web:Kernel web runtime" "kcl-deployer:Kernel deployer (GitHub Actions)"; do
  id=${pair%%:*}; name=${pair#*:}
  gcloud iam service-accounts describe "$(sa "$id")" >/dev/null 2>&1 ||
    gcloud iam service-accounts create "$id" --display-name="$name"
done
bind() { gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$1" --role="$2" --condition=None >/dev/null; }
# API runtime: reach Cloud SQL and read its secrets. The web runtime needs no roles.
bind "$(sa kcl-runtime)" roles/cloudsql.client
bind "$(sa kcl-runtime)" roles/secretmanager.secretAccessor
# Deployer: push images, deploy Cloud Run, deploy Firebase Hosting.
for role in roles/run.admin roles/artifactregistry.writer roles/firebasehosting.admin roles/serviceusage.serviceUsageConsumer; do
  bind "$(sa kcl-deployer)" "$role"
done
for id in kcl-runtime kcl-web; do
  gcloud iam service-accounts add-iam-policy-binding "$(sa "$id")" \
    --member="serviceAccount:$(sa kcl-deployer)" --role=roles/iam.serviceAccountUser >/dev/null
done

echo "== GitHub -> Google keyless auth (only $GITHUB_REPO, only the main branch)"
gcloud iam workload-identity-pools describe github --location=global >/dev/null 2>&1 ||
  gcloud iam workload-identity-pools create github --location=global --display-name="GitHub Actions"
gcloud iam workload-identity-pools providers describe github --location=global --workload-identity-pool=github >/dev/null 2>&1 ||
  gcloud iam workload-identity-pools providers create-oidc github --location=global \
    --workload-identity-pool=github --display-name="GitHub OIDC" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
    --attribute-condition="assertion.repository=='$GITHUB_REPO' && assertion.ref=='refs/heads/main'"
gcloud iam service-accounts add-iam-policy-binding "$(sa kcl-deployer)" --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$NUM/locations/global/workloadIdentityPools/github/attribute.repository/$GITHUB_REPO" >/dev/null

echo "== Secrets (input is hidden; nothing is echoed or saved locally)"
gcloud secrets describe DATABASE_URL >/dev/null 2>&1 || { echo "DATABASE_URL secret missing: create it first (see docs/DEPLOY.md)"; exit 1; }
for name in MODEL_API_KEY SEC_USER_AGENT FRED_API_KEY; do
  if gcloud secrets describe "$name" >/dev/null 2>&1; then echo "  $name: exists"; continue; fi
  val=""
  while [ -z "$val" ]; do read -rsp "  Value for $name (same as in your .env): " val; echo; done
  printf '%s' "$val" | gcloud secrets create "$name" --data-file=- --replication-policy=automatic >/dev/null
  unset val
  echo "  $name: created"
done

echo
echo "Done. Add this repository variable in GitHub (Settings > Secrets and variables > Actions > Variables):"
echo "  GCP_PROJECT_NUMBER = $NUM"
