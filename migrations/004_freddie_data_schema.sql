-- Migration 004: Freddie Mac MBS Disclosure Data Schema
-- Core data tables for pool and loan-level disclosure data
-- Part of Postgres structured DB layer (see business plan)

-- ============================================================================
-- DIMENSION TABLES (slowly changing, updated monthly)
-- ============================================================================

-- Table: dim_pool
-- Pool-level static and slowly-changing attributes
-- One row per pool, updated when new disclosures arrive
CREATE TABLE IF NOT EXISTS dim_pool (
    pool_id TEXT PRIMARY KEY,                    -- Freddie Mac pool ID
    cusip TEXT UNIQUE,                           -- Pool CUSIP
    prefix TEXT,                                 -- Product prefix (e.g., "FG", "FR")
    product_type TEXT,                           -- Product type (30YR, 15YR, ARM, etc.)
    coupon NUMERIC(5,3),                         -- Pool coupon rate
    issue_date DATE,                             -- Pool issue date
    maturity_date DATE,                          -- Pool maturity date
    orig_upb NUMERIC(15,2),                      -- Original UPB at issuance
    orig_loan_count INTEGER,                     -- Original loan count
    
    -- Weighted average characteristics at issuance
    wac NUMERIC(5,3),                            -- Weighted avg coupon
    wam INTEGER,                                 -- Weighted avg maturity (months)
    wala INTEGER,                                -- Weighted avg loan age (months)
    avg_loan_size NUMERIC(12,2),                 -- Average loan size
    
    -- Credit characteristics (weighted averages)
    avg_fico INTEGER,                            -- Weighted avg FICO
    avg_ltv NUMERIC(5,2),                        -- Weighted avg LTV
    avg_dti NUMERIC(5,2),                        -- Weighted avg DTI
    
    -- Geographic concentration
    top_state TEXT,                              -- State with largest concentration
    top_state_pct NUMERIC(5,2),                  -- Percentage in top state
    
    -- Servicer info
    servicer_name TEXT,                          -- Current servicer
    servicer_id TEXT,                            -- Servicer ID
    
    -- Product flags
    is_jumbo BOOLEAN DEFAULT FALSE,
    is_low_balance BOOLEAN DEFAULT FALSE,
    is_high_balance BOOLEAN DEFAULT FALSE,
    is_arm BOOLEAN DEFAULT FALSE,
    
    -- AI-generated tags (Phase 1 feature)
    risk_profile TEXT,                           -- e.g., "conservative", "aggressive"
    burnout_score NUMERIC(5,2),                  -- Burnout likelihood score
    geo_concentration_tag TEXT,                  -- e.g., "CA_heavy", "diversified"
    servicer_quality_tag TEXT,                   -- e.g., "strong", "weak"
    behavior_tags JSONB,                         -- Additional AI-generated tags
    
    -- Metadata
    source_file TEXT,                            -- Source disclosure file
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dim_pool_cusip ON dim_pool(cusip);
CREATE INDEX IF NOT EXISTS idx_dim_pool_product ON dim_pool(product_type);
CREATE INDEX IF NOT EXISTS idx_dim_pool_coupon ON dim_pool(coupon);
CREATE INDEX IF NOT EXISTS idx_dim_pool_issue_date ON dim_pool(issue_date);
CREATE INDEX IF NOT EXISTS idx_dim_pool_servicer ON dim_pool(servicer_id);

-- Table: dim_loan
-- Loan-level static attributes (from loan-level disclosure)
-- Note: For scale, consider BigQuery for full loan tape
CREATE TABLE IF NOT EXISTS dim_loan (
    loan_id TEXT PRIMARY KEY,                    -- Freddie Mac loan ID
    pool_id TEXT REFERENCES dim_pool(pool_id),   -- FK to pool
    
    -- Original loan terms
    orig_upb NUMERIC(12,2),                      -- Original UPB
    orig_rate NUMERIC(5,3),                      -- Original note rate
    orig_term INTEGER,                           -- Original term (months)
    orig_date DATE,                              -- Origination date
    first_pay_date DATE,                         -- First payment date
    maturity_date DATE,                          -- Loan maturity date
    
    -- Borrower characteristics (at origination)
    fico INTEGER,                                -- FICO score
    ltv NUMERIC(5,2),                            -- LTV ratio
    cltv NUMERIC(5,2),                           -- Combined LTV
    dti NUMERIC(5,2),                            -- DTI ratio
    
    -- Property info
    property_type TEXT,                          -- SF, Condo, PUD, etc.
    occupancy TEXT,                              -- Owner, Investor, Second
    num_units INTEGER,                           -- Number of units
    state TEXT,                                  -- Property state
    zip_3 TEXT,                                  -- 3-digit ZIP
    msa TEXT,                                    -- MSA code
    
    -- Loan characteristics
    purpose TEXT,                                -- Purchase, Refi, CashOut
    channel TEXT,                                -- Retail, Broker, Correspondent
    first_time_buyer BOOLEAN,
    num_borrowers INTEGER,
    
    -- Product features
    product_type TEXT,                           -- FRM, ARM, etc.
    amort_type TEXT,                             -- Fully amortizing, IO, etc.
    prepay_penalty BOOLEAN DEFAULT FALSE,
    
    -- Metadata
    source_file TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dim_loan_pool ON dim_loan(pool_id);
CREATE INDEX IF NOT EXISTS idx_dim_loan_state ON dim_loan(state);
CREATE INDEX IF NOT EXISTS idx_dim_loan_fico ON dim_loan(fico);
CREATE INDEX IF NOT EXISTS idx_dim_loan_ltv ON dim_loan(ltv);
CREATE INDEX IF NOT EXISTS idx_dim_loan_purpose ON dim_loan(purpose);

-- ============================================================================
-- FACT TABLES (time series, append-only)
-- ============================================================================

-- Table: fact_pool_month
-- Monthly pool-level performance metrics
-- One row per pool per month
CREATE TABLE IF NOT EXISTS fact_pool_month (
    pool_id TEXT NOT NULL REFERENCES dim_pool(pool_id),
    as_of_date DATE NOT NULL,                    -- Factor date (month-end)
    
    -- Current state
    factor NUMERIC(10,8),                        -- Current factor
    curr_upb NUMERIC(15,2),                      -- Current UPB
    loan_count INTEGER,                          -- Current loan count
    
    -- Weighted averages (current)
    wac NUMERIC(5,3),                            -- Current WAC
    wam INTEGER,                                 -- Current WAM
    wala INTEGER,                                -- Current WALA
    avg_loan_size NUMERIC(12,2),                 -- Current avg loan size
    
    -- Period activity
    scheduled_prin NUMERIC(12,2),                -- Scheduled principal
    unscheduled_prin NUMERIC(12,2),              -- Prepayments
    total_prin NUMERIC(12,2),                    -- Total principal paid
    
    -- Prepayment metrics
    smm NUMERIC(8,6),                            -- Single Monthly Mortality
    cpr NUMERIC(5,2),                            -- Conditional Prepayment Rate
    
    -- Delinquency buckets (loan count)
    dlq_30_count INTEGER DEFAULT 0,
    dlq_60_count INTEGER DEFAULT 0,
    dlq_90_count INTEGER DEFAULT 0,
    dlq_120plus_count INTEGER DEFAULT 0,
    foreclosure_count INTEGER DEFAULT 0,
    reo_count INTEGER DEFAULT 0,
    
    -- Delinquency buckets (UPB)
    dlq_30_upb NUMERIC(15,2) DEFAULT 0,
    dlq_60_upb NUMERIC(15,2) DEFAULT 0,
    dlq_90_upb NUMERIC(15,2) DEFAULT 0,
    
    -- Rates
    serious_dlq_rate NUMERIC(5,4),               -- 90+ DPD rate
    
    -- Removals
    voluntary_removals INTEGER DEFAULT 0,        -- Payoffs, refis
    involuntary_removals INTEGER DEFAULT 0,      -- Defaults, foreclosures
    
    -- Metadata
    source_file TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    
    PRIMARY KEY (pool_id, as_of_date)
);

CREATE INDEX IF NOT EXISTS idx_fact_pool_month_date ON fact_pool_month(as_of_date);
CREATE INDEX IF NOT EXISTS idx_fact_pool_month_factor ON fact_pool_month(factor);

-- Table: fact_loan_month
-- Monthly loan-level performance (for active loans)
-- Consider BigQuery for full history at scale
CREATE TABLE IF NOT EXISTS fact_loan_month (
    loan_id TEXT NOT NULL,
    as_of_date DATE NOT NULL,
    
    -- Current state
    curr_upb NUMERIC(12,2),                      -- Current UPB
    curr_rate NUMERIC(5,3),                      -- Current rate (for ARMs)
    loan_age INTEGER,                            -- Months since origination
    remaining_term INTEGER,                      -- Months to maturity
    
    -- Payment status
    status TEXT,                                 -- Current, 30DPD, 60DPD, etc.
    dlq_status INTEGER,                          -- 0, 1, 2, 3+ months delinquent
    months_dlq INTEGER,                          -- Consecutive months delinquent
    
    -- Modification/forbearance
    mod_flag BOOLEAN DEFAULT FALSE,
    forbear_flag BOOLEAN DEFAULT FALSE,
    mod_date DATE,
    
    -- Activity
    paid_amount NUMERIC(12,2),                   -- Amount paid this month
    prepay_amount NUMERIC(12,2),                 -- Prepayment amount
    
    -- Metadata
    source_file TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    
    PRIMARY KEY (loan_id, as_of_date)
);

CREATE INDEX IF NOT EXISTS idx_fact_loan_month_date ON fact_loan_month(as_of_date);
CREATE INDEX IF NOT EXISTS idx_fact_loan_month_status ON fact_loan_month(status);
CREATE INDEX IF NOT EXISTS idx_fact_loan_month_dlq ON fact_loan_month(dlq_status);

-- ============================================================================
-- CALENDAR DIMENSION
-- ============================================================================

-- Table: dim_calendar
-- Calendar dimension for date joins and business day logic
CREATE TABLE IF NOT EXISTS dim_calendar (
    date_key DATE PRIMARY KEY,
    year INTEGER,
    quarter INTEGER,
    month INTEGER,
    day_of_month INTEGER,
    day_of_week INTEGER,                         -- 1=Monday, 7=Sunday
    week_of_year INTEGER,
    is_month_end BOOLEAN,
    is_quarter_end BOOLEAN,
    is_year_end BOOLEAN,
    is_business_day BOOLEAN,
    is_fed_holiday BOOLEAN,
    bd_of_month INTEGER,                         -- Business day of month (BD1, BD4, etc.)
    
    -- Factor/disclosure dates
    is_factor_date BOOLEAN DEFAULT FALSE,        -- Monthly factor release dates
    is_disclosure_date BOOLEAN DEFAULT FALSE     -- Pool disclosure dates
);

-- Populate calendar for 2000-2030
INSERT INTO dim_calendar (date_key, year, quarter, month, day_of_month, day_of_week, 
                          week_of_year, is_month_end, is_quarter_end, is_year_end, 
                          is_business_day, is_fed_holiday, bd_of_month)
SELECT 
    d::DATE as date_key,
    EXTRACT(YEAR FROM d) as year,
    EXTRACT(QUARTER FROM d) as quarter,
    EXTRACT(MONTH FROM d) as month,
    EXTRACT(DAY FROM d) as day_of_month,
    EXTRACT(ISODOW FROM d) as day_of_week,
    EXTRACT(WEEK FROM d) as week_of_year,
    d = (DATE_TRUNC('MONTH', d) + INTERVAL '1 MONTH - 1 day')::DATE as is_month_end,
    d = (DATE_TRUNC('QUARTER', d) + INTERVAL '3 MONTHS - 1 day')::DATE as is_quarter_end,
    EXTRACT(MONTH FROM d) = 12 AND EXTRACT(DAY FROM d) = 31 as is_year_end,
    EXTRACT(ISODOW FROM d) < 6 as is_business_day,  -- Simplified, no holidays
    FALSE as is_fed_holiday,
    NULL as bd_of_month  -- Would need window function to calculate
FROM generate_series('2000-01-01'::DATE, '2030-12-31'::DATE, '1 day'::INTERVAL) d
ON CONFLICT (date_key) DO NOTHING;

-- ============================================================================
-- SECURITY ISSUANCE (Intraday/Monthly)
-- ============================================================================

-- Table: freddie_security_issuance
-- Daily/intraday security issuance data (FRE_FISS files)
CREATE TABLE IF NOT EXISTS freddie_security_issuance (
    id SERIAL PRIMARY KEY,
    issuance_date DATE NOT NULL,
    pool_id TEXT,
    cusip TEXT,
    
    -- Security details
    prefix TEXT,
    product_type TEXT,
    coupon NUMERIC(5,3),
    issue_price NUMERIC(8,4),
    
    -- Size
    orig_face NUMERIC(15,2),
    
    -- File metadata
    source_file TEXT,
    file_sequence INTEGER,                       -- 1-4 for intraday files
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_freddie_issuance_date ON freddie_security_issuance(issuance_date);
CREATE INDEX IF NOT EXISTS idx_freddie_issuance_cusip ON freddie_security_issuance(cusip);

-- ============================================================================
-- VIEWS
-- ============================================================================

-- View: pool_latest_factor
-- Most recent factor for each pool
CREATE OR REPLACE VIEW pool_latest_factor AS
SELECT DISTINCT ON (pool_id)
    p.pool_id,
    p.cusip,
    p.product_type,
    p.coupon,
    f.as_of_date,
    f.factor,
    f.curr_upb,
    f.loan_count,
    f.cpr,
    f.serious_dlq_rate
FROM dim_pool p
JOIN fact_pool_month f ON p.pool_id = f.pool_id
ORDER BY pool_id, as_of_date DESC;

-- View: pool_summary
-- Pool summary with latest metrics and AI tags
CREATE OR REPLACE VIEW pool_summary AS
SELECT 
    p.*,
    f.as_of_date as latest_factor_date,
    f.factor,
    f.curr_upb,
    f.loan_count as curr_loan_count,
    f.cpr,
    f.serious_dlq_rate
FROM dim_pool p
LEFT JOIN pool_latest_factor f ON p.pool_id = f.pool_id;

-- ============================================================================
-- TRIGGERS
-- ============================================================================

-- Auto-update updated_at on dim_pool
DROP TRIGGER IF EXISTS update_dim_pool_updated_at ON dim_pool;
CREATE TRIGGER update_dim_pool_updated_at
    BEFORE UPDATE ON dim_pool
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Auto-update updated_at on dim_loan
DROP TRIGGER IF EXISTS update_dim_loan_updated_at ON dim_loan;
CREATE TRIGGER update_dim_loan_updated_at
    BEFORE UPDATE ON dim_loan
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================================================
-- COMMENTS
-- ============================================================================

COMMENT ON TABLE dim_pool IS 'Pool dimension: static and slowly-changing pool attributes';
COMMENT ON TABLE dim_loan IS 'Loan dimension: static loan-level attributes from disclosure';
COMMENT ON TABLE fact_pool_month IS 'Monthly pool performance facts: factor, UPB, prepays, delinquency';
COMMENT ON TABLE fact_loan_month IS 'Monthly loan performance facts: status, payments, modifications';
COMMENT ON TABLE dim_calendar IS 'Calendar dimension for date logic and factor dates';
COMMENT ON TABLE freddie_security_issuance IS 'Daily security issuance from FRE_FISS files';

COMMENT ON COLUMN dim_pool.behavior_tags IS 'AI-generated behavioral tags (JSONB): burnout_candidate, bear_market_stable, etc.';
COMMENT ON COLUMN dim_pool.risk_profile IS 'AI-generated overall risk profile classification';
