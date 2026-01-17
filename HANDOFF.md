# Agent Handoff Document

**Last updated:** January 16, 2026

This document provides context for AI agents continuing development on Oasive.

---

## 🎯 Mission

Build an AI-powered **securitized products analytics platform** covering the **entire mortgage market**:

1. **Single-Family MBS** (Agency)
   - Freddie Mac, Fannie Mae, Ginnie Mae pools
   - Fixed-rate, ARMs, IOs, HARP loans
   
2. **Multifamily/Commercial MBS** (Agency)
   - Fannie Mae Multifamily loans
   - DSCR and credit metrics
   
3. **Non-Agency** (Future)
   - Private-label MBS
   - CMOs, ABS

**Core Capabilities:**
- Ingest disclosure data from all GSE sources
- Tag pools with AI-generated behavioral characteristics
- Enable semantic search ("show me prepay-protected pools")
- Support empirical prepay research across ALL loan types

**⚠️ SCOPE NOTE:** We want the ENTIRE mortgage market, not just single-family conforming loans. This includes ARMs, IOs, HARP, Multifamily, and eventually CMOs and non-agency.

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

**Download recommendation**: Download the "2000Q1-2025Q2 Acquisition and Performance File" at the top for complete historical coverage.

**Fannie Mae Ingestion Tools (Ready to Use):**
```bash
# Check status
python3 -m src.ingestors.fannie_sflp_ingestor --status

# Process downloaded files from local directory
python3 -m src.ingestors.fannie_sflp_ingestor --process ~/Downloads/fannie_sflp

# Process from GCS (after uploading)
python3 -m src.ingestors.fannie_sflp_ingestor --process-gcs gs://oasive-raw-data/fannie/sflp
```

**Fannie Mae Tables (Migration 011):**
- `dim_loan_fannie_historical` - Acquisition data (62M loans, 2000-2025)
- `fact_loan_month_fannie_historical` - Monthly performance snapshots
- `fannie_sflp_file_catalog` - File tracking
- `v_all_historical_loans` - Unified view combining Freddie + Fannie

### Ginnie Mae Data Access

⚠️ **Key Difference**: Unlike Freddie Mac and Fannie Mae, Ginnie Mae does NOT offer SFTP feeds or REST APIs. Data must be downloaded via authenticated HTTP.

| Source | URL | Coverage | Status |
|--------|-----|----------|--------|
| **Bulk Download Portal** | `bulk.ginniemae.gov` | Current month only | ✅ Working (58 files, 4.94 GB) |
| **Disclosure Data History** | `ginniemae.gov/.../DisclosureHistory.aspx` | **2013-present** | ✅ Available |

**⚠️ IMPORTANT DISCOVERY (Jan 16, 2026):**
- `bulk.ginniemae.gov` only has **current month** files
- **Historical files (2013+) are on a DIFFERENT page**: https://www.ginniemae.gov/data_and_reports/disclosure_data/Pages/DisclosureHistory.aspx
- This page has **all historical loan-level data back to 2013**!

**Key Resources:**
- Current Month: https://bulk.ginniemae.gov/
- **Historical Data: https://www.ginniemae.gov/data_and_reports/disclosure_data/Pages/DisclosureHistory.aspx**
- File Layouts: https://www.ginniemae.gov/data_and_reports/disclosure_data/Pages/bulk_data_download_layout.aspx
- Historical Layouts Guide: Available on the Disclosure History page
- Contact: `InvestorInquiries@HUD.gov`

### Ginnie Mae Historical File Categories (Complete Inventory)

**Download URL Pattern:** `https://bulk.ginniemae.gov/protectedfiledownload.aspx?dlfile=data_history_cons\<filename>`

**MBS Single Family Files:**

| Category | Prefix | First Available | Est. Files | Download Status | Priority |
|----------|--------|-----------------|------------|-----------------|----------|
| MBS SF MONTHLY NEW ISSUES - POOL/SECURITY | `nimonSFPS` | **2020-01** | ~72 | ⏳ Pending | Medium |
| MBS SF MONTHLY NEW ISSUES - POOL SUPPLEMENTAL | `nimonSFS` | **2020-01** | ~72 | ⏳ Pending | Low |
| MBS SF MONTHLY NEW ISSUES - LOAN LEVEL | `dailyllmni` | **2013-09** | ~148 | ⏳ Pending | ⭐ High |
| MBS SF PORTFOLIO - POOL/SECURITY | `monthlySFPS` | **2020-01** | ~72 | ⏳ Pending | Medium |
| MBS SF PORTFOLIO - POOL SUPPLEMENTAL | `monthlySFS` | **2020-01** | ~72 | ⏳ Pending | Low |
| MBS SF PORTFOLIO - LOAN LEVEL, GINNIE I | `llmon1` | **2013-10** | ~146 | ✅ Cataloged (146) | ⭐ High |
| MBS SF PORTFOLIO - LOAN LEVEL, GINNIE II | `llmon2` | **2013-10** | ~146 | ⏳ Pending | ⭐ High |
| MBS SF LOAN LIQUIDATIONS MONTHLY | `llmonliq` | **2018-09** | ~88 | ⏳ Pending | ⭐ High |
| MBS MONTHLY (NI) – POOL LEVEL | `nissues` | **2012-02** | ~166 | ⏳ Pending | Medium |
| MBS (PORTFOLIO) | `monthly` | **2012-02** | ~166 | ⏳ Pending | Medium |

