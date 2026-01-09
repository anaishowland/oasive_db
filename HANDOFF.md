# Agent Handoff Document

Last updated: January 9, 2026

## Context for New Agent

This document provides everything needed to continue development on the Oasive data ingestion platform.

---

## What Was Built

### 1. FRED Data Ingestion (✅ Complete & Live)

**Purpose**: Ingest 38 economic indicators from FRED API daily

**Components**:
- `src/ingestors/fred_ingestor.py` - Main ingestion logic
- `migrations/001_fred_schema.sql` - Database schema
- `migrations/002_seed_fred_series.sql` - Series definitions

**Status**:
- ✅ 34 active series (NAPM & MORTGAGE5US marked inactive - discontinued)
- ✅ 106,000+ observations in database
- ✅ Cloud Run job deployed
- ✅ Daily scheduler LIVE (6:30 AM ET / 11:30 UTC)
- ✅ Email alerts configured (failure only)

### 2. Freddie Mac SFTP Ingestion (✅ Working, Downloading in Progress)

**Purpose**: Download MBS disclosure files from CSS SFTP server

**Components**:
- `src/ingestors/freddie_ingestor.py` - SFTP download with batching/retry logic
- `migrations/003_freddie_schema.sql` - File catalog schema
- `migrations/004_freddie_data_schema.sql` - Freddie Mac data schema (pools, loans, facts)
- `scripts/analyze_sftp.py` - SFTP inventory analysis tool

**Status**:
- ✅ Authentication WORKING (username: `svcfre-oasive`)
- ✅ Cloud Run job deployed with VPC connector
- ✅ Network routing through whitelisted IP `34.121.116.34`
- ✅ Improved ingestor with batching, reconnection, and retry logic
- ✅ Migration 004 applied - Freddie data tables created
- ⏳ Historical backfill in progress (~45,000 files remaining)

**Database Tables Created** (Migration 004):
- `dim_pool` - Pool-level attributes with AI-generated tags
- `dim_loan` - Loan-level attributes
- `dim_calendar` - Date dimension for efficient joins
- `fact_pool_month` - Monthly pool performance metrics
- `fact_loan_month` - Monthly loan-level performance
- `freddie_security_issuance` - Security issuance data

**SFTP Inventory** (as of Jan 9, 2026):
- 45,353 files totaling 76.73 GB
- File types: `.zip` (71 GB), `.pdf` (5.4 GB), `.fac`, `.typ`
- Key patterns: `FRE_FISS_` (intraday), `FRE_IS_` (monthly)
- Full inventory in `docs/freddie_sftp_inventory.json`

**Credentials**:
- Username: `svcfre-oasive` (stored in `freddie-username` secret)
- Password: 15 chars (stored in `freddie-password` secret, version 5)
- Whitelisted IPs: `34.121.116.34` (Cloud NAT), `108.201.185.230` (local dev)

### 3. Infrastructure

**Cloud SQL**:
- Instance: `oasive-postgres` (us-central1)
- Database: `oasive`
- User: `postgres`

**Cloud Run Jobs**:
- `fred-ingestor` - Daily FRED sync (LIVE)
- `freddie-ingestor` - Freddie Mac SFTP sync (running parallel jobs for backfill)

**Networking for Freddie**:
- VPC Connector: `data-feeds-vpc-1`
- Cloud NAT: `data-feeds-nat-1` (router: `data-feeds-router-1`)
- Static IP: `34.121.116.34`

**Secrets** (in Secret Manager):
- `fred-api-key`
- `postgres-password`
- `freddie-username` (value: `svcfre-oasive`)
- `freddie-password` (version 5)

**GCS Bucket**:
- `oasive-raw-data` - Raw Freddie files stored at `freddie/raw/YYYY/MM/`

---

## ⚠️ CRITICAL: Testing Requirements

**FRED API**: Can be tested locally (public API, no IP restrictions)

**Freddie Mac SFTP**: CANNOT be tested locally - requires IP whitelisting [[memory:12860076]]
- Must test via Cloud Run which routes through VPC Connector → Cloud NAT → whitelisted static IP
- The whitelisted IP is stored in .env as `GCP_PUBLIC_IP=34.121.116.34`

---

## Freddie Mac Ingestor Usage

The improved ingestor supports multiple run modes:

