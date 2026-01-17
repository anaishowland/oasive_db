# Oasive Database Schema

## Overview

| Database | Instance | Location |
|----------|----------|----------|
| PostgreSQL | `oasive-postgres` | `us-central1` |
| Database Name | `oasive` | |
| Connection | `gen-lang-client-0343560978:us-central1:oasive-postgres` |

### Architecture (3 DB Layer per Business Plan)

1. **Postgres (structured DB)** - Core pool/loan data + AI-generated static tags ← *This document*
2. **Vector index (semantic DB)** - Behavioral embeddings for pattern matching ← *Future*
3. **Knowledge graph** - Entity relationships (servicers, originators, etc.) ← *Future*

---

## FRED Data Tables

### `fred_series` — Series Metadata

Maps indicators to FRED API series. One row per data series.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `id` | SERIAL | NO | Auto-increment PK |
| `series_id` | TEXT | NO | FRED series ID (e.g., "UNRATE") — **UNIQUE** |
| `indicator_id` | TEXT | NO | Internal indicator name — **UNIQUE** |
| `name` | TEXT | NO | Human-readable name |
| `description` | TEXT | YES | Full description |
| `domain` | TEXT | NO | Category: macro, housing, mortgage, policy, rates_curve |
| `subcategory` | TEXT | YES | Sub-category within domain |
| `frequency` | TEXT | YES | daily, weekly, monthly, quarterly |
| `source` | TEXT | YES | Original data source (BLS, Treasury, etc.) |
| `fred_url` | TEXT | YES | Link to FRED page |
| `is_active` | BOOLEAN | YES | Whether to fetch this series (default: true) |
| `data_starts` | DATE | YES | First observation date |
| `created_at` | TIMESTAMPTZ | YES | Row creation time |
| `updated_at` | TIMESTAMPTZ | YES | Last update time |

### `fred_observation` — Time Series Data

Stores actual values. One row per observation date per series.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `series_id` | TEXT | NO | FK → `fred_series.series_id` |
| `obs_date` | DATE | NO | Observation date |
| `value` | NUMERIC | YES | Data value (NULL if FRED returns ".") |
| `vintage_date` | DATE | NO | Revision date (default: 0001-01-01) |
| `raw_payload` | JSONB | YES | Original FRED API response |
| `created_at` | TIMESTAMPTZ | YES | Row creation time |

**Primary Key**: `(series_id, obs_date, vintage_date)`

### `fred_ingest_log` — Ingestion Audit Log

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL | Auto-increment PK |
| `series_id` | TEXT | Which series was processed |
| `run_started_at` | TIMESTAMPTZ | Job start time |
| `run_completed_at` | TIMESTAMPTZ | Job end time |
| `status` | TEXT | running, success, error |
| `rows_inserted` | INTEGER | Count of new rows added |
| `error_message` | TEXT | Error details if failed |

---

## Freddie Mac Tables

### File Types on SFTP Server

| File Type | Count | Description | Status | Priority |
|-----------|-------|-------------|--------|----------|
| **FRE_IS** | 200 | Monthly Issuance Summary (pool-level) | ✅ 100% parsed | Critical |
| **FRE_FISS** | 227 | Intraday Security Issuance | ✅ 100% parsed | Critical |
| **FRE_ILLD** | 81 | Loan-Level Disclosure Data (~14M loans) | 🔄 64% parsed | Critical |
| **FRE_DPR** | 34 | Monthly Factor/Prepay Data | ✅ 100% parsed | Critical |
| **Geographic (ge)** | 85 | Pool distribution stats (MAX/MED/MIN) | Downloaded | Low |
| **Economic (ec)** | 1,788 | 45-day/55-day security mapping | Downloaded | Skip |
| **CUSIP Deal Files** | 21,972 | Historical deal documents | Partial | Skip |
| **PDF Reports** | 8,589 | Prospectuses, supplements | Not needed | Skip |
| **Other** | ~13,000 | Misc data files | Partial | Skip |

**Total**: 45,356 files (82 GB)

### Geographic Files (ge) Analysis

The geographic files contain **distribution statistics per pool**, NOT state-level geographic data.
Each pool has 5 rows with percentile values (MAX/75th/MED/25th/MIN) for:

| Column | Description |
|--------|-------------|
| loan_amt | Loan amount distribution |
| gross_rate | Gross interest rate |
| net_rate | Net interest rate |
| orig_term | Original term |
| rem_term | Remaining term |
| loan_age | Loan age |
| dti | Debt-to-income |
| fico | Credit score |
| ltv | Loan-to-value |