**Factor Files:**

| Category | Prefix | First Available | Est. Files | Download Status | Priority |
|----------|--------|-----------------|------------|-----------------|----------|
| FACTOR A G I | `factorA1` | **2012-08** | ~160 | ⏳ Pending | ⭐ High |
| FACTOR A G II | `factorA2` | **2012-08** | ~160 | ⏳ Pending | ⭐ High |
| FACTOR A PLATINUM | `factorAplat` | **2019-06** | ~67 | ⏳ Pending | Medium |
| FACTOR A ADDITIONAL | `factorAAdd` | **2015-09** | ~111 | ⏳ Pending | Low |
| FACTOR B G I | `factorB1` | **2012-08** | ~160 | ⏳ Pending | ⭐ High |
| FACTOR B G II | `factorB2` | **2012-08** | ~160 | ⏳ Pending | ⭐ High |
| REMIC 1 FACTOR | `remic1` | **2012-02** | ~166 | ⏳ Pending | Low |
| REMIC 2 FACTOR | `remic2` | **2012-02** | ~166 | ⏳ Pending | Low |
| FRR HISTORY | `FRR` | **2015-03** | ~130 | ⏳ Pending | Low |
| SRF HISTORY | `SRF` | **2015-03** | ~130 | ⏳ Pending | Low |

**Priority Legend:**
- ⭐ High: Essential for prepay research (loan-level, liquidations, factor data)
- Medium: Useful for pool-level analysis
- Low: Supporting data, download after high-priority complete

**Download Commands:**
```bash
# Download specific MBS SF category
python3 -m src.ingestors.ginnie_ingestor --mode=historical-mbs-sf --historical-category=llmon1

# Download specific Factor category  
python3 -m src.ingestors.ginnie_ingestor --mode=historical-factor --historical-category=factorA1

# Download with file limit for testing
python3 -m src.ingestors.ginnie_ingestor --mode=historical-mbs-sf --historical-category=llmon1 --max-files=5
```

**Storage Location:** `gs://oasive-raw-data/ginnie/historical/<prefix>/<year>/<month>/<filename>`

**⚠️ Note**: Unlike Freddie/Fannie SFLLD (1999-2025), Ginnie loan-level data starts ~2013.

**Ginnie Mae Ingestion Pipeline (WORKING):**

```bash
# Current month files (daily scheduled job)
python3 -m src.ingestors.ginnie_ingestor --mode=daily

# Historical MBS SF files (one category)
python3 -m src.ingestors.ginnie_ingestor --mode=historical-mbs-sf --historical-category=llmon1

# Historical Factor files (one category)
python3 -m src.ingestors.ginnie_ingestor --mode=historical-factor --historical-category=factorA1

# All historical files
python3 -m src.ingestors.ginnie_ingestor --mode=historical-all
```

### Ginnie Mae Historical Data Download Plan

**Phase 1 - High Priority (Loan-Level for Prepay Research):**
| Category | Files | Est. Size | Status |
|----------|-------|-----------|--------|
| `llmon1` - Loan Level Ginnie I | 146 | 5.03 GB | ✅ **Complete** |
| `llmon2` - Loan Level Ginnie II | ~146 | ~5 GB | 🔄 Downloading |
| `dailyllmni` - Loan Level New Issues | ~148 | ~1.5 GB | 🔄 Downloading |
| `llmonliq` - Liquidations | ~88 | ~1 GB | 🔄 Downloading |

**Phase 2 - Factor Data (for CPR Calculation):**
| Category | Files | Est. Size | Status |
|----------|-------|-----------|--------|
| `factorA1` - Factor A Ginnie I | ~160 | ~500 MB | 🔄 Downloading |
| `factorA2` - Factor A Ginnie II | ~160 | ~500 MB | 🔄 Downloading |
| `factorB1` - Factor B Ginnie I | ~160 | ~500 MB | 🔄 Downloading |
| `factorB2` - Factor B Ginnie II | ~160 | ~500 MB | 🔄 Downloading |

