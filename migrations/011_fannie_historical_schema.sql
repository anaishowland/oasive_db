-- Migration 011: Fannie Mae Historical Loan Data Schema
-- Single-Family Loan Performance (SFLP) Data from Data Dynamics
-- Coverage: 2000Q1 - 2025Q2 (~62M loans)

-- ============================================================================
-- FANNIE MAE ACQUISITION DATA (Loan Characteristics at Origination)
-- ============================================================================
-- Source: Acquisition files from datadynamics.fanniemae.com
-- Format: Pipe-delimited, no header row

CREATE TABLE IF NOT EXISTS dim_loan_fannie_historical (
    -- Primary Key
    loan_id TEXT PRIMARY KEY,                    -- Fannie Mae Loan Identifier
    
    -- Loan Characteristics
    channel TEXT,                                -- R=Retail, B=Broker, C=Correspondent, T=TPO
    seller_name TEXT,                            -- Seller/Originator name
    servicer_name TEXT,                          -- Current servicer
    orig_rate DECIMAL(6,3),                      -- Original interest rate
    orig_upb DECIMAL(14,2),                      -- Original UPB
    orig_loan_term INT,                          -- Original loan term (months)
    orig_date DATE,                              -- Origination date (YYYY-MM-01)
    first_payment_date DATE,                     -- First payment date
    ltv DECIMAL(6,2),                            -- Original LTV
    cltv DECIMAL(6,2),                           -- Original Combined LTV
    num_borrowers INT,                           -- Number of borrowers
    dti DECIMAL(5,2),                            -- Original DTI
    fico INT,                                    -- Borrower credit score at origination
    first_time_buyer TEXT,                       -- Y/N
    loan_purpose TEXT,                           -- P=Purchase, C=Cash-out Refi, N=No Cash-out Refi
    property_type TEXT,                          -- SF/PU/CO/MH/CP
    num_units INT,                               -- Number of units
    occupancy TEXT,                              -- P=Principal, I=Investment, S=Second Home
    state TEXT,                                  -- Property state (2-char)
    zipcode TEXT,                                -- 3-digit ZIP
    mi_pct DECIMAL(5,2),                         -- Mortgage insurance percentage
    product_type TEXT,                           -- FRM/ARM
    co_borrower_fico INT,                        -- Co-borrower credit score
    mi_type TEXT,                                -- 1=Borrower Paid, 2=Lender Paid
    relocation_mortgage TEXT,                    -- Y/N
    
    -- Program Indicators (added in later file versions)
    super_conforming TEXT,                       -- Y/N (high-balance loan)
    pre_harp_loan_id TEXT,                       -- Original loan ID if HARP refinance
    program_indicator TEXT,                      -- H=HARP, F=FTHB, R=Refi Plus
    property_valuation TEXT,                     -- Appraisal type
    io_indicator TEXT,                           -- Interest-only indicator
    
    -- Metadata
    source TEXT DEFAULT 'FANNIE_SFLP',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- ============================================================================
-- FANNIE MAE PERFORMANCE DATA (Monthly Snapshots)
-- ============================================================================
-- Source: Performance files from datadynamics.fanniemae.com

CREATE TABLE IF NOT EXISTS fact_loan_month_fannie_historical (
    id SERIAL PRIMARY KEY,
    loan_id TEXT NOT NULL,                       -- FK to dim_loan_fannie_historical
    report_date DATE NOT NULL,                   -- Monthly reporting period
    
    -- UPB and Rate
    current_upb DECIMAL(14,2),                   -- Current actual UPB
    current_rate DECIMAL(6,3),                   -- Current interest rate
    
    -- Loan Status
    loan_age INT,                                -- Months since origination
    rem_months INT,                              -- Remaining months to maturity
    dlq_status TEXT,                             -- Current delinquency (0,1,2,3...XX)
    modification_flag TEXT,                      -- Y/N
    
    -- Zero Balance (Prepay/Default Events)
    zero_balance_code TEXT,                      -- Termination reason
    zero_balance_date DATE,                      -- Termination date
    
    -- Foreclosure/REO Fields
    foreclosure_date DATE,
    disposition_date DATE,
    foreclosure_costs DECIMAL(12,2),
    property_preservation DECIMAL(12,2),
    recovery_costs DECIMAL(12,2),
    misc_expenses DECIMAL(12,2),
    taxes_held DECIMAL(12,2),
    net_proceeds DECIMAL(14,2),
    credit_enhancement DECIMAL(12,2),
    reo_proceeds DECIMAL(14,2),
    
    -- Modification Details
    non_interest_bearing_upb DECIMAL(14,2),
    principal_forgiveness DECIMAL(14,2),
    
    -- Estimated Current LTV
    eltv DECIMAL(6,2),
    
    -- Metadata
    created_at TIMESTAMP DEFAULT NOW(),
    
    CONSTRAINT uq_fannie_loan_month UNIQUE (loan_id, report_date)
);

-- ============================================================================
-- FILE CATALOG (Track Downloaded Files)
-- ============================================================================

CREATE TABLE IF NOT EXISTS fannie_sflp_file_catalog (
    id SERIAL PRIMARY KEY,
    filename TEXT NOT NULL UNIQUE,
    year INT,
    quarter INT,
    file_type TEXT,                              -- acquisition, performance
    download_status TEXT DEFAULT 'pending',      -- pending, downloaded, processed, error
    local_path TEXT,
    gcs_path TEXT,
    records_loaded INT DEFAULT 0,
    downloaded_at TIMESTAMP,
    processed_at TIMESTAMP,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- ============================================================================
-- INDEXES FOR PERFORMANCE
-- ============================================================================

-- Loan dimension indexes
CREATE INDEX IF NOT EXISTS idx_fannie_loan_state ON dim_loan_fannie_historical(state);
CREATE INDEX IF NOT EXISTS idx_fannie_loan_servicer ON dim_loan_fannie_historical(servicer_name);
CREATE INDEX IF NOT EXISTS idx_fannie_loan_orig_date ON dim_loan_fannie_historical(orig_date);
CREATE INDEX IF NOT EXISTS idx_fannie_loan_fico ON dim_loan_fannie_historical(fico);
CREATE INDEX IF NOT EXISTS idx_fannie_loan_ltv ON dim_loan_fannie_historical(ltv);
CREATE INDEX IF NOT EXISTS idx_fannie_loan_purpose ON dim_loan_fannie_historical(loan_purpose);

-- Performance fact indexes
CREATE INDEX IF NOT EXISTS idx_fannie_perf_loan ON fact_loan_month_fannie_historical(loan_id);
CREATE INDEX IF NOT EXISTS idx_fannie_perf_date ON fact_loan_month_fannie_historical(report_date);
CREATE INDEX IF NOT EXISTS idx_fannie_perf_zero_code ON fact_loan_month_fannie_historical(zero_balance_code);
CREATE INDEX IF NOT EXISTS idx_fannie_perf_dlq ON fact_loan_month_fannie_historical(dlq_status);

-- Composite for prepay analysis
CREATE INDEX IF NOT EXISTS idx_fannie_perf_prepay_analysis 
    ON fact_loan_month_fannie_historical(loan_id, report_date, zero_balance_code);

-- ============================================================================
-- ZERO BALANCE CODES (For Reference - Same as Freddie)
-- ============================================================================
COMMENT ON TABLE dim_loan_fannie_historical IS 'Fannie Mae historical loan data (2000-2025) from Data Dynamics SFLP';
COMMENT ON TABLE fact_loan_month_fannie_historical IS 'Monthly performance snapshots for Fannie Mae historical loans';
COMMENT ON COLUMN fact_loan_month_fannie_historical.zero_balance_code IS 
    '01=Prepaid/Matured, 02=Third Party Sale, 03=Short Sale, 06=Repurchased, 09=REO, 15=Note Sale, 96=Inactive';

-- ============================================================================
-- UNIFIED VIEWS FOR CROSS-GSE ANALYSIS
-- ============================================================================

-- Combined historical loan view (Freddie + Fannie)
CREATE OR REPLACE VIEW v_all_historical_loans AS
SELECT 
    loan_sequence AS loan_id,
    'FREDDIE' AS gse,
    credit_score AS fico,
    orig_rate,
    orig_upb,
    ltv,
    cltv,
    dti,
    state,
    zipcode,
    occupancy,
    loan_purpose,
    property_type,
    loan_term AS orig_loan_term,
    first_payment_date,
    servicer_name,
    seller_name,
    channel,
    first_time_buyer,
    source,
    created_at
FROM dim_loan_historical
UNION ALL
SELECT 
    loan_id,
    'FANNIE' AS gse,
    fico,
    orig_rate,
    orig_upb,
    ltv,
    cltv,
    dti,
    state,
    zipcode,
    occupancy,
    loan_purpose,
    property_type,
    orig_loan_term,
    first_payment_date,
    servicer_name,
    seller_name,
    channel,
    first_time_buyer,
    source,
    created_at
FROM dim_loan_fannie_historical;

COMMENT ON VIEW v_all_historical_loans IS 'Unified view of Freddie Mac and Fannie Mae historical loans for cross-GSE analysis';