**Status:** Low priority - most median values already captured in `dim_pool` from IS files.

### Data Date Ranges

| Data Type | Earliest | Latest | Coverage |
|-----------|----------|--------|----------|
| Pools (issue_date) | 2019-06-01 | 2025-12-01 | ~6.5 years |
| Loans (first_pay_date) | 1993-04-01 | 2026-01-01 | ~32 years |
| Factor Data | 2019-06-01 | 2025-12-01 | 70 months |

**Why only 2019+?** The CSS SFTP server (current source) was established with the UMBS reform in 2019.

### Historical Data (1999-2024) via Clarity

For long-term prepay research across economic cycles, use the **Single-Family Loan-Level Dataset (SFLLD)**:

| Source | Coverage | Data |
|--------|----------|------|
| **Clarity Platform** | 1999-2024 | 54.8M loans |

**Access:** `freddiemac.com/research/datasets/sf-loanlevel-dataset` → "Access Historical Data"

Contains:
- Standard Dataset: Fixed-rate amortizing mortgages
- Non-Standard Dataset: ARMs, IOs, credit-enhanced loans
- Monthly performance data (prepay history)
- ~25 years across multiple rate cycles

### Historical Data Tables (SFLLD 1999-2025)

#### `dim_loan_historical` — Freddie Mac Historical Loans

Historical loan origination data from Clarity Platform SFLLD (54.8M loans, 1999-2025).

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL | Auto-increment PK |
| `loan_sequence` | VARCHAR(20) | Freddie Mac loan ID (**UNIQUE**) |
| `credit_score` | INTEGER | FICO at origination (300-850) |
| `first_time_buyer` | VARCHAR(1) | Y/N |
| `num_borrowers` | INTEGER | Number of borrowers |
| `dti` | DECIMAL(6,2) | Original DTI ratio |
| `orig_upb` | DECIMAL(14,2) | Original UPB |
| `orig_rate` | DECIMAL(6,3) | Original interest rate |
| `loan_term` | INTEGER | Loan term (months) |
| `amort_type` | VARCHAR(5) | FRM, ARM |
| `loan_purpose` | VARCHAR(1) | P=Purchase, C=CashOut, N=NoCash |
| `channel` | VARCHAR(1) | R=Retail, B=Broker, C=Correspondent |
| `ltv` | DECIMAL(6,2) | Original LTV |
| `cltv` | DECIMAL(6,2) | Original CLTV |
| `property_type` | VARCHAR(2) | SF, PU, CO, MH, CP |
| `state` | VARCHAR(2) | Property state |
| `zipcode` | VARCHAR(5) | 3-digit ZIP |
| `first_payment_date` | DATE | First payment date |
| `servicer_name` | VARCHAR(100) | Servicer name |
| `seller_name` | VARCHAR(100) | Seller/Originator |
| `source` | VARCHAR(20) | SFLLD or SFLLD_NONSTD |

**Current Stats**: 18,602,822 loans loaded (Standard dataset 1999-2008)

#### `fact_loan_month_historical` — Monthly Performance (Historical)

| Column | Type | Description |
|--------|------|-------------|
| `loan_sequence` | VARCHAR(20) | FK to `dim_loan_historical` |
| `report_date` | DATE | Monthly reporting period |
| `current_upb` | DECIMAL(14,2) | Current UPB |
| `current_rate` | DECIMAL(6,3) | Current rate |
| `dlq_status` | VARCHAR(3) | Delinquency status |
| `zero_balance_code` | VARCHAR(2) | Termination reason |
| `zero_balance_date` | DATE | Termination date |

**Zero Balance Codes**: 01=Prepaid, 02=Third Party Sale, 03=Short Sale, 09=REO

---

### File Ingestion Layer

#### `freddie_file_catalog` — SFTP File Inventory

Tracks files discovered and downloaded from CSS SFTP server.

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL | Auto-increment PK |
| `remote_path` | TEXT | Full path on SFTP server (**UNIQUE**) |
| `filename` | TEXT | File name |
| `file_type` | TEXT | intraday_issuance, monthly_issuance, deal_files, etc. |
| `file_date` | DATE | Date from filename |
| `remote_size` | BIGINT | File size in bytes |
| `download_status` | TEXT | pending, downloaded, processed, error |
| `local_gcs_path` | TEXT | GCS location after download |
| `downloaded_at` | TIMESTAMPTZ | When downloaded |
| `processed_at` | TIMESTAMPTZ | When parsed into DB |
| `error_message` | TEXT | Error details if failed |

