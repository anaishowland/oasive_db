# Agent Handoff Document

**Last updated:** January 14, 2026

This document provides context for AI agents continuing development on Oasive.

---

## 🎯 Mission

Build an AI-powered MBS analytics platform that:
1. Ingests Freddie Mac/Fannie Mae disclosure data
2. Tags pools with AI-generated behavioral characteristics
3. Enables semantic search ("show me prepay-protected pools")
4. Supports empirical prepay research

---

## 📊 Historical Data Access

**IMPORTANT:** The CSS SFTP server only has data from 2019 onwards. For historical prepay research across economic cycles (1999-2024), use:

| Source | Coverage | Access |
|--------|----------|--------|
| **CSS SFTP** | 2019-present | IP-whitelisted SFTP |
| **Clarity Platform** | 1999-2024 | `freddiemac.com/research/datasets/sf-loanlevel-dataset` |

The **Single-Family Loan-Level Dataset (SFLLD)** on Clarity contains:
- ~54.8 million mortgages (1999-2025)
- Origination data + monthly performance data
- Standard + Non-Standard datasets (ARMs, IOs, etc.)
- 25+ years of prepay history across rate cycles

**Access:** Register at `capitalmarkets.freddiemac.com/clarity` → "CRT & Historical Data" → "SFLLD Data"

### Fannie Mae Data Access

| Source | URL | Coverage | Status |
|--------|-----|----------|--------|
| **PoolTalk (SFTP)** | `fanniemae.mbs-securities.com` | 2019-present | ⏳ Pending IP whitelist |
| **Data Dynamics** | `datadynamics.fanniemae.com` | **2000-2025** | ✅ Available for download |

**Fannie Mae Data Dynamics** provides:
- **Primary Dataset**: 2000Q1-2025Q2 single file (similar to SFLLD)
- **Quarterly files**: Individual quarter downloads
- **HARP Dataset**: Home Affordable Refinance Program loans
- ~62M loans covering 25 years of prepay history

**Download recommendation**: Download the "2000Q1-2025Q2 Acquisition and Performance File" at the top for complete historical coverage. Once downloaded, use the same ingestor pattern as SFLLD.

### SFLLD Ingestion Tools

The project includes tools for managing SFLLD historical data:

```bash
# Check which years need downloading
python3 -m src.ingestors.sflld_ingestor --status

# Process downloaded files from a directory
python3 -m src.ingestors.sflld_ingestor --process ~/Downloads/sflld

# Process a single ZIP file
python3 -m src.ingestors.sflld_ingestor --process-file ~/Downloads/historical_data_2008.zip
```

**Compliance Note:** Freddie Mac's Terms of Use prohibit automated web scraping. Files must be downloaded manually from Clarity, then processed using our ingestion tools.

**Tables:**
- `dim_loan_historical` - Origination data (1999-2025)
- `fact_loan_month_historical` - Monthly performance snapshots
- `sflld_file_catalog` - Download tracking (27 years)

---

## 📋 Phased Implementation Plan

### Phase 1: Download Freddie Files 🔄 76% Complete
**Goal:** Download all 45,356 disclosure files from Freddie SFTP to GCS

| Task | Status | Details |
|------|--------|---------|
| Set up SFTP connection | ✅ Done | IP whitelisted: 34.121.116.34 |
| Create file catalog | ✅ Done | 45,356 files tracked in `freddie_file_catalog` |
| Download files | 🔄 76% | 4 parallel jobs running, ~10,941 remaining |
| Critical files | ✅ Done | FRE_ILLD (100%), FRE_IS (100%), FRE_FISS (100%), FRE_DPR (100%) |
| Skip low-value | ✅ Done | CUSIP deal files, PDFs, economic files (not needed for prepay research) |

**Commands:**
```bash
# Run 10 parallel download jobs
for i in {1..10}; do
  gcloud run jobs execute freddie-ingestor --region=us-central1 \
    --project=gen-lang-client-0343560978 \
    --args="-m,src.ingestors.freddie_ingestor,--mode,backfill,--max-files,2500" --async
done
```

### Phase 2: Parse Pool-Level Data ✅ Complete
**Goal:** Load FRE_IS and FRE_FISS into `dim_pool`

