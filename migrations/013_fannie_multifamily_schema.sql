-- Migration 013: Fannie Mae Multifamily (Commercial MBS) Schema
-- Creates tables for multifamily/apartment building loan data

-- =============================================================================
-- Multifamily Loan Performance Table
-- =============================================================================

CREATE TABLE IF NOT EXISTS dim_loan_fannie_multifamily (
    -- Identifiers
    loan_id VARCHAR(30) PRIMARY KEY,
    deal_name VARCHAR(100),
    property_name VARCHAR(200),
    
    -- Loan Characteristics
    orig_date DATE,
    maturity_date DATE,
    orig_upb DECIMAL(14,2),
    current_upb DECIMAL(14,2),
    orig_rate DECIMAL(6,4),
    current_rate DECIMAL(6,4),
    loan_term_months INTEGER,
    amortization_months INTEGER,
    
    -- Property Info
    property_type VARCHAR(50),  -- Multifamily, Student Housing, Senior Housing, etc.
    property_city VARCHAR(100),
    property_state VARCHAR(2),
    property_zip VARCHAR(10),
    msa VARCHAR(50),
    units INTEGER,  -- Number of apartment units
    year_built INTEGER,
    
    -- Credit Metrics
    orig_ltv DECIMAL(6,2),
    current_ltv DECIMAL(6,2),
    orig_dscr DECIMAL(6,2),  -- Debt Service Coverage Ratio at origination
    current_dscr DECIMAL(6,2),
    noi DECIMAL(14,2),  -- Net Operating Income
    
    -- Loan Features
    loan_purpose VARCHAR(50),  -- Acquisition, Refinance, Supplemental
    prepay_lockout_end_date DATE,
    yield_maintenance_end_date DATE,
    defeasance_allowed BOOLEAN,
    
    -- Performance
    dlq_status VARCHAR(20),
    special_servicer VARCHAR(100),
    modification_flag BOOLEAN,
    
    -- Metadata
    source VARCHAR(50) DEFAULT 'FANNIE_MF',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- =============================================================================
-- Multifamily DSCR (Debt Service Coverage Ratio) Table
-- =============================================================================

CREATE TABLE IF NOT EXISTS fact_multifamily_dscr (
    id SERIAL PRIMARY KEY,
    loan_id VARCHAR(30) REFERENCES dim_loan_fannie_multifamily(loan_id),
    as_of_date DATE NOT NULL,
    
    -- DSCR Metrics
    dscr DECIMAL(6,2),
    noi DECIMAL(14,2),
    debt_service DECIMAL(14,2),
    
    -- Additional metrics if available
    occupancy_rate DECIMAL(5,2),
    effective_gross_income DECIMAL(14,2),
    
    created_at TIMESTAMP DEFAULT NOW(),
    
    UNIQUE(loan_id, as_of_date)
);

-- =============================================================================
-- Indexes
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_mf_loan_state ON dim_loan_fannie_multifamily(property_state);
CREATE INDEX IF NOT EXISTS idx_mf_loan_type ON dim_loan_fannie_multifamily(property_type);
CREATE INDEX IF NOT EXISTS idx_mf_loan_orig_date ON dim_loan_fannie_multifamily(orig_date);
CREATE INDEX IF NOT EXISTS idx_mf_dscr_date ON fact_multifamily_dscr(as_of_date);

-- =============================================================================
-- Comments
-- =============================================================================

COMMENT ON TABLE dim_loan_fannie_multifamily IS 'Fannie Mae Multifamily (commercial/apartment) loan data from Data Dynamics';
COMMENT ON TABLE fact_multifamily_dscr IS 'Monthly DSCR (Debt Service Coverage Ratio) metrics for multifamily loans';
COMMENT ON COLUMN dim_loan_fannie_multifamily.dscr IS 'Debt Service Coverage Ratio - NOI / Debt Service. >1.0 means income covers debt.';