**Current Stats**: 45,356 files cataloged (82 GB), 76% downloaded

#### `freddie_ingest_log` — SFTP Run Log

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL | Auto-increment PK |
| `run_started_at` | TIMESTAMPTZ | Job start time |
| `status` | TEXT | running, success, error |
| `files_discovered` | INTEGER | Files found on server |
| `files_downloaded` | INTEGER | Files successfully downloaded |
| `bytes_downloaded` | BIGINT | Total bytes transferred |

---

### Dimension Tables (Slowly Changing)

#### `dim_pool` — Pool Dimension

One row per pool. Contains static and slowly-changing pool attributes plus AI-generated tags.

| Column | Type | Description |
|--------|------|-------------|
| `pool_id` | TEXT | Freddie Mac pool ID (**PK**) |
| `cusip` | TEXT | Pool CUSIP (**UNIQUE**) |
| `prefix` | TEXT | Product prefix (FG, FR) |
| `product_type` | TEXT | 30YR, 15YR, ARM, etc. |
| `coupon` | NUMERIC(5,3) | Pool coupon rate |
| `issue_date` | DATE | Pool issue date |
| `maturity_date` | DATE | Pool maturity date |
| `orig_upb` | NUMERIC(15,2) | Original UPB at issuance |
| `orig_loan_count` | INTEGER | Original loan count |
| `wac` | NUMERIC(5,3) | Weighted avg coupon |
| `wam` | INTEGER | Weighted avg maturity (months) |
| `wala` | INTEGER | Weighted avg loan age (months) |
| `avg_fico` | INTEGER | Weighted avg FICO |
| `avg_ltv` | NUMERIC(5,2) | Weighted avg LTV |
| `servicer_name` | TEXT | Current servicer |
| `servicer_id` | TEXT | Servicer ID |
| **AI Tags** | | |
| `loan_balance_tier` | TEXT | LLB1-7, MLB, STD, JUMBO |
| `loan_program` | TEXT | VA, FHA, USDA, CONV |
| `fico_bucket` | TEXT | FICO_SUB620 to FICO_780PLUS |
| `ltv_bucket` | TEXT | LTV_60 to LTV_95PLUS |
| `seasoning_stage` | TEXT | NEW, EARLY, MATURING, SEASONED, etc. |
| `servicer_prepay_risk` | TEXT | PREPAY_PROTECTED, NEUTRAL, PREPAY_EXPOSED |
| `state_prepay_friction` | TEXT | HIGH_FRICTION, MODERATE_FRICTION, LOW_FRICTION |
| `refi_incentive_bps` | NUMERIC | Basis points in/out of money |
| `composite_prepay_score` | NUMERIC | 0-100 overall prepay risk score |
| `convexity_score` | NUMERIC | Contraction vs extension risk |
| `behavior_tags` | JSONB | AI tags (burnout_candidate, bear_market_stable, etc.) |
| `tags_updated_at` | TIMESTAMPTZ | When AI tags were last calculated |

#### `dim_loan` — Loan Dimension

One row per loan. Contains static loan-level attributes.

| Column | Type | Description |
|--------|------|-------------|
| `loan_id` | TEXT | Freddie Mac loan ID (**PK**) |
| `pool_id` | TEXT | FK → dim_pool |
| `orig_upb` | NUMERIC(12,2) | Original UPB |
| `orig_rate` | NUMERIC(5,3) | Original note rate |
| `orig_term` | INTEGER | Original term (months) |
| `orig_date` | DATE | Origination date |
| `fico` | INTEGER | FICO score |
| `ltv` | NUMERIC(5,2) | LTV ratio |
| `dti` | NUMERIC(5,2) | DTI ratio |
| `property_type` | TEXT | SF, Condo, PUD, etc. |
| `occupancy` | TEXT | Owner, Investor, Second |
| `state` | TEXT | Property state |
| `msa` | TEXT | MSA code |
| `purpose` | TEXT | Purchase, Refi, CashOut |
| `channel` | TEXT | Retail, Broker, Correspondent |

#### `dim_calendar` — Calendar Dimension