**Phase 3 - Pool-Level Data:**
| Category | Files | Est. Size | Status |
|----------|-------|-----------|--------|
| `monthlySFPS` - Portfolio Pool | ~72 | ~2 GB | 🔄 Downloading |
| `nimonSFPS` - New Issues Pool | ~72 | ~500 MB | 🔄 Downloading |
| `monthlySFS` - Portfolio Supplemental | ~72 | ~500 MB | 🔄 Downloading |
| `nimonSFS` - New Issues Supplemental | ~72 | ~500 MB | 🔄 Downloading |
| `nissues` - MBS Monthly NI Pool | ~166 | ~1 GB | 🔄 Downloading |
| `monthly` - MBS Portfolio | ~166 | ~1 GB | 🔄 Downloading |

**Phase 4 - Supporting Data:**
| Category | Files | Est. Size | Status |
|----------|-------|-----------|--------|
| `remic1` - REMIC 1 Factor | ~166 | ~500 MB | 🔄 Downloading |
| `remic2` - REMIC 2 Factor | ~166 | ~500 MB | 🔄 Downloading |
| `factorAplat` - Factor A Platinum | ~67 | ~100 MB | 🔄 Downloading |
| `factorAAdd` - Factor A Additional | ~111 | ~200 MB | 🔄 Downloading |

**Total Estimated:** ~2,000 files, ~20 GB

### Data Integration Plan

**Unified Views (combining Freddie + Fannie + Ginnie):**
- `v_all_agency_loans` - All loan-level data across agencies
- `v_all_agency_pools` - All pool-level data across agencies  
- `v_all_agency_factors` - Combined factor/CPR data

**Schema Alignment:**
| Freddie | Fannie | Ginnie | Unified View |
|---------|--------|--------|--------------|
| `dim_loan_historical` | `dim_loan_fannie_historical` | `dim_loan_ginnie` | `v_all_agency_loans` |
| `fact_loan_month_historical` | `fact_loan_month_fannie_historical` | `fact_loan_month_ginnie` | `v_all_agency_performance` |
| `dim_pool` | `dim_pool_fannie` | `dim_pool_ginnie` | `v_all_agency_pools` |

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

### Phase 6: Historical Data (SFLLD + Fannie SFLP) 🔄 In Progress
**Goal:** Load 54.8M Freddie + 62M Fannie historical loans for cross-cycle prepay research

#### Freddie Mac SFLLD (1999-2025)

| Task | Status | Details |
|------|--------|---------|
| Create schema | ✅ Done | Migration 009: `dim_loan_historical`, `fact_loan_month_historical` |
| Create ingestor | ✅ Done | `src/ingestors/sflld_ingestor.py` with GCS support |
| Download 1999-2008 | ✅ Done | Processed in first batch |
| **1999-2008 loaded** | ✅ **Done** | **18.6M loans in `dim_loan_historical`** |
| **Re-download full dataset** | ✅ **Done** | `full_set_standard_historical_data.zip` (37 GB) |
| **Upload to GCS** | 🔄 **In Progress** | Uploading to `gs://oasive-raw-data/sflld/` |
| **Non-standard dataset** | ⏳ Pending | `non_std_historical_data.zip` (4.4 GB) - ARMs, IOs |
| Cloud Run processor | ⏳ Ready | `sflld-processor` job (Gen2, 8Gi, 24h timeout) |

#### Fannie Mae SFLP (2000-2025)

| Task | Status | Details |
|------|--------|---------|
| Create schema | ✅ Done | Migration 011: `dim_loan_fannie_historical` |
| Create ingestor | ✅ Done | `src/ingestors/fannie_sflp_ingestor.py` with GCS support |
| Download file | ✅ Done | `Performance_All.zip` (56 GB) |
| Upload to GCS | ✅ **Done** | `gs://oasive-raw-data/fannie/sflp/Performance_All.zip` |
| Cloud Run processor | 🔄 **Running** | `fannie-sflp-processor-n924r` (Gen2, 8Gi, 24h timeout) |
| Parse loan data | 🔄 In Progress | ~62M loans expected |

**Cloud Run Configuration (Updated Jan 16):**
- Execution environment: **Gen2** (for larger ephemeral storage)
- Memory: 8 GiB
- CPU: 4 vCPUs
- Timeout: 24 hours

### Phase 7: Ginnie Mae Data Ingestion ✅ FULLY WORKING
**Goal:** Ingest GNMA pool and loan-level data via HTTP bulk download

| Task | Status | Details |
|------|--------|---------|
| Research data access | ✅ Done | No SFTP/API - HTTP bulk download only |
| Account created | ✅ Done | `anais@oasive.ai` (email + security question auth) |
| Create schema | ✅ Done | Migration 012 applied |
| Build Playwright ingestor | ✅ Done | `src/ingestors/ginnie_ingestor.py` |
| **Full auth flow** | ✅ Working | Email → Submit → Security Question → Verify |
| Create file catalog | ✅ Done | 58 files cataloged in `ginnie_file_catalog` |
| Daily download pipeline | ✅ Deployed | `ginnie-ingestor` Cloud Run + 3 schedulers |
| **Current month download** | ✅ Done | **58 files, 4.94 GB** in GCS |
| **Historical backfill** | ❌ Blocked | Ginnie Mae only provides current month files |
| Create parser | ⏳ Pending | Stub exists, needs file layout specs |