| Task | Status | Details |
|------|--------|---------|
| FRE_IS → dim_pool | ✅ 78% | 155/200 files (rest are old 2019 daily format) |
| FRE_FISS → dim_pool | ✅ 100% | 227/227 files parsed |
| FRE_DPR → fact_pool_month | ✅ 100% | 34/34 factor files parsed |
| Basic servicer tagging | ✅ Done | prepay_protected/neutral/exposed |

**Commands:**
```bash
# Run issuance parser
gcloud run jobs execute freddie-parser --region=us-central1 \
  --project=gen-lang-client-0343560978 \
  --args="-m,src.parsers.freddie_parser,--file-type,issuance" --async

# Run FISS parser
gcloud run jobs execute freddie-parser --region=us-central1 \
  --project=gen-lang-client-0343560978 \
  --args="-m,src.parsers.freddie_parser,--file-type,fiss" --async
```

### Phase 3: Parse Loan-Level Data 🔄 99% Complete
**Goal:** Load FRE_ILLD (81 files, ~14M loans) into `dim_loan`

| Task | Status | Details |
|------|--------|---------|
| Design bulk load strategy | ✅ Done | Using batch inserts (10K per batch) |
| Process ILLD files | 🔄 99% | **80/81 files, 6.9M loans loaded** |
| Parse geographic files | 🔄 86% | **59/69 files processed** |
| Calculate pool aggregates | ⏳ Pending | State concentration, avg metrics |

**Commands:**
```bash
# Run remaining ILLD parser jobs
gcloud run jobs execute freddie-parser --region=us-central1 \
  --args="-m,src.parsers.freddie_parser,--file-type,illd,--limit,2" --async

# Run geographic parser
gcloud run jobs execute freddie-parser --region=us-central1 \
  --args="-m,src.parsers.freddie_parser,--file-type,geo,--limit,15" --async
```

**Estimated remaining:** 1 ILLD file, 10 geographic files

### Phase 4: Factor & CPR Data ✅ Complete
**Goal:** Load FRE_DPR_Fctr for prepayment analysis

| Task | Status | Details |
|------|--------|---------|
| Parse factor files | ✅ Done | 34/34 DPR files parsed |
| fact_pool_month | ✅ Done | 157,600 records loaded |
| Calculate servicer metrics | ⏳ Pending | For dynamic servicer scoring |

### Phase 5: AI Tagging & Validation ✅ Complete
**Goal:** Apply full AI tagging system to all pools

| Task | Status | Details |
|------|--------|---------|
| Schema migration | ✅ Done | Migration 008 applied - 24 new columns |
| Factor multipliers table | ✅ Done | 26 entries seeded for all factors |
| Review tagging design | ✅ Done | User updated `ai_tagging_design.md` v2.0 |
| Implement PoolTagger class | ✅ Done | `src/tagging/pool_tagger.py` (1192/sec) |
| **Tag all pools** | ✅ Done | **177,278 pools tagged (100%)** |
| **Auto-tag integration** | ✅ Done | Parser auto-tags after file processing |
| Apply FK constraints | ⏳ Pending | Migration 007 |
| Validate assumptions | ⏳ Pending | Use research framework |

### Phase 6: Historical Data (SFLLD 1999-2025) 🔄 19% Complete
**Goal:** Load 54.8M historical loans for cross-cycle prepay research

| Task | Status | Details |
|------|--------|---------|
| Create schema | ✅ Done | Migration 009: `dim_loan_historical`, `fact_loan_month_historical` |
| Create ingestor | ✅ Done | `src/ingestors/sflld_ingestor.py` with GCS support |
| Download full dataset | ✅ Done | 36.8 GB downloaded from Clarity |
| Upload to GCS | ✅ Done | 128 GB uploaded to `gs://oasive-raw-data/sflld/` |
| Cloud Run processor | ✅ Running | `sflld-processor` job (24h timeout) |
| Parse origination data | 🔄 **19%** | **10.46M / ~55M loans loaded** |
| Parse performance data | ⏳ Pending | Monthly snapshots (large but important) |
| Cross-cycle analysis | ⏳ Pending | 2000s boom, 2008 crisis, COVID refi, 2022 rates |

**Current Progress:**
- Currently processing: 2003-2004 data
- Running since: Jan 14, 05:17 UTC
- Timeout: 24 hours (plenty of time remaining)