| Column | Type | Description |
|--------|------|-------------|
| `date_key` | DATE | Calendar date (**PK**) |
| `year` | INTEGER | Year |
| `month` | INTEGER | Month |
| `is_month_end` | BOOLEAN | Month-end flag |
| `is_business_day` | BOOLEAN | Business day flag |
| `bd_of_month` | INTEGER | Business day of month (BD1, BD4) |
| `is_factor_date` | BOOLEAN | Monthly factor release date |

---

### Fact Tables (Time Series)

#### `fact_pool_month` — Monthly Pool Performance

One row per pool per month. Contains factor, prepayment, and delinquency metrics.

| Column | Type | Description |
|--------|------|-------------|
| `pool_id` | TEXT | FK → dim_pool (**PK part**) |
| `as_of_date` | DATE | Factor date (**PK part**) |
| `factor` | NUMERIC(10,8) | Current factor |
| `curr_upb` | NUMERIC(15,2) | Current UPB |
| `loan_count` | INTEGER | Current loan count |
| `wac` | NUMERIC(5,3) | Current WAC |
| `wala` | INTEGER | Current WALA |
| `smm` | NUMERIC(8,6) | Single Monthly Mortality |
| `cpr` | NUMERIC(5,2) | Conditional Prepayment Rate |
| `dlq_30_count` | INTEGER | Loans 30 DPD |
| `dlq_60_count` | INTEGER | Loans 60 DPD |
| `dlq_90_count` | INTEGER | Loans 90+ DPD |
| `serious_dlq_rate` | NUMERIC(5,4) | 90+ DPD rate |

#### `fact_loan_month` — Monthly Loan Performance

One row per loan per month. Contains payment status and delinquency.

| Column | Type | Description |
|--------|------|-------------|
| `loan_id` | TEXT | FK → dim_loan (**PK part**) |
| `as_of_date` | DATE | As-of date (**PK part**) |
| `curr_upb` | NUMERIC(12,2) | Current UPB |
| `status` | TEXT | Current, 30DPD, 60DPD, etc. |
| `dlq_status` | INTEGER | Months delinquent (0, 1, 2, 3+) |
| `mod_flag` | BOOLEAN | Loan modified |
| `forbear_flag` | BOOLEAN | In forbearance |

#### `freddie_security_issuance` — Daily Issuance

From FRE_FISS (intraday) and FRE_IS (monthly) files.

| Column | Type | Description |
|--------|------|-------------|
| `issuance_date` | DATE | Issuance date |
| `pool_id` | TEXT | Pool ID |
| `cusip` | TEXT | CUSIP |
| `product_type` | TEXT | Product type |
| `coupon` | NUMERIC(5,3) | Coupon |
| `orig_face` | NUMERIC(15,2) | Original face |
| `file_sequence` | INTEGER | 1-4 for intraday files |

---

## Fannie Mae Tables

### Historical Data (2000-2025 via Data Dynamics)

#### `dim_loan_fannie_historical` — Fannie Mae Historical Loans

Single-Family Loan Performance (SFLP) data from Data Dynamics (~62M loans, 2000-2025).

| Column | Type | Description |
|--------|------|-------------|
| `loan_id` | TEXT | Fannie Mae loan ID (**PK**) |
| `channel` | TEXT | R=Retail, B=Broker, C=Correspondent, T=TPO |
| `seller_name` | TEXT | Seller/Originator |
| `servicer_name` | TEXT | Current servicer |
| `orig_rate` | DECIMAL(6,3) | Original interest rate |
| `orig_upb` | DECIMAL(14,2) | Original UPB |
| `orig_loan_term` | INT | Original term (months) |
| `orig_date` | DATE | Origination date |
| `first_payment_date` | DATE | First payment date |
| `ltv` | DECIMAL(6,2) | Original LTV |
| `cltv` | DECIMAL(6,2) | Original CLTV |
| `dti` | DECIMAL(5,2) | Original DTI |
| `fico` | INT | FICO at origination |
| `first_time_buyer` | TEXT | Y/N |
| `loan_purpose` | TEXT | P=Purchase, C=CashOut, N=NoCash |
| `property_type` | TEXT | SF/PU/CO/MH/CP |
| `state` | TEXT | Property state |
| `zipcode` | TEXT | 3-digit ZIP |
| `product_type` | TEXT | FRM/ARM |
| `super_conforming` | TEXT | Y/N (high-balance) |
| `pre_harp_loan_id` | TEXT | Original ID if HARP refi |

**Current Stats**: 751,201 loans loaded

#### `fact_loan_month_fannie_historical` — Monthly Performance