**Authentication Flow (Jan 16, 2026 - WORKING):**
1. Navigate to download URL → Redirects to profile.aspx
2. Enter email (anais@oasive.ai) → Click Submit
3. Security question: "In what city did you meet your spouse..." → Answer: "Berkeley"
4. Click Verify → Download starts automatically

**Secrets in GCP Secret Manager:**
- `ginnie-security-answer`: "Berkeley"
- `ginnie-session-cookies`: Browser cookies for session persistence

**Files Downloaded (Jan 2026 - VERIFIED):**
- Total: **58 files, 4.94 GB**
- Pool-level: `monthlySFPS_202512.zip` (18 MB), `dailySFPS.zip`
- Loan-level: `LoanPerfAnn_202412.zip` (2.75 GB), `llmon1_202512.zip`
- Factor files: `factorA1/A2/B1/B2_202512.zip`
- HMBS: `hmonthlyPS_202512.zip`, `hllmon1_202512.zip`

**⚠️ Historical Data Limitation:** Ginnie Mae bulk download portal only provides **current month** files. Historical URLs like `llmon1_201301.zip` return "There was an issue processing your request" error - the files do not exist at those URLs.

**Historical data options:**
1. Contact `InvestorInquiries@HUD.gov` to request historical data access
2. Data vendors: Bloomberg, Intex, CoreLogic
3. Check "Disclosure Data History" page (covers periods before March 2012 only)

---

## Ginnie Mae Implementation Plan

### Why Playwright (Not Simple HTTP)?

Testing confirmed that `bulk.ginniemae.gov` uses JavaScript-based bot protection:
- Direct HTTP requests get 302 redirect to `/?check=XXXXX`
- Page requires JavaScript execution to pass validation
- **Solution**: Use Playwright (headless browser) in Cloud Run

### Files to Download (Equivalent to Freddie Data)

**Daily Files** (posted ~4:50-5:04 AM ET, Tue-Sat):
| Ginnie File | Equivalent Freddie File | Purpose |
|-------------|------------------------|---------|
| `dailySFPS.zip` | `FRE_FISS_*.zip` | Daily pool/security issuance |
| `dailySFS.zip` | - | Pool supplemental (extended attributes) |
| `dailyll_new.zip` | - | Daily loan-level issuance |

**Monthly Files** (posted BD1-BD6):
| Ginnie File | Release | Equivalent Freddie | Purpose |
|-------------|---------|-------------------|---------|
| `nimonSFPS_YYYYMM.zip` | BD1 10PM | `FRE_IS_*.zip` | Monthly new issues pool |
| `nimonSFS_YYYYMM.zip` | BD1 10PM | - | Monthly new issues supplemental |
| `dailyllmni.zip` | BD1 | `FRE_ILLD_*.zip` | Monthly new issues loan-level |
| `monthlySFPS_YYYYMM.zip` | BD6 6PM | - | Portfolio pool/security |
| `monthlySFS_YYYYMM.zip` | BD6 6PM | - | Portfolio supplemental |
| `llmon1_YYYYMM.zip` | BD6 6PM | - | Loan-level portfolio (Ginnie I) |
| `llmon2_YYYYMM.zip` | BD6 6PM | - | Loan-level portfolio (Ginnie II) |
| `llmonliq_YYYYMM.zip` | BD4 9:30PM | - | Loan liquidations |

**Factor Files** (BD4-BD6):
| Ginnie File | Release | Purpose |
|-------------|---------|---------|
| `factorA1_YYYYMM.zip` | BD4 9PM | Factor A, Ginnie I |
| `factorA2_YYYYMM.zip` | BD4 9PM | Factor A, Ginnie II |
| `factorB1_YYYYMM.zip` | BD6 7PM | Factor B, Ginnie I |
| `factorB2_YYYYMM.zip` | BD6 7PM | Factor B, Ginnie II |

### Historical Data Backfill

**Loan-Level (2013-present):**
- Download monthly portfolio files (`llmon1/llmon2_YYYYMM.zip`) from Disclosure Data History
- ~12 years × 12 months × 2 files = ~288 historical files

**Pool-Level (Pre-2012):**
- Download "Disclosure Data History" files (aggregate stats)
- Covers: Pool issuance dates, WAC, WAM, delinquency rates, etc.
- NOT individual loan attributes, but useful for historical prepay research

### Cloud Run Schedule (UTC)

