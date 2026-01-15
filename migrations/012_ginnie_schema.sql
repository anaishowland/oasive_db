-- Migration 012: Ginnie Mae Disclosure Data Schema
-- Stores metadata about downloaded files and pool/loan data from bulk.ginniemae.gov

-- ============================================
-- FILE CATALOG & INGESTION TRACKING
-- ============================================

-- Table: ginnie_file_catalog
-- Tracks all files discovered and downloaded from Ginnie Mae bulk download
CREATE TABLE IF NOT EXISTS ginnie_file_catalog (
    id SERIAL PRIMARY KEY,
    filename TEXT NOT NULL,                    -- e.g., "dailySFPS.zip", "llmon1_202512.zip"
    file_type TEXT NOT NULL,                   -- daily_pool, daily_loan, monthly_pool, monthly_loan, factor, historical
    file_category TEXT,                        -- MBS_SF, HMBS, MULTIFAMILY, PLATINUM, FACTOR, OTHER
    file_date DATE,                            -- Date from filename if available
    file_size_bytes BIGINT,                    -- File size in bytes
    last_posted_at TIMESTAMPTZ,                -- When file was last posted on Ginnie site
    local_gcs_path TEXT,                       -- GCS path after download
    download_status TEXT DEFAULT 'pending',    -- 'pending', 'downloaded', 'processed', 'error'
    downloaded_at TIMESTAMPTZ,
    processed_at TIMESTAMPTZ,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (filename)
);

CREATE INDEX IF NOT EXISTS idx_ginnie_file_status ON ginnie_file_catalog(download_status);
CREATE INDEX IF NOT EXISTS idx_ginnie_file_type ON ginnie_file_catalog(file_type);
CREATE INDEX IF NOT EXISTS idx_ginnie_file_date ON ginnie_file_catalog(file_date);
CREATE INDEX IF NOT EXISTS idx_ginnie_file_category ON ginnie_file_catalog(file_category);