| Column | Type | Description |
|--------|------|-------------|
| `loan_id` | TEXT | FK to `dim_loan_fannie_historical` |
| `report_date` | DATE | Monthly reporting period |
| `current_upb` | DECIMAL(14,2) | Current UPB |
| `current_rate` | DECIMAL(6,3) | Current rate |
| `dlq_status` | TEXT | Delinquency status |
| `zero_balance_code` | TEXT | Termination reason |
| `zero_balance_date` | DATE | Termination date |

---

### HARP Data

#### `dim_loan_fannie_harp` — HARP Loan Data

| Column | Type | Description |
|--------|------|-------------|
| `loan_id` | VARCHAR(50) | Loan ID (**PK**) |
| `channel` | VARCHAR(50) | Origination channel |
| `seller_name` | VARCHAR(100) | Seller name |
| `orig_rate` | NUMERIC(9,6) | Original rate |
| `orig_upb` | NUMERIC(18,2) | Original UPB |
| `orig_loan_term` | INTEGER | Term (months) |
| `fico` | INTEGER | FICO score |
| `state` | VARCHAR(2) | Property state |

**Current Stats**: 130,000 loans

#### `harp_loan_mapping` — Pre/Post HARP Loan IDs

| Column | Type | Description |
|--------|------|-------------|
| `original_loan_id` | VARCHAR(50) | Pre-HARP loan ID (**PK**) |
| `new_loan_id` | VARCHAR(50) | Post-HARP loan ID |

**Current Stats**: 135,000 mappings

---

### Multifamily Data

#### `dim_loan_fannie_multifamily` — Multifamily Loans

| Column | Type | Description |
|--------|------|-------------|
| `loan_id` | VARCHAR(50) | Loan ID (**PK**) |
| `deal_name` | VARCHAR(255) | Securitization deal |
| `property_name` | VARCHAR(255) | Property name |
| `orig_date` | DATE | Origination date |
| `maturity_date` | DATE | Maturity date |
| `orig_upb` | NUMERIC(18,2) | Original UPB |
| `current_upb` | NUMERIC(18,2) | Current UPB |
| `property_type` | VARCHAR(50) | Apartment, Student, etc. |
| `property_state` | VARCHAR(2) | State |
| `units` | INTEGER | Number of units |
| `orig_ltv` | NUMERIC(5,2) | Original LTV |
| `orig_dscr` | NUMERIC(5,2) | Original DSCR |
| `current_dscr` | NUMERIC(5,2) | Current DSCR |
| `dlq_status` | VARCHAR(50) | Delinquency status |

**Current Stats**: 184 loans

---

### RPL/SCRT Mapping Tables (Freddie Mac)

#### `rpl_loan_id_mapping` — Seasoned Credit Risk Transfer Mappings

Maps original loan IDs to new IDs for RPL/SCRT/SLST transactions.

| Column | Type | Description |
|--------|------|-------------|
| `original_loan_id` | VARCHAR(50) | Original loan ID (**PK**) |
| `new_loan_id` | VARCHAR(50) | New transaction loan ID |

**Current Stats**: 90,000 standard + 169,369 non-standard mappings

---

## Ginnie Mae Tables

### File Ingestion Layer

#### `ginnie_file_catalog` — Bulk Download File Inventory

Tracks files discovered and downloaded from `bulk.ginniemae.gov`.

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL | Auto-increment PK |
| `filename` | TEXT | File name (**UNIQUE**) |
| `file_type` | TEXT | daily_pool, portfolio_loan_g1, factor_a1, etc. |
| `file_category` | TEXT | MBS_SF, HMBS, MULTIFAMILY, PLATINUM, FACTOR |
| `file_date` | DATE | Date from filename |
| `file_size_bytes` | BIGINT | File size in bytes |
| `last_posted_at` | TIMESTAMPTZ | When file was posted on Ginnie site |
| `local_gcs_path` | TEXT | GCS location after download |
| `download_status` | TEXT | pending, downloaded, processed, error |
| `downloaded_at` | TIMESTAMPTZ | When downloaded |
| `processed_at` | TIMESTAMPTZ | When parsed into DB |
| `error_message` | TEXT | Error details if failed |

**Current Stats**: 58 files cataloged (December 2025 data)

