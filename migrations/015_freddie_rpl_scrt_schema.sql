-- Migration 015: Freddie Mac RPL (Re-performing Loans) / SCRT / SLST Schema
-- SCRT = Seasoned Credit Risk Transfer
-- SLST = Seasoned Loan Structured Transactions
-- These are formerly distressed loans that Freddie sells to investors

-- =============================================================================
-- RPL Loan ID Mapping Table
-- Maps SFLLD loan IDs to their SCRT/SLST transaction IDs
-- =============================================================================

CREATE TABLE IF NOT EXISTS rpl_loan_id_mapping (
    id SERIAL PRIMARY KEY,
    
    -- SFLLD Identifiers
    sflld_loan_sequence VARCHAR(30) NOT NULL,  -- Loan ID in SFLLD dataset
    
    -- SCRT/SLST Transaction Info
    transaction_type VARCHAR(10),  -- SCRT or SLST
    transaction_name VARCHAR(100),
    deal_id VARCHAR(50),
    
    -- Mapping metadata
    sale_date DATE,
    
    -- Source file tracking
    source_file VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW()
);

-- =============================================================================
-- RPL Excluded Loans (non-standard that went through SCRT/SLST)
-- =============================================================================

CREATE TABLE IF NOT EXISTS rpl_loan_id_mapping_excl (
    id SERIAL PRIMARY KEY,
    
    -- Non-Standard SFLLD Identifiers
    sflld_loan_sequence VARCHAR(30) NOT NULL,
    
    -- Transaction Info
    transaction_type VARCHAR(10),
    transaction_name VARCHAR(100),
    deal_id VARCHAR(50),
    
    sale_date DATE,
    source_file VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW()
);

-- =============================================================================
-- Indexes
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_rpl_sflld_id ON rpl_loan_id_mapping(sflld_loan_sequence);
CREATE INDEX IF NOT EXISTS idx_rpl_transaction ON rpl_loan_id_mapping(transaction_type, transaction_name);
CREATE INDEX IF NOT EXISTS idx_rpl_excl_sflld_id ON rpl_loan_id_mapping_excl(sflld_loan_sequence);

-- =============================================================================
-- Comments
-- =============================================================================

COMMENT ON TABLE rpl_loan_id_mapping IS 'Maps SFLLD standard loans to SCRT/SLST credit risk transfer transactions';
COMMENT ON TABLE rpl_loan_id_mapping_excl IS 'Maps SFLLD non-standard loans to SCRT/SLST transactions';
COMMENT ON COLUMN rpl_loan_id_mapping.transaction_type IS 'SCRT = Seasoned Credit Risk Transfer, SLST = Seasoned Loan Structured Transaction';