| Job | Schedule (UTC) | Schedule (ET) | Files Downloaded |
|-----|----------------|---------------|------------------|
| `ginnie-ingestor-daily` | `0 10 * * 2-6` | 5:00 AM ET (Tue-Sat) | Daily new issues |
| `ginnie-ingestor-monthly-bd1` | `0 3 1-7 * *` | 10:00 PM ET BD1 | Monthly new issues |
| `ginnie-ingestor-monthly-bd6` | `0 23 6-12 * *` | 6:00 PM ET BD6 | Portfolio + loan-level |
| `ginnie-ingestor-factor` | `0 2 4-10 * *` | 9:00 PM ET BD4-6 | Factor files |

**Note:** BD schedules need calendar logic to determine actual business days.

### Database Schema (Migration 012)

```sql
-- Ginnie Mae file catalog
CREATE TABLE ginnie_file_catalog (
  id SERIAL PRIMARY KEY,
  filename TEXT UNIQUE NOT NULL,
  file_type TEXT NOT NULL,  -- daily_pool, daily_loan, monthly_pool, monthly_loan, factor, historical
  file_date DATE,
  file_size_bytes BIGINT,
  download_status TEXT DEFAULT 'pending',  -- pending, downloaded, processed, error
  local_gcs_path TEXT,
  downloaded_at TIMESTAMPTZ,
  processed_at TIMESTAMPTZ,
  error_message TEXT
);

-- Ginnie Mae pool dimension (similar to dim_pool but GNMA-specific)
CREATE TABLE dim_pool_ginnie (
  pool_id TEXT PRIMARY KEY,
  cusip TEXT UNIQUE,
  security_type TEXT,  -- GNM1, GNM2, HMBS, Platinum
  product_type TEXT,   -- 30YR, 15YR, ARM
  coupon NUMERIC(5,3),
  issue_date DATE,
  maturity_date DATE,
  orig_upb NUMERIC(15,2),
  orig_loan_count INTEGER,
  wac NUMERIC(5,3),
  wam INTEGER,
  wala INTEGER,
  avg_fico INTEGER,
  avg_ltv NUMERIC(5,2),
  issuer_id TEXT,
  issuer_name TEXT,
  program_type TEXT,   -- FHA, VA, USDA, RD
  -- AI tags (same as dim_pool)
  loan_balance_tier TEXT,
  fico_bucket TEXT,
  ltv_bucket TEXT,
  servicer_prepay_risk TEXT,
  composite_prepay_score NUMERIC,
  behavior_tags JSONB,
  tags_updated_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Ginnie Mae loan dimension
CREATE TABLE dim_loan_ginnie (
  loan_id TEXT PRIMARY KEY,
  pool_id TEXT REFERENCES dim_pool_ginnie(pool_id),
  orig_upb NUMERIC(12,2),
  orig_rate NUMERIC(5,3),
  orig_term INTEGER,
  orig_date DATE,
  first_pay_date DATE,
  fico INTEGER,
  ltv NUMERIC(5,2),
  dti NUMERIC(5,2),
  property_type TEXT,
  occupancy TEXT,
  state TEXT,
  msa TEXT,
  purpose TEXT,
  program_type TEXT,   -- FHA, VA, USDA, RD
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Ginnie Mae monthly pool facts
CREATE TABLE fact_pool_month_ginnie (
  pool_id TEXT REFERENCES dim_pool_ginnie(pool_id),
  as_of_date DATE,
  factor NUMERIC(10,8),
  curr_upb NUMERIC(15,2),
  loan_count INTEGER,
  wac NUMERIC(5,3),
  wala INTEGER,
  smm NUMERIC(8,6),
  cpr NUMERIC(5,2),
  dlq_30_count INTEGER,
  dlq_60_count INTEGER,
  dlq_90_count INTEGER,
  serious_dlq_rate NUMERIC(5,4),
  PRIMARY KEY (pool_id, as_of_date)
);

-- Historical pool stats (pre-2012 aggregate data)
CREATE TABLE ginnie_historical_pool_stats (
  as_of_date DATE,
  security_type TEXT,  -- GNM1, GNM2
  product_type TEXT,
  coupon_bucket TEXT,
  total_upb NUMERIC(18,2),
  pool_count INTEGER,
  loan_count INTEGER,
  avg_wac NUMERIC(5,3),
  avg_wam INTEGER,
  avg_cpr NUMERIC(5,2),
  dlq_90_plus_rate NUMERIC(5,4),
  PRIMARY KEY (as_of_date, security_type, product_type, coupon_bucket)
);
```

### Implementation Steps

1. **Add Playwright to requirements.txt:**
   ```
   playwright==1.40.0
   ```

2. **Create `src/ingestors/ginnie_ingestor.py`:**
   - Use Playwright to navigate to bulk.ginniemae.gov
   - Parse file list from HTML
   - Download files to GCS via streaming
   - Update `ginnie_file_catalog`

3. **Create Cloud Run job with Playwright:**
   - Use Playwright Docker image as base
   - Install Chromium in container
   - Configure for headless execution

4. **Create `src/parsers/ginnie_parser.py`:**
   - Parse downloaded files into database tables
   - Auto-tag pools after parsing