#### `ginnie_ingest_log` — Download Run Log

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL | Auto-increment PK |
| `run_started_at` | TIMESTAMPTZ | Job start time |
| `run_mode` | TEXT | daily, monthly, factor, backfill |
| `status` | TEXT | running, success, error, auth_required |
| `files_discovered` | INTEGER | Files found on page |
| `files_downloaded` | INTEGER | Files successfully downloaded |
| `bytes_downloaded` | BIGINT | Total bytes transferred |

### Dimension Tables

#### `dim_pool_ginnie` — Pool Dimension (GNMA-specific)

One row per Ginnie Mae pool. Contains static attributes and AI-generated tags.

| Column | Type | Description |
|--------|------|-------------|
| `pool_id` | TEXT | Ginnie Mae pool ID (**PK**) |
| `cusip` | TEXT | Pool CUSIP (**UNIQUE**) |
| `security_type` | TEXT | GNM1, GNM2, HMBS, PLATINUM |
| `product_type` | TEXT | 30YR, 15YR, 20YR, ARM, etc. |
| `coupon` | NUMERIC(5,3) | Pool coupon rate |
| `issue_date` | DATE | Pool issue date |
| `maturity_date` | DATE | Pool maturity date |
| `orig_upb` | NUMERIC(15,2) | Original UPB at issuance |
| `orig_loan_count` | INTEGER | Original loan count |
| `wac` | NUMERIC(5,3) | Weighted avg coupon |
| `wam` | INTEGER | Weighted avg maturity (months) |
| `wala` | INTEGER | Weighted avg loan age (months) |
| `avg_fico` | INTEGER | Weighted avg FICO |
| `avg_ltv` | NUMERIC(5,2) | Weighted avg LTV |
| `program_type` | TEXT | FHA, VA, USDA, RD, PIH |
| `issuer_id` | TEXT | Issuer ID |
| `issuer_name` | TEXT | Issuer name |
| **AI Tags** | | (Same structure as `dim_pool`) |
| `loan_balance_tier` | TEXT | LLB1-7, MLB, STD, JUMBO |
| `fico_bucket` | TEXT | FICO_SUB620 to FICO_780PLUS |
| `ltv_bucket` | TEXT | LTV_60 to LTV_95PLUS |
| `servicer_prepay_risk` | TEXT | PREPAY_PROTECTED, NEUTRAL, PREPAY_EXPOSED |
| `composite_prepay_score` | NUMERIC | 0-100 overall prepay risk score |
| `behavior_tags` | JSONB | AI tags |

#### `dim_loan_ginnie` — Loan Dimension (GNMA-specific)

One row per Ginnie Mae loan.

| Column | Type | Description |
|--------|------|-------------|
| `loan_id` | TEXT | Ginnie Mae loan ID (**PK**) |
| `pool_id` | TEXT | FK → `dim_pool_ginnie` |
| `orig_upb` | NUMERIC(12,2) | Original UPB |
| `orig_rate` | NUMERIC(5,3) | Original note rate |
| `fico` | INTEGER | FICO score |
| `ltv` | NUMERIC(5,2) | LTV ratio |
| `dti` | NUMERIC(5,2) | DTI ratio |
| `property_type` | TEXT | SF, CONDO, PUD, MH, 2-4UNIT |
| `state` | TEXT | Property state |
| `program_type` | TEXT | FHA, VA, USDA, RD, PIH |
| `fha_insurance_pct` | NUMERIC(5,2) | FHA insurance percentage |
| `va_guaranty_pct` | NUMERIC(5,2) | VA guaranty percentage |

### Fact Tables

#### `fact_pool_month_ginnie` — Monthly Pool Performance

One row per pool per month. Contains factor, prepayment, and delinquency metrics.

| Column | Type | Description |
|--------|------|-------------|
| `pool_id` | TEXT | FK → `dim_pool_ginnie` (**PK part**) |
| `as_of_date` | DATE | Factor date (**PK part**) |
| `factor` | NUMERIC(10,8) | Current factor |
| `curr_upb` | NUMERIC(15,2) | Current UPB |
| `loan_count` | INTEGER | Current loan count |
| `smm` | NUMERIC(8,6) | Single Monthly Mortality |
| `cpr` | NUMERIC(5,2) | Conditional Prepayment Rate |
| `dlq_30_pct` | NUMERIC(5,4) | 30 DPD rate |
| `dlq_60_pct` | NUMERIC(5,4) | 60 DPD rate |
| `dlq_90_plus_pct` | NUMERIC(5,4) | 90+ DPD rate |
| `serious_dlq_rate` | NUMERIC(5,4) | 90+ DPD rate |

