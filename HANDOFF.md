# Agent Handoff Document

Last updated: December 31, 2025

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
- ✅ 35/36 series working (NAPM discontinued in FRED)
- ✅ 106,000+ observations in database
- ✅ Cloud Run job deployed
- ✅ Daily scheduler LIVE (6:30 AM ET / 11:30 UTC)

**Data in Database**:
- Unemployment rate, CPI, GDP, housing starts, mortgage rates, Treasury yields, Fed balance sheet, etc.
- Full list in `docs/FRED_data.csv`

### 2. Freddie Mac SFTP Ingestion (⚠️ Blocked on Auth)

**Purpose**: Download MBS disclosure files from CSS SFTP server

**Components**:
- `src/ingestors/freddie_ingestor.py` - SFTP download logic
- `migrations/003_freddie_schema.sql` - File catalog schema

**Status**:
- ✅ Cloud Run job deployed with VPC connector
- ✅ Network routing works (traffic goes through whitelisted IP 34.121.116.34)
- ✅ SFTP connection reaches CSS server (see "Welcome to CSS" banner in logs)
- ❌ **Authentication failing** - See Outstanding Issues

### 3. Infrastructure

**Cloud SQL**:
- Instance: `oasive-postgres` (us-central1)
- Database: `oasive` (NOT `postgres` - this was a bug we fixed)
- User: `postgres`

**Cloud Run Jobs**:
- `fred-ingestor` - Works, runs daily
- `freddie-ingestor` - Deployed, waiting for auth fix

**Networking for Freddie**:
- VPC Connector: `data-feeds-vpc-1`
- Cloud NAT: `data-feeds-nat-1`
- Static IP: `34.121.116.34` (whitelisted with CSS)

**Secrets** (in Secret Manager):
- `fred-api-key`
- `postgres-password`
- `freddie-username` (value: `svcFRE-OasiveInc`)
- `freddie-password`

---

## Outstanding Issues

### 1. Freddie Mac Authentication (HIGH PRIORITY)

**Problem**: SFTP authentication fails despite correct credentials

**Evidence from logs**:
```
INFO - Connected (version 2.0, client CrushFTPSSHD)
INFO - Auth banner: b'Welcome to Common Securitization Solutions SFTP Portal !!!'
INFO - Authentication (password) failed.
```

**What we verified**:
- Username in Secret Manager: `svcFRE-OasiveInc` (16 chars)
- Password in Secret Manager: 14 chars (matches .env)
- Network: Traffic routes through whitelisted IP 34.121.116.34

**Action Required**:
User has emailed Freddie Mac support (`Investor_Inquiry@freddiemac.com`) to:
1. Verify account is not locked (we made 3 failed attempts)
2. Confirm credentials are correct
3. Verify IP 34.121.116.34 is properly whitelisted

**CSS Support**:
- Email: `Investor_Inquiry@freddiemac.com`
- Phone: (800) 336-3672

### 2. NAPM Series Failing

**Problem**: ISM Manufacturing PMI (NAPM) fails to fetch from FRED

**Impact**: Minor - 1 of 36 series, non-critical

**Likely Cause**: Series may be discontinued or renamed in FRED

**Fix Options**:
1. Mark NAPM as `is_active=FALSE` in `fred_series` table
2. Or find replacement series ID

### 3. Freddie Scheduler Not Set Up

**Once auth is fixed**, add scheduler:
```bash
gcloud scheduler jobs create http freddie-ingestor-daily \
    --project=gen-lang-client-0343560978 \
    --location=us-central1 \
    --schedule="0 12 * * *" \
    --time-zone="UTC" \
    --uri="https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/gen-lang-client-0343560978/jobs/freddie-ingestor:run" \
    --http-method=POST \
    --oauth-service-account-email=cloud-run-jobs-sa@gen-lang-client-0343560978.iam.gserviceaccount.com
```

---

## Key Files to Know

| File | Purpose |
|------|---------|
| `src/config.py` | All configuration, reads from env vars |
| `src/db/connection.py` | Cloud SQL connector setup |
| `src/ingestors/fred_ingestor.py` | FRED API client, bulk insert logic |
| `src/ingestors/freddie_ingestor.py` | SFTP client, file catalog management |
| `scripts/run_migrations.py` | Runs SQL migrations with proper statement splitting |
| `.env` | Local secrets (not in git) |

---

## How to Test Changes

### Local Testing

```bash
cd /Users/anaishowland/oasive_db
source venv/bin/activate

# Test FRED
PYTHONPATH=/Users/anaishowland/oasive_db python -c "
from src.ingestors.fred_ingestor import FREDIngestor
ingestor = FREDIngestor()
result = ingestor.run(series_ids=['UNRATE'])  # Test single series
print(result)
"

# Test Freddie (will fail on auth until fixed)
PYTHONPATH=/Users/anaishowland/oasive_db python -c "
from src.ingestors.freddie_ingestor import FreddieIngestor
ingestor = FreddieIngestor()
result = ingestor.run(download=False)
print(result)
"
```

### Deploy Changes

```bash
# 1. Build new image
gcloud builds submit --tag us-central1-docker.pkg.dev/gen-lang-client-0343560978/oasive-images/oasive-ingestor:latest --project=gen-lang-client-0343560978

# 2. Update job(s)
gcloud run jobs update fred-ingestor --region=us-central1 --project=gen-lang-client-0343560978 --image=us-central1-docker.pkg.dev/gen-lang-client-0343560978/oasive-images/oasive-ingestor:latest

# 3. Test
gcloud run jobs execute fred-ingestor --region=us-central1 --wait
```

### Check Logs

```bash
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=fred-ingestor" --project=gen-lang-client-0343560978 --limit=20 --format="value(textPayload)"
```

---

## Database Schema

### fred_series
```sql
- series_id (PK): FRED series ID (e.g., "UNRATE")
- indicator_id: Internal name
- name: Human-readable name
- domain: macro, housing, mortgage, policy, rates_curve
- frequency: daily, weekly, monthly, quarterly
- is_active: Whether to fetch this series
```

### fred_observation
```sql
- series_id (FK)
- obs_date
- value
- vintage_date (default: 0001-01-01)
- raw_payload (JSONB)
```

### freddie_file_catalog
```sql
- remote_path: Path on SFTP server
- filename
- file_type: loan_level, pool, factor, disclosure
- download_status: pending, downloaded, processed, error
- local_gcs_path: GCS location after download
```

---

## Next Steps (Suggested Roadmap)

1. **Fix Freddie Auth** - Wait for CSS support response
2. **Add Freddie Scheduler** - Once auth works
3. **Process Freddie Files** - Parse downloaded ZIPs, load to BigQuery
4. **Add More FRED Series** - Based on `docs/MBS_ontology.csv`
5. **Build Analytics Layer** - Vector embeddings, knowledge graph (per business plan)

---

## GitHub

Repository: https://github.com/anaishowland/oasive_db

All code is committed and pushed. `.env` is gitignored.
