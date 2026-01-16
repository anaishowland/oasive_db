-- Migration 014: Fannie Mae HARP (Home Affordable Refinance Program) Schema
-- HARP allowed underwater borrowers to refinance 2009-2018
-- Key: Loan_Mapping.txt links old loan IDs to new post-HARP loan IDs

-- =============================================================================
-- HARP Loan Performance Table
-- =============================================================================

CREATE TABLE IF NOT EXISTS dim_loan_fannie_harp (
    -- Identifiers
    loan_id VARCHAR(30) PRIMARY KEY,
    original_loan_id VARCHAR(30),  -- Pre-HARP loan ID (from Loan_Mapping.txt)
    
    -- Origination Data
    channel VARCHAR(10),  -- R=Retail, B=Broker, C=Correspondent
    seller_name VARCHAR(100),
    servicer_name VARCHAR(100),
    
    -- Loan Characteristics
    orig_rate DECIMAL(6,4),
    orig_upb DECIMAL(14,2),
    orig_loan_term INTEGER,
    orig_date DATE,
    first_payment_date DATE,
    maturity_date DATE,
    
    -- Borrower/Property
    ltv DECIMAL(6,2),
    cltv DECIMAL(6,2),  -- Combined LTV (important for HARP - often >100%)
    dti DECIMAL(6,2),
    fico INTEGER,
    co_borrower_fico INTEGER,
    num_borrowers INTEGER,
    first_time_buyer VARCHAR(1),
    
    -- Property
    loan_purpose VARCHAR(10),  -- R=Refinance (all HARP loans)
    property_type VARCHAR(10),
    num_units INTEGER,
    occupancy VARCHAR(1),  -- P=Primary, I=Investment, S=Second
    state VARCHAR(2),
    msa VARCHAR(50),
    zipcode VARCHAR(5),
    
    -- HARP-specific fields
    harp_indicator VARCHAR(1) DEFAULT 'Y',  -- Always Y for HARP loans
    ltv_at_harp_refi DECIMAL(6,2),  -- LTV when refinanced under HARP
    pre_harp_ltv DECIMAL(6,2),  -- LTV before HARP (often >100%)
    
    -- Insurance
    mi_pct DECIMAL(6,2),
    mi_type VARCHAR(10),
    
    -- Performance (final state)
    current_upb DECIMAL(14,2),
    loan_age INTEGER,
    dlq_status VARCHAR(10),
    zero_balance_code VARCHAR(2),
    zero_balance_date DATE,
    
    -- Metadata
    source VARCHAR(50) DEFAULT 'FANNIE_HARP',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- =============================================================================
-- HARP Loan Mapping Table (Old Loan -> New Loan)
-- Critical for tracking borrowers through the HARP refi
-- =============================================================================

CREATE TABLE IF NOT EXISTS harp_loan_mapping (
    id SERIAL PRIMARY KEY,
    original_loan_id VARCHAR(30) NOT NULL,  -- Pre-HARP loan ID
    new_loan_id VARCHAR(30) NOT NULL,       -- Post-HARP loan ID
    
    -- Mapping metadata
    mapping_date DATE,
    
    created_at TIMESTAMP DEFAULT NOW(),
    
    UNIQUE(original_loan_id, new_loan_id)
);

-- =============================================================================
-- Indexes
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_harp_orig_loan ON dim_loan_fannie_harp(original_loan_id);
CREATE INDEX IF NOT EXISTS idx_harp_state ON dim_loan_fannie_harp(state);
CREATE INDEX IF NOT EXISTS idx_harp_cltv ON dim_loan_fannie_harp(cltv);
CREATE INDEX IF NOT EXISTS idx_harp_orig_date ON dim_loan_fannie_harp(orig_date);
CREATE INDEX IF NOT EXISTS idx_harp_mapping_orig ON harp_loan_mapping(original_loan_id);
CREATE INDEX IF NOT EXISTS idx_harp_mapping_new ON harp_loan_mapping(new_loan_id);

-- =============================================================================
-- Comments
-- =============================================================================

COMMENT ON TABLE dim_loan_fannie_harp IS 'Fannie Mae HARP (Home Affordable Refinance Program) loans 2009-2018. Allowed underwater borrowers to refinance.';
COMMENT ON TABLE harp_loan_mapping IS 'Maps pre-HARP loan IDs to post-HARP loan IDs for tracking borrower continuity';
COMMENT ON COLUMN dim_loan_fannie_harp.cltv IS 'Combined LTV - often exceeds 100% for HARP loans (underwater borrowers)';
COMMENT ON COLUMN dim_loan_fannie_harp.original_loan_id IS 'Links to the loan ID before HARP refinance via harp_loan_mapping';