5. **Schedule jobs:**
   - Daily job at 5:00 AM ET (10:00 UTC)
   - Monthly jobs on BD1, BD4, BD6

### Playwright Ingestor Code Outline

```python
# src/ingestors/ginnie_ingestor.py
from playwright.sync_api import sync_playwright
import os
from google.cloud import storage

BULK_URL = "https://bulk.ginniemae.gov/"

def download_ginnie_files(mode="daily", max_files=None):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        # Navigate and wait for JS to execute
        page.goto(BULK_URL, wait_until="networkidle")
        
        # Parse file table
        files = parse_file_table(page)
        
        # Filter by mode (daily, monthly, factor)
        files_to_download = filter_files(files, mode)
        
        # Download each file
        for file_info in files_to_download[:max_files]:
            download_to_gcs(page, file_info)
        
        browser.close()

def download_to_gcs(page, file_info):
    """Click download link and stream to GCS"""
    with page.expect_download() as download_info:
        page.click(f'a:has-text("{file_info["filename"]}")')
    download = download_info.value
    
    # Upload to GCS
    gcs_path = f"gs://oasive-raw-data/ginnie/raw/{file_info['filename']}"
    upload_to_gcs(download.path(), gcs_path)
    
    # Update catalog
    update_file_catalog(file_info, gcs_path)
```

### Data Availability: IMPORTANT LIMITATION

**Unlike Freddie Mac (full SFTP archive), Ginnie Mae only provides CURRENT MONTH data on the bulk download page.**

| Data Type | Availability | Notes |
|-----------|-------------|-------|
| Current month files | ✅ Public | 58 files (daily, monthly, factor) |
| Historical files (2012-present) | ❌ Not public | Protected URLs redirect to login |
| Pre-2012 data | ⚠️ Separate archive | Different format, via Disclosure Data History |

**What this means for research:**
- We CAN automate daily/monthly ingestion of NEW data going forward
- We CANNOT bulk download 13 years of historical data automatically
- For historical backfill, contact Ginnie Mae directly or use data vendors

**Historical data options:**
1. **Contact Ginnie Mae investor relations** - Request historical data access
2. **Data vendors** - Bloomberg, Intex, CoreLogic have historical Ginnie data
3. **Manual download** - If you can get login access, download manually

### Authentication Approach: Fully Automated (Current Data Only)

**Strategy: Primary path (no auth) + Automated fallback (Gmail API magic link)**

Based on testing:
1. **Primary path**: Bulk downloads appear public - Playwright just handles JS bot check
2. **Fallback**: If login required, automate via Gmail API to capture magic link
3. **Monitoring**: Email alerts on any failure with debug screenshots

**Why this is robust:**
- No manual intervention needed (ever)
- Gmail API captures magic links automatically
- Debug screenshots uploaded to GCS for troubleshooting
- Email alerts sent on any failure
- Works reliably for years

**Authentication Flow:**
```
1. Navigate to bulk.ginniemae.gov
   ↓
2. Check: Is login required?
   ├─ NO → Continue to file listing ✓
   └─ YES → Attempt automated login:
            a. Enter anais@oasive.ai in form
            b. Submit to trigger magic link email
            c. Wait 30 seconds for email
            d. Gmail API reads magic link from inbox
            e. Navigate to magic link URL
            f. Save cookies to Secret Manager
            g. Continue to file listing ✓
            (If automated login fails → Send alert email)
```

**Secrets Required:**

| Secret ID | Purpose | How to Set Up |
|-----------|---------|---------------|
| `ginnie-session-cookies` | Browser cookies (auto-saved) | Created automatically |
| `sendgrid-api-key` | Email alerts (optional) | Get from SendGrid |
| `gmail-api-credentials` | Magic link capture (optional) | See setup below |

**Gmail API Setup (for automated magic link):**
```bash
# 1. Enable Gmail API in GCP Console
gcloud services enable gmail.googleapis.com

# 2. Create service account
gcloud iam service-accounts create gmail-reader \
  --display-name="Gmail API Reader"

# 3. For Workspace accounts: Set up domain-wide delegation
#    - Go to admin.google.com → Security → API Controls → Domain-wide Delegation
#    - Add client ID for gmail-reader service account
#    - Grant scope: https://www.googleapis.com/auth/gmail.readonly
#    - Subject: anais@oasive.ai

# 4. Download key and store in Secret Manager
gcloud iam service-accounts keys create gmail-key.json \
  --iam-account=gmail-reader@$PROJECT_ID.iam.gserviceaccount.com

gcloud secrets create gmail-api-credentials \
  --data-file=gmail-key.json

rm gmail-key.json  # Don't keep locally
```

**SendGrid Setup (for email alerts):**
```bash
# 1. Create SendGrid account and API key
# 2. Store in Secret Manager
echo -n "SG.your-api-key" | gcloud secrets create sendgrid-api-key --data-file=-

# 3. Verify domain for sending (optional but recommended)
```

