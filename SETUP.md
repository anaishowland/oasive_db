# Oasive Data Ingestion Setup Guide

## Overview

This project contains data ingestion pipelines for:
1. **FRED** - Federal Reserve Economic Data (macro/housing/mortgage indicators)
2. **Freddie Mac** - Pool and loan-level disclosure data via CSS SFTP

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Cloud Run     │────▶│   Cloud SQL     │     │      GCS        │
│     Jobs        │     │   (Postgres)    │     │  (Raw Files)    │
└────────┬────────┘     └─────────────────┘     └────────▲────────┘
         │                                               │
         │              ┌─────────────────┐              │
         ├─────────────▶│   FRED API      │              │
         │              └─────────────────┘              │
         │                                               │
         │              ┌─────────────────┐              │
         └─────────────▶│ Freddie Mac     │──────────────┘
                        │ SFTP (CSS)      │
                        └─────────────────┘
```

---

## Prerequisites

### 1. GCP Project Setup (Already Done)
- Project ID: `gen-lang-client-0343560978`
- Region: `us-central1`
- Cloud SQL instance: `oasive-postgres`

### 2. Enable Required APIs

Run this command to enable all necessary APIs:

```bash
gcloud services enable \
    sqladmin.googleapis.com \
    run.googleapis.com \
    cloudscheduler.googleapis.com \
    secretmanager.googleapis.com \
    storage.googleapis.com \
    --project=gen-lang-client-0343560978
```

---

## GCP Credentials Setup

### Step 1: Get Your Cloud SQL Postgres Credentials

**Option A: Check existing credentials in GCP Console**
1. Go to: https://console.cloud.google.com/sql/instances/oasive-postgres/users
2. You should see a `postgres` user listed
3. If you forgot the password, click the user → "Change password" → set a new one

**Option B: Reset password via gcloud**
```bash
gcloud sql users set-password postgres \
    --instance=oasive-postgres \
    --password=YOUR_NEW_PASSWORD \
    --project=gen-lang-client-0343560978
```

### Step 2: Create GCS Bucket for Raw Data

```bash
# Create bucket for Freddie Mac raw files
gsutil mb -p gen-lang-client-0343560978 -l us-central1 gs://oasive-raw-data
```

### Step 3: Set Up Secret Manager

Store your secrets securely in GCP Secret Manager:

```bash
# FRED API Key
echo -n "YOUR_FRED_API_KEY" | gcloud secrets create fred-api-key \
    --data-file=- \
    --project=gen-lang-client-0343560978

# Postgres password
echo -n "YOUR_POSTGRES_PASSWORD" | gcloud secrets create postgres-password \
    --data-file=- \
    --project=gen-lang-client-0343560978

# Freddie Mac SFTP credentials
echo -n "YOUR_FREDDIE_USERNAME" | gcloud secrets create freddie-username \
    --data-file=- \
    --project=gen-lang-client-0343560978

echo -n "YOUR_FREDDIE_PASSWORD" | gcloud secrets create freddie-password \
    --data-file=- \
    --project=gen-lang-client-0343560978
```

### Step 4: Configure Service Account Permissions

Your service account `cloud-run-jobs-sa` needs these roles:

```bash
SA_EMAIL="cloud-run-jobs-sa@gen-lang-client-0343560978.iam.gserviceaccount.com"
PROJECT="gen-lang-client-0343560978"

# Cloud SQL Client (connect to Postgres)
gcloud projects add-iam-policy-binding $PROJECT \
    --member="serviceAccount:$SA_EMAIL" \
    --role="roles/cloudsql.client"

# Secret Manager Accessor (read secrets)
gcloud projects add-iam-policy-binding $PROJECT \
    --member="serviceAccount:$SA_EMAIL" \
    --role="roles/secretmanager.secretAccessor"

# Storage Admin (write to GCS)
gcloud projects add-iam-policy-binding $PROJECT \
    --member="serviceAccount:$SA_EMAIL" \
    --role="roles/storage.admin"

# Cloud Run Invoker (for scheduler to trigger jobs)
gcloud projects add-iam-policy-binding $PROJECT \
    --member="serviceAccount:$SA_EMAIL" \
    --role="roles/run.invoker"
