# Agency MBS Data Ingestion Guide

This document covers data access and ingestion for Fannie Mae, Freddie Mac, and Ginnie Mae MBS disclosure data.

---

## Fannie Mae (FNM)

### Access Method: SFTP (PoolTalk)

| Resource | URL |
|----------|-----|
| SFTP Server | `fanniemae.mbs-securities.com` |
| Glossary/Data Dictionary | [mbsglossary.pdf](https://capitalmarkets.fanniemae.com/sites/g/files/koqyhd216/files/2025-02/mbsglossary.pdf) |
| Historical Data (SFLP) | [Data Dynamics](https://datadynamics.fanniemae.com) |

**Status:** ⏳ Pending IP whitelist for SFTP access

### File Names & Release Schedule

| File Type | Pattern | Release Time (ET) |
|-----------|---------|-------------------|
| Intraday loan-level | `FNM_ILLD_YYYYMMDD_{1..4}` | ~6:30, 10:30, 13:30, 15:30 |
| Intraday security | `FNM_IS_YYYYMMDD_{1..4}` | ~6:30, 10:30, 13:30, 15:30 |
| Month-end issuance | `FNM_ILLD_YYYYMM`, `FNM_IS_YYYYMM` | BD1 6:30 AM |
| Monthly disclosure | `FNM_MLLD_YYYYMM`, `FNM_MF_YYYYMM` | BD4 4:30 PM |
| Corrections | `FNM_RIS_YYYYMM`, `FNM_RISS_YYYYMM` | BD1-BD4 6:30 AM |

---

## Freddie Mac (FRE)

### Access Method: SFTP (CSS)

| Resource | URL |
|----------|-----|
| SFTP Server | CSS SFTP (IP whitelisted: `34.121.116.34`) |
| Historical Data (SFLLD) | [Clarity Platform](https://capitalmarkets.freddiemac.com/clarity) |
| Support | `Investor_Inquiry@freddiemac.com` / (800) 336-3672 |

**Status:** ✅ Live - 45,356 files in catalog

### File Names & Release Schedule ✅ VERIFIED from SFTP timestamps

| File Type | Pattern | Release Time (ET) |
|-----------|---------|-------------------|
| Daily security issuance | `FRE_FISS_YYYYMMDD.zip` | ~10:30 AM (EDT) / ~11:30 AM (EST) |
| Monthly security issuance | `FRE_IS_YYYYMM.zip` | BD1 ~5:30 AM (EDT) / ~6:30 AM (EST) |
| Monthly loan-level | `FRE_ILLD_YYYYMM.zip` | BD1 |
| Monthly factor/prepay | `FRE_DPR_YYYYMM.zip` | BD4-6 |

⚠️ **Note**: Unlike Fannie Mae (4 intraday releases), Freddie Mac only releases ONCE per day.

### Parsing Performance (Cloud Run)

The following benchmarks are based on observed Cloud Run job executions:

| File Type | Files/Job | Duration | Records | Notes |
|-----------|-----------|----------|---------|-------|
| **FRE_FISS** (daily) | 1 | ~1-2 min | ~100-500 pools | Small daily files, fast |
| **FRE_IS** (monthly) | 5 | ~5-10 min | ~5,000 pools | Pool-level issuance data |
| **FRE_ILLD** (loan-level) | 2 | ~30-60 min | ~200K loans | Large files, batch inserts |
| **FRE_GE** (geographic) | 4-5 | ~10-15 min | Distribution stats | Updates existing pools |
| **FRE_DPR** (factors) | 2 | ~5-10 min | ~10K factor records | Monthly prepay data |

**AI Tagging:** After parsing, the `PoolTagger` runs automatically at **~1,200 pools/sec** (optimized batch updates).

**Daily Pipeline Timeline:**
```
16:45 UTC - Cloud Scheduler triggers freddie-ingestor-daily
           ↓ (download FISS from SFTP: ~1 min)
           ↓ (parse to dim_pool: ~1-2 min)
           ↓ (auto-tag new pools: <1 sec for ~100 pools)
~16:50 UTC - Complete (total: ~5 minutes)
```

**Monthly Pipeline Timeline (BD1):**
```
11:45 UTC - Cloud Scheduler triggers freddie-ingestor-monthly
           ↓ (download IS + ILLD from SFTP: ~5-10 min)
           ↓ (parse pools: ~10 min)
           ↓ (parse loans: ~30-60 min depending on volume)
           ↓ (auto-tag new pools: ~2 min for ~5K pools)
~12:45 UTC - Complete (total: ~1 hour)
```

**Resource Configuration (Cloud Run):**
- Memory: 2-4 GiB
- CPU: 2 vCPUs
- Timeout: 4 hours (parser), 2 hours (ingestor)
- Batch size: 10,000 records per insert

---

## Ginnie Mae (GNMA)

### Access Method: HTTP Bulk Download (No SFTP/API)

⚠️ **Important**: Unlike Freddie Mac and Fannie Mae, Ginnie Mae does NOT offer SFTP feeds or REST APIs. Data must be downloaded via authenticated HTTP from their bulk download portal.

| Resource | URL |
|----------|-----|
| Bulk Download Portal | [bulk.ginniemae.gov](https://bulk.ginniemae.gov/) |
| Account Setup | [Disclosure Data Download Account](https://www.ginniemae.gov/data_and_reports/disclosure_data/Pages/datadownload_bulk.aspx) |
| File Layouts & Samples | [Layout Documentation](https://www.ginniemae.gov/data_and_reports/disclosure_data/Pages/bulk_data_download_layout.aspx) |
| Data Dictionaries | [Data Dictionaries](https://www.ginniemae.gov/investors/disclosures_and_reports/pages/disclosure-data-dictionaries.aspx) |
| Release Schedule | [Data Release Schedule](https://www.ginniemae.gov/investors/disclosures_and_reports/Pages/Disclosure-Data-Release-Schedule.aspx) |
| Investor Inquiries | `InvestorInquiries@HUD.gov` |

**Status:** ✅ Account created - Need to build HTTP download pipeline

### File Types (Single Family MBS)

| Category | File | Description | Size |
|----------|------|-------------|------|
| **Daily New Issues** | `dailySFPS.zip` | Pool/Security level | ~108 KB |
| | `dailySFS.zip` | Pool Supplemental | ~1.3 MB |
| | `dailyll_new.zip` | Loan Level | ~2.5 MB |
| **Monthly New Issues** | `nimonSFPS_YYYYMM.zip` | Pool/Security | ~179 KB |
| | `nimonSFS_YYYYMM.zip` | Pool Supplemental | ~2 MB |
| | `dailyllmni.zip` | Loan Level | ~3.5 MB |
| **Portfolio** | `monthlySFPS_YYYYMM.zip` | Pool/Security | ~19 MB |
| | `monthlySFS_YYYYMM.zip` | Pool Supplemental | ~179 MB |
| | `llmon1_YYYYMM.zip` | Loan Level, Ginnie I | ~17 MB |
| | `llmon2_YYYYMM.zip` | Loan Level, Ginnie II | ~340 MB |
| **Liquidations** | `llmonliq_YYYYMM.zip` | Monthly liquidations | ~2.4 MB |
| **Factor Files** | `factorA1_YYYYMM.zip` | Factor A, Ginnie I | ~3 MB |
| | `factorA2_YYYYMM.zip` | Factor A, Ginnie II | ~7.5 MB |
| | `factorB1_YYYYMM.zip` | Factor B, Ginnie I | ~3 MB |
| | `factorB2_YYYYMM.zip` | Factor B, Ginnie II | ~7.5 MB |

### Release Schedule (Official from Ginnie Mae)

**Reference:** [Data Release Schedule](https://www.ginniemae.gov/investors/disclosures_and_reports/Pages/Disclosure-Data-Release-Schedule.aspx)

| File Type | Release Day | Release Time (ET) | UTC |
|-----------|-------------|-------------------|-----|
| **MBS SF Daily New Issues** (Pool/Security, Loan Level) | Tue-Sat | 6:00 AM | 11:00 |
| **HMBS Daily New Issues** | Tue-Sat | 7:00 AM | 12:00 |
| **MBS SF Monthly New Issues** | BD1 | 10:00 PM | 03:00+1 |
| **HMBS Monthly New Issues** | BD1 | 7:00 AM | 12:00 |
| **MBS SF Portfolio** (Pool/Security, Supplemental) | BD6 | 6:00 PM | 23:00 |
| **MBS SF Portfolio** (Loan Level Ginnie I & II) | BD6 | 6:00 PM | 23:00 |
| **Loan Liquidations Monthly** | BD4 | 9:30 PM | 02:30+1 |
| **Factor A** (GI, GII, Platinum) | BD4 | 9:30 PM | 02:30+1 |
| **Factor B** (GI, GII) | BD6 | 7:30 PM | 00:30+1 |
| **REMIC Factors** | BD6 | 2:00 AM | 07:00 |

**Notes:**
- BD = Business Day of the month (excludes weekends, federal holidays)
- Files may be posted earlier than scheduled
- All times are Eastern Time (ET)

### Historical Data Coverage

| Data Type | Coverage | Notes |
|-----------|----------|-------|
| Loan-level | 2013-present | GNMA started loan-level disclosure in Jan 2014 |
| Pool-level | March 2012 and earlier | "Disclosure Data History" files - aggregate stats only |

⚠️ **Historical Limitation**: Unlike Freddie Mac SFLLD (1999-2025) and Fannie Mae SFLP (2000-2025), Ginnie Mae does NOT have loan-level data going back 25+ years. Only ~12 years of loan-level history is available.

### Automation Approach

⚠️ **Important**: The bulk.ginniemae.gov site uses JavaScript-based bot protection. Simple HTTP requests (requests/curl) get 302 redirected. **Must use headless browser (Playwright)**.

```python
# src/ingestors/ginnie_ingestor.py - Playwright approach
from playwright.sync_api import sync_playwright
from google.cloud import storage

BULK_URL = "https://bulk.ginniemae.gov/"

def download_ginnie_files(mode="daily"):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        # Navigate and wait for JS bot check to pass
        page.goto(BULK_URL, wait_until="networkidle")
        
        # Parse file table from rendered HTML
        files = page.query_selector_all("table a[href$='.zip'], table a[href$='.txt']")
        
        for link in files:
            filename = link.inner_text()
            if should_download(filename, mode):
                # Click to download, then upload to GCS
                with page.expect_download() as download_info:
                    link.click()
                download = download_info.value
                upload_to_gcs(download.path(), f"gs://oasive-raw-data/ginnie/raw/{filename}")
        
        browser.close()
```

**Cloud Run Implementation:**
1. Create Docker image with Playwright + Chromium installed
2. `ginnie_ingestor.py` navigates via headless browser, downloads files
3. Compare file list against `ginnie_file_catalog` table to avoid re-downloads
4. Upload to GCS (`gs://oasive-raw-data/ginnie/raw/`)
5. Schedule via Cloud Scheduler:

| Job | Schedule (UTC) | Purpose |
|-----|----------------|---------|
| `ginnie-ingestor-daily` | `0 11 * * 2-6` | Daily new issues (6 AM ET) |
| `ginnie-ingestor-monthly-bd1` | `0 3 * * *` | Monthly new issues (10 PM ET BD1) |
| `ginnie-ingestor-monthly-bd6` | `0 23 * * *` | Portfolio + loan-level (6 PM ET BD6) |
| `ginnie-ingestor-factor` | `0 2 * * *` | Factor files (9 PM ET BD4-6) |

**Note:** BD-specific scheduling requires calendar logic to determine actual business days.

---

## GCP Architecture

* **Storage**: GCS buckets with versioned raw zips
  - `gs://oasive-raw-data/freddie/raw/` - Freddie SFTP files
  - `gs://oasive-raw-data/fannie/raw/` - Fannie SFTP files (future)
  - `gs://oasive-raw-data/ginnie/raw/` - Ginnie HTTP downloads
* **Compute**: Cloud Run jobs for ingestion and parsing, triggered by Cloud Scheduler
* **Orchestration**: Cloud Scheduler + Pub/Sub fan-out
* **Warehouse**: PostgreSQL in Cloud SQL
* **API**: FastAPI on Cloud Run (future)
* **UI**: NextJS + Tailwind on Cloud Run (future)

---

## Data Quality & Change Management

* Track intraday vs month-end vs monthly file provenance and `loan_correction_ind` if present
* Keep raw, staged, and canonical layers
* Build a small dashboard for file arrivals, row deltas, and schema drifts

---

## Data Schema Summary

* **dim_pool** (pool_id, cusip, prefix, product, coupon, wam_iss, wala_iss, issue_dt, issuer, servicer_id, arm_flag, ...)
* **dim_loan** (loan_id, pool_id, first_pay_dt, note_rate, orig_upb, fico, ltv, dti, state, msa, purpose, occ, prop_type, loan_term, ...)
* **fact_pool_month** (pool_id, as_of_month, loan_count, factor, upb, paydown_prin, delinq_30_60_90, invol_removals, ...)
* **fact_loan_month** (loan_id, pool_id, as_of_month, curr_upb, status, dlq_status, mod_flag, forbear_flag, ...)
* **dim_calendar** (as_of_month, bd1_dt, bd4_dt, holiday_flag, ...)

**Note**: Loan-level monthly files exclude paid-off loans for subsequent months, while factor files reflect decreased loan count and factors when loans pay off. Your snapshot model should respect that.