**Alert Notifications:**
- **Auth Required**: Email sent with instructions if automated login fails
- **Page Load Failed**: Email with debug screenshot
- **Sync Error**: Email with error details and stack trace
- All screenshots saved to `gs://oasive-raw-data/ginnie/debug/`

**Account:** `anais@oasive.ai` (email-based magic link auth)

### Implementation Status

| Component | Status | File/Details |
|-----------|--------|--------------|
| Database migration | ✅ Done | `migrations/012_ginnie_schema.sql` |
| Ingestor (Playwright) | ✅ Done | `src/ingestors/ginnie_ingestor.py` |
| **Security Question Auth** | ✅ Done | Reads answer from `ginnie-security-answer` secret |
| Dockerfile | ✅ Done | `Dockerfile` (updated for Playwright) |
| Cloud Run Job | ✅ Deployed | `ginnie-ingestor` in us-central1 |
| **Scheduler Permission** | ✅ Fixed | Added `roles/run.invoker` to service account |
| Cloud Scheduler (Daily) | ✅ Active | `ginnie-ingestor-daily` (11:30 UTC Tue-Sat) |
| Cloud Scheduler (Monthly) | ✅ Active | `ginnie-ingestor-monthly` (11:30 UTC 2nd) |
| Cloud Scheduler (Factor) | ✅ Active | `ginnie-ingestor-factor` (11:30 UTC 5th) |
| **Files Downloaded** | ⚠️ Need Reset | Previous downloads were HTML error pages |
| Parser | ⏳ Pending | `src/parsers/ginnie_parser.py` (needs file layout specs) |

**GCS Location:** `gs://oasive-raw-data/ginnie/raw/2026/01/`

**Secrets (GCP Secret Manager):**
- `ginnie-security-answer`: Security question answer for login
- `ginnie-session-cookies`: Auto-saved browser cookies

**IMPORTANT - Catalog Reset Needed:**
The 58 files in `ginnie_file_catalog` are marked "downloaded" but contain HTML error pages (authentication failures). To properly download:

```sql
-- Reset catalog to re-download (run via Cloud SQL or ingestor)
UPDATE ginnie_file_catalog 
SET download_status = 'pending', 
    local_gcs_path = NULL, 
    downloaded_at = NULL 
WHERE download_status = 'downloaded';
```

Then run: `gcloud run jobs execute ginnie-ingestor --args="--mode,backfill"`

### Deployment Commands

```bash
# 1. Run migration
python3 scripts/run_migrations.py

# 2. Build Docker image
docker build -f Dockerfile.ginnie -t gcr.io/$PROJECT_ID/ginnie-ingestor .
docker push gcr.io/$PROJECT_ID/ginnie-ingestor

# 3. Create Cloud Run job
gcloud run jobs create ginnie-ingestor \
  --image gcr.io/gen-lang-client-0343560978/ginnie-ingestor \
  --memory 2Gi \
  --cpu 2 \
  --task-timeout 3600s \
  --max-retries 1 \
  --region us-central1 \
  --service-account cloud-run-jobs-sa@gen-lang-client-0343560978.iam.gserviceaccount.com \
  --set-cloudsql-instances gen-lang-client-0343560978:us-central1:oasive-postgres \
  --set-secrets POSTGRES_PASSWORD=postgres-password:latest

# 4. Test run
gcloud run jobs execute ginnie-ingestor --region=us-central1 \
  --args="--mode,catalog,--max-files,5"

# 5. Schedule daily job (6 AM ET = 11 AM UTC)
gcloud scheduler jobs create http ginnie-ingestor-daily \
  --schedule="0 11 * * 2-6" \
    --time-zone="UTC" \
  --uri="https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/gen-lang-client-0343560978/jobs/ginnie-ingestor:run" \
    --http-method=POST \
    --oauth-service-account-email=cloud-run-jobs-sa@gen-lang-client-0343560978.iam.gserviceaccount.com
```

---

**Phase 6 Details:**

#### Freddie Mac SFLLD (1999-2025)

| Task | Status | Details |
|------|--------|---------|
| Create schema | ✅ Done | Migration 009: `dim_loan_historical`, `fact_loan_month_historical` |
| Create ingestor | ✅ Done | `src/ingestors/sflld_ingestor.py` with GCS support |
| Download 1999-2008 | ✅ Done | Processed in first batch |
| **1999-2008 loaded** | ✅ **Done** | **18.6M loans in `dim_loan_historical`** |
| **2009-2025 data** | ⚠️ **MISSING** | Needs re-download (see below) |

**⚠️ IMPORTANT: Freddie SFLLD Partial Coverage Issue**

The initial download ran out of local disk space during extraction, so only 1999-2008 data was uploaded to GCS. The 2009-2025 data was never extracted or processed.