#### `ginnie_historical_pool_stats` — Pre-2012 Aggregate Stats

Historical pool-level statistics for pre-loan-level-disclosure era.

| Column | Type | Description |
|--------|------|-------------|
| `as_of_date` | DATE | (**PK part**) |
| `security_type` | TEXT | GNM1, GNM2 (**PK part**) |
| `product_type` | TEXT | 30YR, 15YR, ARM (**PK part**) |
| `coupon_bucket` | TEXT | 3.0, 3.5, 4.0, etc. (**PK part**) |
| `total_upb` | NUMERIC(18,2) | Total UPB |
| `pool_count` | INTEGER | Number of pools |
| `avg_cpr` | NUMERIC(5,2) | Average CPR |

### Views

| View | Description |
|------|-------------|
| `ginnie_pool_latest_factor` | Most recent factor for each Ginnie pool |
| `ginnie_pool_summary` | Pool dimension with latest metrics joined |
| `v_all_agency_pools` | Combined Freddie + Ginnie pools for cross-agency analysis |

### Data Availability

**Ginnie Mae Data Sources:**

| Source | URL | Data Available |
|--------|-----|----------------|
| **Bulk Download** | `bulk.ginniemae.gov` | Current month only |
| **Disclosure History** | `ginniemae.gov/.../DisclosureHistory.aspx` | **2012-present** |

**Historical File Categories (Disclosure History):**

| Category | Prefix | First Date | Est. Files | Priority |
|----------|--------|------------|------------|----------|
| Loan Level Ginnie I | `llmon1` | 2013-10 | ~146 | ⭐ High |
| Loan Level Ginnie II | `llmon2` | 2013-10 | ~146 | ⭐ High |
| Loan Level New Issues | `dailyllmni` | 2013-09 | ~148 | ⭐ High |
| Liquidations | `llmonliq` | 2018-09 | ~88 | ⭐ High |
| Factor A G I | `factorA1` | 2012-08 | ~160 | ⭐ High |
| Factor B G I | `factorB1` | 2012-08 | ~160 | ⭐ High |
| Pool/Security | `monthlySFPS` | 2020-01 | ~72 | Medium |
| REMIC Factors | `remic1`, `remic2` | 2012-02 | ~166 each | Low |

**Storage Location:** `gs://oasive-raw-data/ginnie/historical/<prefix>/<year>/<month>/<filename>`

### Layout Version History (Loan-Level Files)

Ginnie Mae file layouts change over time. The parser must be version-aware.

Reference: `docs/Ginnie_Historical_Layouts_Guide_Feb2024.pdf`

**Loan Record ("L" record) Versions:**

| Version | Date Range | L Record Length | Key Changes |
|---------|------------|-----------------|-------------|
| **V1.0** | Oct 2013 - Mar 2015 | **142 bytes** | Initial version |
| **V1.5** | Dec 2013 - Mar 2015 | 142 bytes | First production release |
| **V1.6** | Apr 2015 - Nov 2017 | **154 bytes** | +12 bytes: Loan Origination Date, Seller Issuer ID |
| **V1.7** | Dec 2017 - Present | **192 bytes** | +38 bytes: 10 ARM fields (Index Type, Rate Caps, etc.) |
| **V1.8** | Feb 2021 - Present | 192 bytes | Loan Purpose "5" for Re-Performing (no layout change) |

**File Record Types:**

| Type | Length | Description |
|------|--------|-------------|
| H | 41 bytes | Header - file metadata |
| P | 37 bytes | Pool - pool ID, CUSIP, issue date |
| L | 142-192 bytes | Loan - individual loan data (version-dependent) |
| T | 44 bytes | Trailer - pool totals/counts |

**Parser Logic:**
```python
def get_loan_version(year, month):
    if (year, month) < (2015, 4):
        return "V1.0"  # 142 bytes
    elif (year, month) < (2017, 12):
        return "V1.6"  # 154 bytes
    else:
        return "V1.7"  # 192 bytes
```

**Download Commands:**
```bash
# Download specific category
python3 -m src.ingestors.ginnie_ingestor --mode=historical-mbs-sf --historical-category=llmon1

# Download all MBS SF categories
python3 -m src.ingestors.ginnie_ingestor --mode=historical-mbs-sf

# Download all Factor categories
python3 -m src.ingestors.ginnie_ingestor --mode=historical-factor
```

---

## Entity Relationship Diagram