-- Table: ginnie_ingest_log
-- Audit trail for download runs
CREATE TABLE IF NOT EXISTS ginnie_ingest_log (
    id SERIAL PRIMARY KEY,
    run_started_at TIMESTAMPTZ NOT NULL,
    run_completed_at TIMESTAMPTZ,
    run_mode TEXT,                              -- 'daily', 'monthly', 'factor', 'backfill'
    status TEXT NOT NULL DEFAULT 'running',    -- 'running', 'success', 'error', 'auth_required'
    files_discovered INTEGER DEFAULT 0,
    files_downloaded INTEGER DEFAULT 0,
    bytes_downloaded BIGINT DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================
-- POOL DIMENSION (Ginnie Mae specific)
-- ============================================

CREATE TABLE IF NOT EXISTS dim_pool_ginnie (
    pool_id TEXT PRIMARY KEY,
    cusip TEXT UNIQUE,
    security_type TEXT,                        -- GNM1, GNM2, HMBS, PLATINUM
    product_type TEXT,                         -- 30YR, 15YR, 20YR, ARM, etc.
    coupon NUMERIC(5,3),
    issue_date DATE,
    maturity_date DATE,
    orig_upb NUMERIC(15,2),
    orig_loan_count INTEGER,
    
    -- Weighted averages at issuance
    wac NUMERIC(5,3),                          -- Weighted avg coupon
    wam INTEGER,                               -- Weighted avg maturity (months)
    wala INTEGER,                              -- Weighted avg loan age (months)
    avg_fico INTEGER,                          -- Weighted avg FICO
    avg_ltv NUMERIC(5,2),                      -- Weighted avg LTV
    avg_dti NUMERIC(5,2),                      -- Weighted avg DTI
    avg_loan_size NUMERIC(12,2),               -- Weighted avg loan size
    
    -- Issuer info
    issuer_id TEXT,
    issuer_name TEXT,
    
    -- Program type (key for Ginnie - FHA/VA/USDA)
    program_type TEXT,                         -- FHA, VA, USDA, RD, PIH
    
    -- Geographic
    top_state TEXT,                            -- State with highest concentration
    state_concentration NUMERIC(5,2),          -- % in top state
    
    -- AI Tags (same structure as dim_pool for consistency)
    loan_balance_tier TEXT,                    -- LLB1-7, MLB, STD, JUMBO
    fico_bucket TEXT,                          -- FICO_SUB620, FICO_620_659, etc.
    ltv_bucket TEXT,                           -- LTV_60, LTV_60_70, etc.
    seasoning_stage TEXT,                      -- NEW, EARLY, MATURING, SEASONED
    servicer_prepay_risk TEXT,                 -- PREPAY_PROTECTED, NEUTRAL, PREPAY_EXPOSED
    composite_prepay_score NUMERIC,            -- 0-100 overall prepay risk
    behavior_tags JSONB,                       -- AI tags (burnout_candidate, etc.)
    tags_updated_at TIMESTAMPTZ,
    
    -- Metadata
    source_file TEXT,                          -- Which file this came from
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ginnie_pool_cusip ON dim_pool_ginnie(cusip);
CREATE INDEX IF NOT EXISTS idx_ginnie_pool_type ON dim_pool_ginnie(security_type, product_type);
CREATE INDEX IF NOT EXISTS idx_ginnie_pool_program ON dim_pool_ginnie(program_type);
CREATE INDEX IF NOT EXISTS idx_ginnie_pool_issue_date ON dim_pool_ginnie(issue_date);
CREATE INDEX IF NOT EXISTS idx_ginnie_pool_coupon ON dim_pool_ginnie(coupon);

-- ============================================
-- LOAN DIMENSION (Ginnie Mae specific)
-- ============================================

CREATE TABLE IF NOT EXISTS dim_loan_ginnie (
    loan_id TEXT PRIMARY KEY,
    pool_id TEXT REFERENCES dim_pool_ginnie(pool_id),
    
    -- Origination attributes
    orig_upb NUMERIC(12,2),
    orig_rate NUMERIC(5,3),
    orig_term INTEGER,
    orig_date DATE,
    first_pay_date DATE,
    
    -- Credit attributes
    fico INTEGER,
    ltv NUMERIC(5,2),
    cltv NUMERIC(5,2),                         -- Combined LTV
    dti NUMERIC(5,2),
    
    -- Property
    property_type TEXT,                        -- SF, CONDO, PUD, MH, 2-4UNIT
    occupancy TEXT,                            -- OWNER, INVESTOR, SECOND
    state TEXT,
    zip3 TEXT,                                 -- First 3 digits of ZIP
    msa TEXT,
    
    -- Loan attributes
    purpose TEXT,                              -- PURCHASE, REFI, CASHOUT
    channel TEXT,                              -- RETAIL, BROKER, CORRESPONDENT
    num_units INTEGER,
    num_borrowers INTEGER,
    first_time_buyer BOOLEAN,
    
    -- Ginnie-specific: Program info
    program_type TEXT,                         -- FHA, VA, USDA, RD, PIH
    fha_insurance_pct NUMERIC(5,2),           -- FHA insurance percentage
    va_guaranty_pct NUMERIC(5,2),             -- VA guaranty percentage
    
    -- Metadata
    source_file TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ginnie_loan_pool ON dim_loan_ginnie(pool_id);
CREATE INDEX IF NOT EXISTS idx_ginnie_loan_state ON dim_loan_ginnie(state);
CREATE INDEX IF NOT EXISTS idx_ginnie_loan_program ON dim_loan_ginnie(program_type);
CREATE INDEX IF NOT EXISTS idx_ginnie_loan_orig_date ON dim_loan_ginnie(orig_date);

-- ============================================
-- MONTHLY POOL FACTS (Factor/Performance)
-- ============================================

CREATE TABLE IF NOT EXISTS fact_pool_month_ginnie (
    pool_id TEXT REFERENCES dim_pool_ginnie(pool_id),
    as_of_date DATE,
    
    -- Factor data
    factor NUMERIC(10,8),
    curr_upb NUMERIC(15,2),
    loan_count INTEGER,
    
    -- Current weighted averages
    wac NUMERIC(5,3),
    wala INTEGER,
    
    -- Prepayment metrics
    smm NUMERIC(8,6),                          -- Single Monthly Mortality
    cpr NUMERIC(5,2),                          -- Conditional Prepayment Rate
    
    -- Delinquency
    dlq_30_count INTEGER,
    dlq_60_count INTEGER,
    dlq_90_count INTEGER,
    dlq_30_pct NUMERIC(5,4),
    dlq_60_pct NUMERIC(5,4),
    dlq_90_plus_pct NUMERIC(5,4),
    serious_dlq_rate NUMERIC(5,4),             -- 90+ DPD rate
    
    -- Metadata
    source_file TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    
    PRIMARY KEY (pool_id, as_of_date)
);

CREATE INDEX IF NOT EXISTS idx_ginnie_pool_month_date ON fact_pool_month_ginnie(as_of_date);

-- ============================================
-- HISTORICAL AGGREGATE STATS (Pre-2012)
-- Pool-level aggregates for historical research
-- ============================================

CREATE TABLE IF NOT EXISTS ginnie_historical_pool_stats (
    as_of_date DATE,
    security_type TEXT,                        -- GNM1, GNM2
    product_type TEXT,                         -- 30YR, 15YR, ARM
    coupon_bucket TEXT,                        -- 3.0, 3.5, 4.0, etc.
    program_type TEXT,                         -- FHA, VA, USDA (if available)
    
    -- Aggregate metrics
    total_upb NUMERIC(18,2),
    pool_count INTEGER,
    loan_count INTEGER,
    
    -- Weighted averages
    avg_wac NUMERIC(5,3),
    avg_wam INTEGER,
    avg_wala INTEGER,
    avg_loan_size NUMERIC(12,2),
    
    -- Prepayment
    avg_cpr NUMERIC(5,2),
    avg_smm NUMERIC(8,6),
    
    -- Delinquency
    dlq_30_pct NUMERIC(5,4),
    dlq_60_pct NUMERIC(5,4),
    dlq_90_plus_pct NUMERIC(5,4),
    
    -- Metadata
    source_file TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    
    PRIMARY KEY (as_of_date, security_type, product_type, coupon_bucket)
);

CREATE INDEX IF NOT EXISTS idx_ginnie_hist_date ON ginnie_historical_pool_stats(as_of_date);
CREATE INDEX IF NOT EXISTS idx_ginnie_hist_type ON ginnie_historical_pool_stats(security_type, product_type);

-- ============================================
-- TRIGGERS
-- ============================================

-- Auto-update updated_at on ginnie_file_catalog
DROP TRIGGER IF EXISTS update_ginnie_file_catalog_updated_at ON ginnie_file_catalog;
CREATE TRIGGER update_ginnie_file_catalog_updated_at
    BEFORE UPDATE ON ginnie_file_catalog
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Auto-update updated_at on dim_pool_ginnie
DROP TRIGGER IF EXISTS update_dim_pool_ginnie_updated_at ON dim_pool_ginnie;
CREATE TRIGGER update_dim_pool_ginnie_updated_at
    BEFORE UPDATE ON dim_pool_ginnie
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================
-- VIEWS
-- ============================================

-- View: Latest Ginnie pool factors
CREATE OR REPLACE VIEW ginnie_pool_latest_factor AS
SELECT DISTINCT ON (pool_id)
    pool_id,
    as_of_date,
    factor,
    curr_upb,
    loan_count,
    cpr,
    serious_dlq_rate
FROM fact_pool_month_ginnie
ORDER BY pool_id, as_of_date DESC;

-- View: Combined pool summary with latest metrics
CREATE OR REPLACE VIEW ginnie_pool_summary AS
SELECT 
    p.*,
    f.as_of_date AS latest_factor_date,
    f.factor AS current_factor,
    f.curr_upb,
    f.cpr AS current_cpr,
    f.serious_dlq_rate AS current_dlq_rate
FROM dim_pool_ginnie p
LEFT JOIN ginnie_pool_latest_factor f ON p.pool_id = f.pool_id;

-- View: Cross-agency pool comparison
CREATE OR REPLACE VIEW v_all_agency_pools AS
SELECT 
    pool_id,
    cusip,
    'FREDDIE' AS agency,
    product_type,
    coupon,
    issue_date,
    orig_upb,
    avg_fico,
    avg_ltv,
    'CONV' AS program_type,
    composite_prepay_score
FROM dim_pool
UNION ALL
SELECT 
    pool_id,
    cusip,
    'GINNIE' AS agency,
    product_type,
    coupon,
    issue_date,
    orig_upb,
    avg_fico,
    avg_ltv,
    program_type,
    composite_prepay_score
FROM dim_pool_ginnie;

-- ============================================
-- COMMENTS
-- ============================================

COMMENT ON TABLE ginnie_file_catalog IS 'Tracks files downloaded from bulk.ginniemae.gov';
COMMENT ON TABLE dim_pool_ginnie IS 'Ginnie Mae pool dimension with static attributes and AI tags';
COMMENT ON TABLE dim_loan_ginnie IS 'Ginnie Mae loan dimension with origination attributes';
COMMENT ON TABLE fact_pool_month_ginnie IS 'Monthly pool performance data (factor, CPR, delinquency)';
COMMENT ON TABLE ginnie_historical_pool_stats IS 'Pre-2012 aggregate pool statistics for historical research';
