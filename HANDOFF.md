# Agent Handoff Document

**Last updated:** January 9, 2026

This document provides context for AI agents continuing development on Oasive.

---

## Current Status

| Task | Status | Progress |
|------|--------|----------|
| FRED Ingestion | ✅ Complete | 34 series, 106K+ observations |
| Freddie Download | 🔄 In Progress | 12,959 / 45,356 (28.6%) |
| Freddie Parse | 🔄 In Progress | 2,333 pools loaded |
| AI Tagging | 📋 Designed | `docs/ai_tagging_design.md` |
| Research Framework | 📋 Designed | `docs/prepay_research_framework.md` |

---

## What's Built

### 1. FRED Data Ingestion (✅ Live)

- **Purpose:** Daily ingest of 38 economic indicators
- **Status:** Running daily at 6:30 AM ET
- **Data:** 106,000+ observations in `fred_observation`
- **Files:** `src/ingestors/fred_ingestor.py`, `migrations/001-002`

### 2. Freddie Mac SFTP Ingestion (🔄 Downloading)

- **Purpose:** Download 45,353 MBS disclosure files (76.73 GB)
- **Auth:** Working (username: `svcfre-oasive`, IP: `34.121.116.34`)
- **Files:** `src/ingestors/freddie_ingestor.py`, `migrations/003-004`

**⚠️ CRITICAL:** Freddie SFTP cannot be tested locally - requires whitelisted IP via Cloud Run.

### 3. Freddie File Parser (🔄 Running)

- **Purpose:** Parse downloaded ZIP files → PostgreSQL tables
- **Files:** `src/parsers/freddie_parser.py`
- **Outputs:** `dim_pool`, `dim_loan`, `fact_pool_month`

### 4. AI Tagging System (📋 Designed)

Full design in `docs/ai_tagging_design.md`:

| Tag | Purpose |
|-----|---------|
| `loan_program` | VA/FHA/USDA/CONV classification |
| `state_prepay_friction` | high/moderate/low based on state |
| `servicer_prepay_risk` | prepay_protected/neutral/exposed |
| `burnout_score` | 0-100 burnout likelihood |
| `composite_score` | 0-100 overall spec pool score |

### 5. Prepay Research Framework (📋 Designed)

Full design in `docs/prepay_research_framework.md`:

- 20 prepay assumptions registered (A001-A020)
- 10 interaction hypotheses (I001-I010)
- Empirical validation protocol
- Database tables in `migrations/006`

---

## Key Files

| File | Purpose |
|------|---------|
| `src/ingestors/fred_ingestor.py` | FRED API client |
| `src/ingestors/freddie_ingestor.py` | SFTP download with retry |
| `src/parsers/freddie_parser.py` | Parse disclosure files |
| `src/db/connection.py` | Cloud SQL connector |
| `migrations/004_freddie_data_schema.sql` | Pool/loan schema |
| `migrations/007_add_foreign_keys.sql` | FK constraints (pending) |

---

## Cloud Run Commands

```bash
# Execute Freddie download (backfill mode)
gcloud run jobs execute freddie-ingestor --region=us-central1 \
  --project=gen-lang-client-0343560978 \
  --args="-m,src.ingestors.freddie_ingestor,--mode,backfill,--max-files,2000" \
  --async

# Check job status
gcloud run jobs executions list --job=freddie-ingestor --region=us-central1 \
  --project=gen-lang-client-0343560978 --limit=5

# View logs
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=freddie-ingestor" \
  --project=gen-lang-client-0343560978 --limit=30
```

---

## Database Tables

**FRED:**
- `fred_series` - 38 indicators
- `fred_observation` - 106K+ time series values

**Freddie Files:**
- `freddie_file_catalog` - 45,356 files tracked

**Freddie Data:**
- `dim_pool` - Pool attributes + AI tags (2,333+ rows)
- `dim_loan` - Loan-level data (pending ILLD parsing)
- `fact_pool_month` - Monthly metrics (2,333+ rows)
- `dim_calendar` - Date dimension

---

## Remaining Tasks

### Immediate
1. Continue historical downloads (32,000+ files remaining)
2. Parse FRE_ILLD files → `dim_loan`
3. Apply migration 007 (FK constraints)

### Next Phase
1. Implement `PoolTagger` class with tag rules
2. Build semantic translation agent
3. Create pool screener API

---

## Credentials

| Secret | Location |
|--------|----------|
| `freddie-username` | GCP Secret Manager (value: `svcfre-oasive`) |
| `freddie-password` | GCP Secret Manager (version 5) |
| `fred-api-key` | GCP Secret Manager |
| `postgres-password` | GCP Secret Manager |

**CSS Support:** `Investor_Inquiry@freddiemac.com` / (800) 336-3672

---

## GitHub

Repository: https://github.com/anaishowland/oasive_db

All code committed. `.env` and credentials are gitignored.
