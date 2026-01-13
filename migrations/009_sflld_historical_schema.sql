-- ============================================================================
-- Migration 009: SFLLD Historical Loan-Level Data Schema
-- ============================================================================
-- 
-- This creates tables for Freddie Mac's Single-Family Loan-Level Dataset (SFLLD)
-- which contains historical data from 1999-2025 (54.8 million loans).
--
-- Data Source: Clarity Data Intelligence Platform
-- https://claritydownload.fmapps.freddiemac.com/CRT/#/sflld
--
-- Schema based on Freddie Mac's published file layout.
-- ============================================================================

-- Historical Loan Origination Data (1999-2025)
-- This stores loan characteristics at time of origination
CREATE TABLE IF NOT EXISTS dim_loan_historical (
    id SERIAL PRIMARY KEY,
    
    -- Unique loan identifier (Freddie Mac's internal ID)
    loan_sequence VARCHAR(20) UNIQUE NOT NULL,
    
    -- Borrower/Credit characteristics
    credit_score INTEGER,                    -- Credit score at origination (300-850)
    first_time_buyer VARCHAR(1),             -- Y=Yes, N=No
    num_borrowers INTEGER,                   -- Number of borrowers
    dti DECIMAL(6,2),                        -- Original Debt-to-Income ratio
    
    -- Loan characteristics
    orig_upb DECIMAL(14,2),                  -- Original unpaid principal balance
    orig_rate DECIMAL(6,3),                  -- Original interest rate
    loan_term INTEGER,                       -- Original loan term (months)
    amort_type VARCHAR(5),                   -- FRM, ARM, etc.
    loan_purpose VARCHAR(1),                 -- P=Purchase, C=CashOut Refi, N=NoCash Refi, R=Rehab
    channel VARCHAR(1),                      -- R=Retail, B=Broker, C=Correspondent
    prepay_penalty VARCHAR(1),               -- Y=Yes, N=No
    
    -- LTV/CLTV
    ltv DECIMAL(6,2),                        -- Original LTV
    cltv DECIMAL(6,2),                       -- Original Combined LTV
    
    -- Property characteristics
    property_type VARCHAR(2),                -- SF, PU, CO, MH, CP
    num_units INTEGER,                       -- Number of units (1-4)
    occupancy VARCHAR(1),                    -- P=Primary, I=Investment, S=Second Home
    state VARCHAR(2),                        -- Property state (2-letter)
    zipcode VARCHAR(5),                      -- 3-digit ZIP code
    msa VARCHAR(10),                         -- Metropolitan Statistical Area code
    
    -- Dates
    first_payment_date DATE,                 -- First payment date
    maturity_date DATE,                      -- Original maturity date
    
    -- Mortgage insurance
    mi_pct DECIMAL(6,2),                     -- Mortgage insurance percentage
    
    -- Parties
    seller_name VARCHAR(100),                -- Seller/Originator name
    servicer_name VARCHAR(100),              -- Servicer name
    
    -- Program indicators
    super_conforming VARCHAR(1),             -- Super conforming flag
    harp_indicator VARCHAR(1),               -- HARP refinance indicator
    pre_harp_loan_seq VARCHAR(20),           -- Pre-HARP loan sequence (for HARP refis)
    program_indicator VARCHAR(1),            -- H=HARP, F=FTHB, 9=Other
    io_indicator VARCHAR(1),                 -- Interest-only indicator
    
    -- Data source tracking
    source VARCHAR(20) DEFAULT 'SFLLD',      -- Data source identifier
    vintage_year INTEGER,                    -- Year of origination (derived)
    
    -- Metadata
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Historical Loan Performance/Monthly Data
-- This stores monthly snapshots of loan status (prepay, foreclosure, etc.)
CREATE TABLE IF NOT EXISTS fact_loan_month_historical (
    id SERIAL PRIMARY KEY,
    
    -- Link to origination
    loan_sequence VARCHAR(20) NOT NULL,
    
    -- Reporting period
    report_date DATE NOT NULL,               -- Monthly reporting period
    
    -- Current loan status
    current_upb DECIMAL(14,2),               -- Current unpaid principal balance
    current_rate DECIMAL(6,3),               -- Current interest rate
    loan_age INTEGER,                        -- Age of loan in months
    rem_months INTEGER,                      -- Remaining months to maturity
    
    -- Delinquency
    dlq_status VARCHAR(3),                   -- Current delinquency status (0=current, 1-9=DQ months)
    
    -- Zero balance (loan termination)
    zero_balance_code VARCHAR(2),            -- Reason for termination (01=Prepay, 09=REO, etc.)
    zero_balance_date DATE,                  -- Date loan reached zero balance
    
    -- Loss/Recovery data (for defaults)
    actual_loss DECIMAL(14,2),               -- Actual loss amount
    mi_recoveries DECIMAL(14,2),             -- MI recovery amount
    net_sales_proceeds DECIMAL(14,2),        -- Net sales proceeds (for REO)
    
    -- Estimated LTV (updated over time)
    eltv DECIMAL(6,2),                       -- Estimated current LTV
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW(),
    
    -- Composite key for uniqueness
    UNIQUE(loan_sequence, report_date)
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_loan_hist_vintage ON dim_loan_historical(vintage_year);
CREATE INDEX IF NOT EXISTS idx_loan_hist_state ON dim_loan_historical(state);
CREATE INDEX IF NOT EXISTS idx_loan_hist_servicer ON dim_loan_historical(servicer_name);
CREATE INDEX IF NOT EXISTS idx_loan_hist_credit ON dim_loan_historical(credit_score);
CREATE INDEX IF NOT EXISTS idx_loan_hist_ltv ON dim_loan_historical(ltv);
CREATE INDEX IF NOT EXISTS idx_loan_hist_purpose ON dim_loan_historical(loan_purpose);
CREATE INDEX IF NOT EXISTS idx_loan_hist_occupancy ON dim_loan_historical(occupancy);
CREATE INDEX IF NOT EXISTS idx_loan_hist_source ON dim_loan_historical(source);

CREATE INDEX IF NOT EXISTS idx_fact_hist_loan ON fact_loan_month_historical(loan_sequence);
CREATE INDEX IF NOT EXISTS idx_fact_hist_date ON fact_loan_month_historical(report_date);
CREATE INDEX IF NOT EXISTS idx_fact_hist_zb ON fact_loan_month_historical(zero_balance_code);

-- Track file processing for SFLLD files
CREATE TABLE IF NOT EXISTS sflld_file_catalog (
    id SERIAL PRIMARY KEY,
    filename VARCHAR(255) UNIQUE NOT NULL,
    year INTEGER,
    file_type VARCHAR(20),                   -- 'standard', 'nonstandard', 'sample'
    download_status VARCHAR(20) DEFAULT 'pending',
    downloaded_at TIMESTAMP,
    processed_at TIMESTAMP,
    loan_count INTEGER,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Insert expected files for tracking (Standard dataset 1999-2025)
INSERT INTO sflld_file_catalog (filename, year, file_type)
SELECT 
    'historical_data_' || year || '.zip',
    year,
    'standard'
FROM generate_series(1999, 2025) AS year
ON CONFLICT (filename) DO NOTHING;

-- Comments for documentation
COMMENT ON TABLE dim_loan_historical IS 'Freddie Mac SFLLD historical loan-level data (1999-2025). 54.8M loans.';
COMMENT ON TABLE fact_loan_month_historical IS 'Monthly performance snapshots for historical loans.';
COMMENT ON TABLE sflld_file_catalog IS 'Tracks SFLLD file downloads and processing status.';

COMMENT ON COLUMN fact_loan_month_historical.zero_balance_code IS 
    '01=Prepaid/Matured, 02=Third Party Sale, 03=Short Sale, 06=Repurchased, 09=REO, 15=Note Sale';
COMMENT ON COLUMN dim_loan_historical.loan_purpose IS 
    'P=Purchase, C=Cash-Out Refi, N=No Cash-Out Refi, R=Rehab';
COMMENT ON COLUMN dim_loan_historical.channel IS 
    'R=Retail, B=Broker, C=Correspondent';
COMMENT ON COLUMN dim_loan_historical.occupancy IS 
    'P=Primary Residence, I=Investment, S=Second Home';
