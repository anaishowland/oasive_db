# Agent Handoff Document

**Last updated:** January 11, 2026

This document provides context for AI agents continuing development on Oasive.

---

## 🎯 Mission

Build an AI-powered MBS analytics platform that:
1. Ingests Freddie Mac/Fannie Mae disclosure data
2. Tags pools with AI-generated behavioral characteristics
3. Enables semantic search ("show me prepay-protected pools")
4. Supports empirical prepay research

---

## 📋 Phased Implementation Plan

### Phase 1: Download Freddie Files 🔄 71% Complete
**Goal:** Download all 45,356 disclosure files from Freddie SFTP to GCS

| Task | Status | Details |
|------|--------|---------|
| Set up SFTP connection | ✅ Done | IP whitelisted: 34.121.116.34 |
| Create file catalog | ✅ Done | 45,356 files tracked in `freddie_file_catalog` |
| Download files | 🔄 71% | 10 parallel jobs running, ~12,700 remaining |
| Critical files | ✅ Done | FRE_ILLD (100%), FRE_IS (93%), FRE_FISS (87%) |
| Skip tiny metadata | ✅ Done | 323 files skipped (status/ack files) |

**Commands:**
```bash
# Run 10 parallel download jobs
for i in {1..10}; do
  gcloud run jobs execute freddie-ingestor --region=us-central1 \
    --project=gen-lang-client-0343560978 \
    --args="-m,src.ingestors.freddie_ingestor,--mode,backfill,--max-files,2500" --async
done
```

### Phase 2: Parse Pool-Level Data 🔄 In Progress
**Goal:** Load FRE_IS and FRE_FISS into `dim_pool` and `freddie_security_issuance`

| Task | Status | Details |
|------|--------|---------|
| FRE_IS → dim_pool | 🔄 Running | 186 files, ~1000+ pools each |
| FRE_FISS → issuance | 🔄 Running | 197 files, headerless format |
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

### Phase 3: Parse Loan-Level Data ⏳ Pending
**Goal:** Load FRE_ILLD (81 files, ~8M loans) into `dim_loan`

| Task | Status | Details |
|------|--------|---------|
| Design bulk load strategy | ⏳ Pending | Use PostgreSQL COPY for speed |
| Process by year/quarter | ⏳ Pending | Avoid memory issues |
| Calculate pool aggregates | ⏳ Pending | State concentration, avg metrics |

**Strategy:** 
- Use `COPY` command for bulk inserts (100x faster than individual INSERTs)
- Process files chronologically by year
- Calculate state-level aggregates for `geo_concentration_tag`

### Phase 4: Factor & CPR Data ⏳ Pending
**Goal:** Load FRE_DPR_Fctr for prepayment analysis

| Task | Status | Details |
|------|--------|---------|
| Parse factor files | ⏳ Pending | Cohort-level CPR/SMM data |
| Update fact_pool_month | ⏳ Pending | Monthly prepay metrics |
| Calculate servicer metrics | ⏳ Pending | For dynamic servicer scoring |

### Phase 5: AI Tagging & Validation ✅ Complete
**Goal:** Apply full AI tagging system to all pools

| Task | Status | Details |
|------|--------|---------|
| Schema migration | ✅ Done | Migration 008 applied - 24 new columns |
| Factor multipliers table | ✅ Done | 26 entries seeded for all factors |
| Review tagging design | ✅ Done | User updated `ai_tagging_design.md` v2.0 |
| Implement PoolTagger class | ✅ Done | `src/tagging/pool_tagger.py` (1192/sec) |
| **Tag all pools** | ✅ Done | **157,953 pools tagged** |
| **Auto-tag integration** | ✅ Done | Parser auto-tags after file processing |
| Apply FK constraints | ⏳ Pending | Migration 007 |
| Validate assumptions | ⏳ Pending | Use research framework |

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

## 📊 Current Database Status (Updated Jan 11, 2026)

| Table | Records | Status |
|-------|---------|--------|
| `dim_pool` | **161,136** | 157,971 tagged |
| `dim_loan` | **2,245,435** | 🔄 Phase 3 in progress |
| `fact_pool_month` | 157,600 | ✅ |
| `freddie_file_catalog` | 45,356 | 76% downloaded |

**Parsing Progress:**
- IS: 155/200 (78%) - remaining are 2019 daily files
- FISS: 227/227 ✅
- DPR: 34/34 ✅
- ILLD: 13/81 (16%) - Cloud Run jobs processing

**AI Tag Distribution:**
- Loan Balance: STD (54K), MLB (27K), LLB1-7 (77K), JUMBO (339)
- Servicer Risk: Neutral (114K), Exposed (28K), Protected (15K)
- Avg Composite Score: 47.8

---

## 🔧 Infrastructure

| Component | Details |
|-----------|---------|
| **Cloud SQL** | `oasive-postgres` (PostgreSQL) |
| **GCS Bucket** | `oasive-raw-data` |
| **Cloud Run Jobs** | `freddie-ingestor`, `freddie-parser` |
| **VPC Connector** | `data-feeds-vpc-1` (for SFTP egress) |
| **Service Account** | `cloud-run-jobs-sa@gen-lang-client-0343560978.iam.gserviceaccount.com` |

**⚠️ CRITICAL:** Freddie SFTP cannot be tested locally - requires whitelisted IP via Cloud Run.

---

## 📁 Key Files

| File | Purpose |
|------|---------|
| `src/ingestors/freddie_ingestor.py` | SFTP download with retry logic |
| `src/parsers/freddie_parser.py` | Parse ZIPs → database |
| `src/tagging/pool_tagger.py` | **AI tagging engine** (1192 pools/sec) |
| `src/db/connection.py` | Cloud SQL connector |
| `docs/ai_tagging_design.md` | **Full AI tagging specification** |
| `docs/prepay_research_framework.md` | Empirical validation plan |
| `migrations/004_freddie_data_schema.sql` | Core data schema |
| `migrations/008_ai_tagging_schema.sql` | AI tag columns + factor_multipliers |
| `migrations/007_add_foreign_keys.sql` | FK constraints (pending) |

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

- Job timeout increased to 2 hours (was failing at 1 hour)
- Alert policy sends both firing + recovery emails (need to disable recovery in Cloud Console)
- FRE_FISS files are headerless (9 columns, no header row)
- Servicer classification already being applied during parsing
- **AI tagging is auto-integrated into parser** - runs after each file is processed
- Use `--no-tag` flag to disable auto-tagging if needed

---

## 🔗 Repository

GitHub: https://github.com/anaishowland/oasive_db

All code committed. `.env` and credentials are gitignored.
