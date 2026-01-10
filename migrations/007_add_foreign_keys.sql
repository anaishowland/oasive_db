-- Migration 007: Add Foreign Key Relationships
-- Links loan-level and pool-level data together

-- =============================================================================
-- Foreign Keys for dim_loan -> dim_pool
-- =============================================================================

-- Note: We add FK with ON DELETE SET NULL to handle cases where pool is deleted
-- but we want to keep loan data for historical analysis

ALTER TABLE dim_loan 
ADD CONSTRAINT fk_dim_loan_pool 
FOREIGN KEY (pool_id) REFERENCES dim_pool(pool_id) 
ON DELETE SET NULL
ON UPDATE CASCADE;

-- =============================================================================
-- Foreign Keys for fact_pool_month -> dim_pool
-- =============================================================================

ALTER TABLE fact_pool_month 
ADD CONSTRAINT fk_fact_pool_month_pool 
FOREIGN KEY (pool_id) REFERENCES dim_pool(pool_id) 
ON DELETE CASCADE
ON UPDATE CASCADE;

-- =============================================================================
-- Foreign Keys for fact_loan_month -> dim_loan
-- =============================================================================

ALTER TABLE fact_loan_month 
ADD CONSTRAINT fk_fact_loan_month_loan 
FOREIGN KEY (loan_id) REFERENCES dim_loan(loan_id) 
ON DELETE CASCADE
ON UPDATE CASCADE;

-- =============================================================================
-- Indexes for faster joins
-- =============================================================================

-- Pool lookups
CREATE INDEX IF NOT EXISTS idx_dim_pool_cusip ON dim_pool(cusip);
CREATE INDEX IF NOT EXISTS idx_dim_pool_servicer ON dim_pool(servicer_name);
CREATE INDEX IF NOT EXISTS idx_dim_pool_issue_date ON dim_pool(issue_date);
CREATE INDEX IF NOT EXISTS idx_dim_pool_product ON dim_pool(product_type);

-- Loan lookups  
CREATE INDEX IF NOT EXISTS idx_dim_loan_pool_id ON dim_loan(pool_id);
CREATE INDEX IF NOT EXISTS idx_dim_loan_state ON dim_loan(state);
CREATE INDEX IF NOT EXISTS idx_dim_loan_fico ON dim_loan(fico);
CREATE INDEX IF NOT EXISTS idx_dim_loan_ltv ON dim_loan(ltv);
CREATE INDEX IF NOT EXISTS idx_dim_loan_purpose ON dim_loan(purpose);
CREATE INDEX IF NOT EXISTS idx_dim_loan_occupancy ON dim_loan(occupancy);

-- Fact table lookups
CREATE INDEX IF NOT EXISTS idx_fact_pool_month_date ON fact_pool_month(as_of_month);
CREATE INDEX IF NOT EXISTS idx_fact_loan_month_date ON fact_loan_month(as_of_month);

-- =============================================================================
-- Comments
-- =============================================================================

COMMENT ON CONSTRAINT fk_dim_loan_pool ON dim_loan IS 
'Links each loan to its containing pool. pool_id format: PREFIX-SECURITY_ID (e.g., CL-RA5094)';

COMMENT ON CONSTRAINT fk_fact_pool_month_pool ON fact_pool_month IS 
'Links monthly pool metrics to pool dimension';

COMMENT ON CONSTRAINT fk_fact_loan_month_loan ON fact_loan_month IS 
'Links monthly loan performance to loan dimension';