**Solution:** Re-download `full_set_standard_historical_data.zip` from Clarity and upload directly to GCS (no local extraction needed). Currently downloading on user's laptop.

**Re-download workflow:**
```bash
# 1. Upload ZIP directly to GCS (no local extraction)
gsutil cp ~/Downloads/full_set_standard_historical_data.zip gs://oasive-raw-data/sflld/

# 2. Cloud Run will extract and process in cloud
gcloud run jobs execute sflld-processor \
  --region=us-central1 \
  --project=gen-lang-client-0343560978 \
  --args="python,-m,src.ingestors.sflld_ingestor,--process-gcs,gs://oasive-raw-data/sflld"
```

#### Fannie Mae SFLP (2000-2025)

| Task | Status | Details |
|------|--------|---------|
| Create schema | ✅ Done | Migration 011: `dim_loan_fannie_historical`, `fact_loan_month_fannie_historical` |
| Create ingestor | ✅ Done | `src/ingestors/fannie_sflp_ingestor.py` |
| Download file | ✅ Done | `Performance_All.zip` (56 GB) |
| Upload to GCS | ✅ **Done** | `gs://oasive-raw-data/fannie/sflp/Performance_All.zip` |
| Cloud Run processor | 🔄 **Running** | `fannie-sflp-processor-zrfsw` started Jan 15 |
| Parse loan data | 🔄 In Progress | ~62M loans expected |

**Fannie Mae Processing (runs in cloud):**
```bash
# Check upload progress
gsutil ls -l gs://oasive-raw-data/fannie/sflp/

# Start cloud processing (after upload completes)
gcloud run jobs execute fannie-sflp-processor \
  --region=us-central1 \
  --project=gen-lang-client-0343560978 \
  --args="-m,src.ingestors.fannie_sflp_ingestor,--process-gcs,gs://oasive-raw-data/fannie/sflp"
```

**Note:** Both ingestors use `ON CONFLICT DO NOTHING` - safe to restart without duplicates.

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

## 📊 Current Database Status (Updated Jan 15, 2026 - Morning)

| Table | Records | Status |
|-------|---------|--------|
| `dim_pool` | **177,278** | ✅ 100% AI tagged |
| `dim_loan` | **6,997,748** | ✅ Complete (SFTP 2019+) |
| `fact_pool_month` | 157,600 | ✅ |
| `freddie_file_catalog` | 45,356 | 76% downloaded |
| `dim_loan_historical` | **18,649,688** | ⚠️ 1999-2008 only (see note) |
| `dim_loan_fannie_historical` | 0 | 🔄 Cloud Run processing |

**Parsing Progress (SFTP 2019+):**
- IS: 200/200 ✅ 
- FISS: 227/227 ✅
- DPR: 34/34 ✅
- ILLD: 81/81 ✅ - 7.0M loans loaded
- Geographic: 59/69 (86%) - 10 remaining

**Historical Data Status:**

| Dataset | Years | Records | Status |
|---------|-------|---------|--------|
| Freddie SFLLD | 1999-2008 | 18.6M | ✅ Loaded |
| Freddie SFLLD | 2009-2025 | ~36M | ⏳ User re-downloading |
| Fannie SFLP | 2000-2025 | ~62M | 🔄 Cloud Run processing |

**⚠️ Freddie SFLLD Gap:** The 2009-2025 data was never extracted due to local disk space limits during initial processing. User is re-downloading the full file for cloud-only processing.

**AI Tag Distribution:**
- Loan Balance: STD (54K), MLB (27K), LLB1-7 (77K), JUMBO (339)
- Servicer Risk: Neutral (114K), Exposed (28K), Protected (15K)
- Avg Composite Score: 47.8

**Data Date Ranges:**
- Pools: 2019-06 to 2025-12 (~6.5 years)
- Loans (SFTP): 1993-04 to 2026-01 (~32 years)
- Historical Freddie: 1999-01 to 2008-12 (partial - needs 2009-2025)
- Historical Fannie: Pending upload

**Next Steps:**
1. 🔄 Fannie Mae Cloud Run processing (started - `fannie-sflp-processor-zrfsw`)
2. ⏳ Freddie SFLLD 2009-2025 re-download (user downloading from Clarity)
3. ⏳ After re-download: Upload to GCS + kick off Cloud Run job
4. ⏳ Delete local historical files after cloud processing verified
5. Calculate CPR from factor time series
6. Validate prepay assumptions using research framework

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
- `gs://oasive-raw-data/fannie/sflp/` - Fannie Mae historical data
- `gs://oasive-raw-data/ginnie/raw/` - Ginnie Mae HTTP downloaded files (future)

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
| `migrations/011_fannie_historical_schema.sql` | **Fannie Mae historical tables** |
| `src/ingestors/fannie_sflp_ingestor.py` | **Fannie Mae SFLP processor** |
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