**GCS Processing (runs in cloud, not local):**
```bash
# Start cloud processing (already running)
gcloud run jobs execute sflld-processor \
  --region=us-central1 \
    --project=gen-lang-client-0343560978 \
  --args="python,-m,src.ingestors.sflld_ingestor,--process-gcs,gs://oasive-raw-data/sflld"

# Monitor progress
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="sflld-processor"' \
  --project=gen-lang-client-0343560978 --limit=50
```

**Note:** Job uses `ON CONFLICT DO NOTHING` so restarts skip already-loaded data.

**New columns added to `dim_pool`:**
- **Static:** `loan_balance_tier`, `loan_program`, `fico_bucket`, `ltv_bucket`, `occupancy_type`, `loan_purpose`, `state_prepay_friction`, `seasoning_stage`, `property_type`, `origination_channel`, `has_rate_buydown`
- **Derived:** `refi_incentive_bps`, `premium_cpr_mult`, `discount_cpr_mult`, `convexity_score`, `contraction_risk_score`, `extension_risk_score`, `s_curve_position`
- **Composite:** `composite_prepay_score`, `bull_scenario_score`, `bear_scenario_score`, `neutral_scenario_score`, `payup_efficiency_score`
- **Renamed:** `servicer_quality_tag` → `servicer_prepay_risk`
- `servicer_prepay_risk` (prepay_protected/neutral/exposed)
- `behavior_tags` (JSONB: burnout_candidate, bear_market_stable, etc.)
- `loan_program` (VA/FHA/USDA/CONV)
- `composite_spec_pool_score` (0-100)

---

## 📊 Current Database Status (Updated Jan 14, 2026 - Evening)

| Table | Records | Status |
|-------|---------|--------|
| `dim_pool` | **177,278** | ✅ 100% AI tagged |
| `dim_loan` | **6,997,748** | ✅ Complete |
| `fact_pool_month` | 157,600 | ✅ |
| `freddie_file_catalog` | 45,356 | 76% downloaded |
| `dim_loan_historical` | **10,463,160** | 🔄 19% (~55M target) |

**Parsing Progress (SFTP 2019+):**
- IS: 200/200 ✅ 
- FISS: 227/227 ✅
- DPR: 34/34 ✅
- ILLD: 81/81 ✅ - 7.0M loans loaded
- Geographic: 59/69 (86%) - 10 remaining

**Historical Data (Clarity 1999-2025):**
- SFLLD Download: ✅ Done (36.8 GB)
- Upload to GCS: ✅ Done
- Cloud Run job: 🔄 **Running** (`sflld-processor-cz98r`)
- Progress: **10.46M / ~55M loans (19%)**
- Currently processing: 2003-2004 data

**AI Tag Distribution:**
- Loan Balance: STD (54K), MLB (27K), LLB1-7 (77K), JUMBO (339)
- Servicer Risk: Neutral (114K), Exposed (28K), Protected (15K)
- Avg Composite Score: 47.8

**Data Date Ranges:**
- Pools: 2019-06 to 2025-12 (~6.5 years)
- Loans (SFTP): 1993-04 to 2026-01 (~32 years)
- Historical: 1999-01 to 2003+ (loading...)

**Next Steps:**
1. 🔄 SFLLD processing continues (~8-12 hours remaining)
2. ⏳ Delete local `~/Downloads/sflld` after verification
3. ⏳ Download Fannie Mae historical data from Data Dynamics (2000-2025)
4. Calculate CPR from factor time series
5. Validate prepay assumptions using research framework

---

## 🔧 Infrastructure

| Component | Details |
|-----------|---------|
| **Cloud SQL** | `oasive-postgres` (PostgreSQL) |
| **GCS Bucket** | `oasive-raw-data` |
| **Cloud Run Jobs** | `freddie-ingestor`, `freddie-parser`, `sflld-processor` |
| **VPC Connector** | `data-feeds-vpc-1` (for SFTP egress) |
| **Service Account** | `cloud-run-jobs-sa@gen-lang-client-0343560978.iam.gserviceaccount.com` |

**GCS Paths:**
- `gs://oasive-raw-data/freddie/raw/` - SFTP downloaded files
- `gs://oasive-raw-data/sflld/extracted/` - SFLLD pre-extracted TXT files
- `gs://oasive-raw-data/sflld/yearly/` - SFLLD yearly ZIP archives

## ⏰ Scheduled Jobs