```bash
# Catalog files without downloading
python -m src.ingestors.freddie_ingestor --mode catalog

# Download new files incrementally
python -m src.ingestors.freddie_ingestor --mode incremental

# Backfill all pending/error files
python -m src.ingestors.freddie_ingestor --mode backfill

# Filter by file type
python -m src.ingestors.freddie_ingestor --mode incremental --file-types intraday_issuance monthly_issuance

# Filter by pattern (e.g., only 2024 files)
python -m src.ingestors.freddie_ingestor --mode backfill --file-pattern "2024"

# Limit number of files (for testing)
python -m src.ingestors.freddie_ingestor --mode backfill --max-files 100
```

**File Types**:
- `intraday_issuance` - FRE_FISS_YYYYMMDD.zip (daily issuance)
- `monthly_issuance` - FRE_IS_YYYYMM.zip (monthly summary)
- `deal_files` - Individual deal documents
- `factor` - .fac files
- `archive` - Other .zip files
- `document` - .pdf files

---

## Outstanding Tasks

### 1. Historical Backfill (IN PROGRESS)

Download all 45,353 files (76 GB) from SFTP. Currently running parallel jobs:

```bash
# Run via Cloud Run for large batches
gcloud run jobs execute freddie-ingestor --region=us-central1 \
  --project=gen-lang-client-0343560978 \
  --args="-m,src.ingestors.freddie_ingestor,--mode,backfill,--max-files,1000" \
  --async
```

### 2. Set Up Recurring Schedulers (NEXT)

Create Cloud Scheduler jobs for different file cadences:
- **Intraday**: FRE_FISS files (multiple times per day on business days)
- **Daily**: Incremental sync for new files
- **Monthly**: FRE_IS monthly summary files

### 3. Parse Downloaded Files

After downloading, need to:
1. Parse ZIP files to extract loan/pool data
2. Transform data to match schema (dim_pool, dim_loan, fact tables)
3. Load structured data to Postgres

### 4. AI Tag Generation

Implement AI-generated tags for pools:
- `risk_profile` - conservative/moderate/aggressive
- `burnout_score` - prepayment burnout likelihood
- `geo_concentration_tag` - CA_heavy, diversified, etc.
- `servicer_quality_tag` - strong/moderate/weak
- `behavior_tags` - JSONB for additional predictive tags

---

## Key Files

| File | Purpose |
|------|---------|
| `src/config.py` | Configuration from env vars |
| `src/db/connection.py` | Cloud SQL connector |
| `src/ingestors/fred_ingestor.py` | FRED API client |
| `src/ingestors/freddie_ingestor.py` | SFTP client with batching |
| `scripts/analyze_sftp.py` | SFTP inventory analysis |
| `scripts/run_migrations.py` | Database migrations |
| `migrations/004_freddie_data_schema.sql` | Freddie data tables |
| `docs/freddie_sftp_inventory.json` | Full SFTP file inventory |
| `docs/database_schema.md` | Full database documentation |

---

## Deploy Changes

```bash
cd /Users/anaishowland/oasive_db

# 1. Build new image
gcloud builds submit --tag us-central1-docker.pkg.dev/gen-lang-client-0343560978/oasive-images/oasive-ingestor:latest --project=gen-lang-client-0343560978

# 2. Update job
gcloud run jobs update freddie-ingestor --region=us-central1 --project=gen-lang-client-0343560978 --image=us-central1-docker.pkg.dev/gen-lang-client-0343560978/oasive-images/oasive-ingestor:latest

# 3. Execute with args
gcloud run jobs execute freddie-ingestor --region=us-central1 --args="-m,src.ingestors.freddie_ingestor,--mode,incremental"
```

---

## Check Logs

```bash
# Freddie logs
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=freddie-ingestor" --project=gen-lang-client-0343560978 --limit=30 --format="value(textPayload)"

# FRED logs
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=fred-ingestor" --project=gen-lang-client-0343560978 --limit=20 --format="value(textPayload)"
```

---

## Database Schema

Full schema documentation in `docs/database_schema.md`.

### Key Tables

**FRED Tables**:
- `fred_series` - Series metadata (38 indicators)
- `fred_observation` - Time series data (106K+ rows)

**Freddie File Management**:
- `freddie_file_catalog` - Tracks all SFTP files and download status

**Freddie Data Tables** (Migration 004):
- `dim_pool` - Pool attributes + AI tags
- `dim_loan` - Loan-level data
- `dim_calendar` - Date dimension
- `fact_pool_month` - Monthly pool metrics
- `fact_loan_month` - Monthly loan metrics
- `freddie_security_issuance` - Issuance data

---

## Contact Info

**CSS Support** (Freddie Mac SFTP):
- Email: `Investor_Inquiry@freddiemac.com`
- Phone: (800) 336-3672

---

## GitHub

Repository: https://github.com/anaishowland/oasive_db

All code committed and pushed. `.env` and credentials are gitignored.