```

---

## Local Development Setup

### Step 1: Update Your `.env` File

Add these variables to your `.env`:

```bash
# FRED API
FRED_API_KEY=your_fred_api_key

# Freddie Mac SFTP
FREDDIE_USERNAME=svcfre-yourcompany
FREDDIE_PASSWORD=your_password

# GCP
GCP_PROJECT_ID=gen-lang-client-0343560978
GCP_PROJECT_NAME=exploration
GCP_PUBLIC_IP=your_public_ip

# Cloud SQL Connection
CLOUDSQL_CONNECTION_NAME=gen-lang-client-0343560978:us-central1:oasive-postgres
POSTGRES_DB=postgres
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your_postgres_password

# GCS
GCS_RAW_BUCKET=oasive-raw-data

# Optional: For BigQuery
BQ_DATASET=freddie_data
```

### Step 2: Authenticate for Local Development

```bash
# Authenticate with your user account
gcloud auth application-default login

# Set default project
gcloud config set project gen-lang-client-0343560978
```

### Step 3: Install Dependencies

```bash
# Create and activate virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Step 4: Run Migrations

```bash
# Run all migrations
python scripts/run_migrations.py

# Or run a specific migration
python scripts/run_migrations.py --migration 001_fred_schema.sql
```

### Step 5: Test Locally

```bash
# Test FRED ingestion
python -m src.ingestors.fred_ingestor

# Test Freddie Mac ingestion (catalog only, no download)
python -c "from src.ingestors.freddie_ingestor import FreddieIngestor; FreddieIngestor().run(download=False)"
```

---

## Deployment

### Build and Deploy

```bash
# Make deploy script executable
chmod +x scripts/deploy.sh

# Deploy everything
./scripts/deploy.sh all

# Or deploy individually
./scripts/deploy.sh fred
./scripts/deploy.sh freddie
```

### Manual Job Execution

```bash
# Trigger FRED job manually
gcloud run jobs execute fred-ingestor --region=us-central1

# Trigger Freddie job manually
gcloud run jobs execute freddie-ingestor --region=us-central1

# View job logs
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=fred-ingestor" --limit=50
```

---

## Database Schema

### FRED Tables

| Table | Description |
|-------|-------------|
| `fred_series` | Metadata about each FRED series (38 series) |
| `fred_observation` | Time series values (one row per date per series) |
| `fred_ingest_log` | Audit trail of ingestion runs |
| `fred_latest` (view) | Most recent value for each series |

### Freddie Mac Tables

| Table | Description |
|-------|-------------|
| `freddie_file_catalog` | Tracks files discovered/downloaded from SFTP |
| `freddie_ingest_log` | Audit trail of SFTP sync runs |

---

## Schedule

| Job | Schedule | Description |
|-----|----------|-------------|
| `fred-ingestor` | Daily 6:30 AM ET | Fetch new FRED observations |
| `freddie-ingestor` | Daily 7:00 AM ET | Sync new files from CSS SFTP |

---

## Troubleshooting

### Cloud SQL Connection Issues

1. Ensure your IP is whitelisted (for local dev):
   - Go to: https://console.cloud.google.com/sql/instances/oasive-postgres/connections
   - Add your public IP under "Authorized networks"

2. For Cloud Run, connections are automatic via the Cloud SQL Auth Proxy (configured in deploy script)

### FRED API Errors

- Rate limit: 120 requests/minute. The ingestor uses retry logic.
- Missing API key: Ensure `FRED_API_KEY` is set in Secret Manager

### Freddie SFTP Connection Issues

- Account lockout after 3 failed attempts - contact CSS support
- IP whitelist required - ensure your GCP IPs are registered with CSS
- CSS Support: `Investor_Inquiry@freddiemac.com` or (800) 336-3672

---

## Next Steps

1. [ ] Run migrations to create database schema
2. [ ] Test FRED ingestion locally
3. [ ] Test Freddie SFTP connection locally  
4. [ ] Deploy to Cloud Run
5. [ ] Verify scheduled jobs are running
6. [ ] Set up BigQuery for loan-level data (future)