| Job | Schedule | Purpose | Status |
|-----|----------|---------|:------:|
| `fred-ingestor-daily` | 11:30 UTC daily | FRED interest rates | ✅ Working |
| `freddie-ingestor-daily` | 16:45 UTC Mon-Fri | Daily SFTP FISS files | ✅ Working |
| `freddie-ingestor-monthly` | 11:45 UTC 1st-3rd | Monthly IS/ILLD files | ✅ Working |

**Note (Jan 14, 2026):** Fixed scheduler permission issue - added `roles/run.invoker` to service account. Scheduler was failing with code 7 (PERMISSION_DENIED) but Cloud Run job alerts weren't triggered because jobs never started.

**Alert Policies:**
- ❌ Cloud Run job failures (FRED, Freddie) → Email notification
- ⚠️ Cloud Scheduler failures (FRED, Freddie) → Email notification
- All alerts configured with the same notification channel

---

## 📁 Key Files

| File | Purpose |
|------|---------|
| `src/ingestors/freddie_ingestor.py` | SFTP download with retry logic |
| `src/ingestors/sflld_ingestor.py` | **SFLLD historical data processor** (GCS support) |
| `src/parsers/freddie_parser.py` | Parse ZIPs → database |
| `src/tagging/pool_tagger.py` | **AI tagging engine** (1192 pools/sec) |
| `src/db/connection.py` | Cloud SQL connector |
| `docs/ai_tagging_design.md` | **Full AI tagging specification** |
| `docs/prepay_research_framework.md` | Empirical validation plan |
| `migrations/004_freddie_data_schema.sql` | Core data schema |
| `migrations/008_ai_tagging_schema.sql` | AI tag columns + factor_multipliers |
| `migrations/009_sflld_historical_schema.sql` | Historical loan tables |
| `migrations/010_unified_research_views.sql` | Cross-era research views |
| `scripts/run_sflld_cloud_migration.sh` | GCS upload + Cloud Run setup |

---

## 🔐 Credentials

| Secret | Location |
|--------|----------|
| `freddie-username` | GCP Secret Manager (`svcfre-oasive`) |
| `freddie-password` | GCP Secret Manager (version 5) |
| `fred-api-key` | GCP Secret Manager |
| `postgres-password` | GCP Secret Manager |

**CSS Support:** `Investor_Inquiry@freddiemac.com` / (800) 336-3672

---

## 🚀 Quick Commands

```bash
# Check download progress
gcloud run jobs executions list --job=freddie-ingestor --region=us-central1 \
  --project=gen-lang-client-0343560978 --limit=15

# Check parser progress
gcloud run jobs executions list --job=freddie-parser --region=us-central1 \
  --project=gen-lang-client-0343560978 --limit=5

# View logs
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="freddie-parser"' \
  --project=gen-lang-client-0343560978 --limit=20

# Database status (local)
cd /Users/anaishowland/oasive_db && source venv/bin/activate
python3 -c "
from src.db.connection import get_engine
from sqlalchemy import text
engine = get_engine()
with engine.connect() as conn:
    pools = conn.execute(text('SELECT COUNT(*) FROM dim_pool')).fetchone()[0]
    print(f'Pools: {pools:,}')
"

# Tag new pools (only processes untagged)
python3 -m src.tagging.pool_tagger --batch-size 1000

# Parse + auto-tag in one command
python3 -m src.parsers.freddie_parser --file-type issuance

# Parse without tagging
python3 -m src.parsers.freddie_parser --file-type issuance --no-tag

# Only run tagging (no parsing)
python3 -m src.parsers.freddie_parser --tag-only
```

---

## 📝 Notes

- SFLLD processor timeout: **24 hours** (increased from 4h to avoid timeouts)
- Freddie parser timeout: 4 hours
- Alert policy sends both firing + recovery emails (need to disable recovery in Cloud Console)
- FRE_FISS files are headerless (9 columns, no header row)
- Servicer classification already being applied during parsing
- **AI tagging is auto-integrated into parser** - runs after each file is processed
- Use `--no-tag` flag to disable auto-tagging if needed
- SFLLD uses `ON CONFLICT DO NOTHING` - safe to restart without duplicates
- **Jan 14 Fix:** Added `roles/run.invoker` to service account for scheduler
- **Jan 14:** Added Cloud Scheduler failure alert for Freddie (now have 4 alert policies total)

---

## 🔗 Repository

GitHub: https://github.com/anaishowland/oasive_db

All code committed. `.env` and credentials are gitignored.