```
                           FRED DATA
┌─────────────────┐       ┌─────────────────────┐
│   fred_series   │       │  fred_observation   │
├─────────────────┤       ├─────────────────────┤
│ series_id (PK)  │◄──────│ series_id (FK)      │
│ indicator_id    │       │ obs_date            │
│ domain          │       │ value               │
│ frequency       │       │ vintage_date        │
└─────────────────┘       └─────────────────────┘

                        FREDDIE MAC DATA
┌─────────────────────┐        ┌──────────────────────┐
│ freddie_file_catalog│        │  freddie_ingest_log  │
├─────────────────────┤        ├──────────────────────┤
│ remote_path (PK)    │        │ files_discovered     │
│ download_status     │        │ files_downloaded     │
│ local_gcs_path      │        │ status               │
└─────────────────────┘        └──────────────────────┘

┌─────────────────┐       ┌─────────────────────┐
│    dim_pool     │       │   fact_pool_month   │
├─────────────────┤       ├─────────────────────┤
│ pool_id (PK)    │◄──────│ pool_id (FK)        │
│ cusip           │       │ as_of_date          │
│ product_type    │       │ factor              │
│ coupon          │       │ curr_upb            │
│ **AI TAGS**     │       │ cpr                 │
│ risk_profile    │       │ serious_dlq_rate    │
│ behavior_tags   │       └─────────────────────┘
└────────┬────────┘
         │
         │ 1:N
         ▼
┌─────────────────┐       ┌─────────────────────┐
│    dim_loan     │       │   fact_loan_month   │
├─────────────────┤       ├─────────────────────┤
│ loan_id (PK)    │◄──────│ loan_id (FK)        │
│ pool_id (FK)    │       │ as_of_date          │
│ fico            │       │ status              │
│ ltv             │       │ dlq_status          │
│ state           │       └─────────────────────┘
└─────────────────┘
```

---

## Views

### `pool_latest_factor`
Most recent factor for each pool.

### `pool_summary`
Pool dimension with latest metrics and AI tags joined.

### `fred_latest`
Most recent value for each FRED series.

### `fred_series_catalog`
Complete series metadata with coverage statistics.

---

## Migrations

| File | Description |
|------|-------------|
| `001_fred_schema.sql` | FRED series, observations, ingest log, views |
| `002_seed_fred_series.sql` | Initial 38 series definitions |
| `003_freddie_schema.sql` | Freddie file catalog and ingest log |
| `004_freddie_data_schema.sql` | dim_pool, dim_loan, fact tables, calendar |
| `009_sflld_historical_schema.sql` | Freddie SFLLD historical (1999-2025) |
| `010_ginnie_schema.sql` | Ginnie Mae pool/loan/fact tables |
| `011_fannie_historical_schema.sql` | Fannie Mae SFLP historical (2000-2025) |
| `013_fannie_multifamily_schema.sql` | Fannie Mae Multifamily loans |
| `014_fannie_harp_schema.sql` | Fannie Mae HARP data + mapping |
| `015_freddie_rpl_scrt_schema.sql` | Freddie RPL/SCRT/SLST mappings |

Run migrations:
```bash
cd /Users/anaishowland/oasive_db
source venv/bin/activate
PYTHONPATH=/Users/anaishowland/oasive_db python scripts/run_migrations.py
```

---

## Sample Queries

```sql
-- Get all 30YR 4.0 pools with factor > 0.5
SELECT pool_id, cusip, factor, curr_upb, cpr
FROM pool_summary
WHERE product_type = '30YR' AND coupon = 4.0 AND factor > 0.5;

-- Pools with high burnout potential (AI tag)
SELECT pool_id, cusip, burnout_score, behavior_tags
FROM dim_pool
WHERE burnout_score > 0.7
ORDER BY burnout_score DESC;

-- Monthly CPR trend for a pool
SELECT as_of_date, factor, cpr, serious_dlq_rate
FROM fact_pool_month
WHERE pool_id = 'XXXXX'
ORDER BY as_of_date;

-- State concentration analysis
SELECT state, COUNT(*) as loan_count, SUM(orig_upb) as total_upb
FROM dim_loan
WHERE pool_id = 'XXXXX'
GROUP BY state
ORDER BY total_upb DESC;

-- Combine FRED rates with pool data
SELECT p.pool_id, p.coupon, f.value as current_10y_rate,
       (p.coupon - f.value) as spread_to_10y
FROM dim_pool p
CROSS JOIN fred_latest f
WHERE f.series_id = 'DGS10';
```
