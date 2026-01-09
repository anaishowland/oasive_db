-- Migration 006: Prepay Research Framework
-- Stores validation results, discoveries, and weight history for empirical research

-- =============================================================================
-- Table: research_validation_results
-- Stores results of validating each prepay assumption
-- =============================================================================
CREATE TABLE IF NOT EXISTS research_validation_results (
    id SERIAL PRIMARY KEY,
    assumption_id TEXT NOT NULL,           -- e.g., 'A001' for VA slower assumption
    run_date DATE NOT NULL,
    
    -- Results
    validated BOOLEAN,                      -- Did data confirm the assumption?
    observed_effect NUMERIC(5,3),          -- e.g., 0.72 = 28% slower than baseline
    expected_effect NUMERIC(5,3),          -- What we expected from assumption
    confidence_interval_low NUMERIC(5,3),
    confidence_interval_high NUMERIC(5,3),
    p_value NUMERIC(10,8),
    
    -- Sample info
    sample_pools INTEGER,
    sample_months INTEGER,
    time_period_start DATE,
    time_period_end DATE,
    
    -- Methodology
    methodology TEXT,                       -- 'stratified', 'regression', 'matched_pairs'
    confounders_controlled TEXT[],          -- ['incentive', 'wala', 'fico']
    
    -- Metadata
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE research_validation_results IS 'Empirical validation of prepay assumptions from Freddie Mac data';
COMMENT ON COLUMN research_validation_results.observed_effect IS 'CPR multiplier vs baseline (0.72 = 28% slower)';

-- =============================================================================
-- Table: research_discoveries
-- Stores unexpected patterns found through analysis
-- =============================================================================
CREATE TABLE IF NOT EXISTS research_discoveries (
    id SERIAL PRIMARY KEY,
    discovery_date DATE NOT NULL,
    discovery_type TEXT NOT NULL,           -- 'interaction', 'anomaly', 'regime_change', 'reversal'
    
    -- What was discovered
    factors TEXT[],                         -- ['loan_program', 'state']
    factor_values TEXT[],                   -- ['VA', 'NY']
    description TEXT NOT NULL,
    
    -- Effect magnitude
    observed_effect NUMERIC(5,3),
    expected_effect NUMERIC(5,3),
    deviation NUMERIC(5,3),                 -- (observed - expected) / expected
    
    -- Significance
    sample_size INTEGER,
    confidence TEXT CHECK (confidence IN ('high', 'medium', 'low')),
    
    -- Action taken
    incorporated_into_model BOOLEAN DEFAULT FALSE,
    incorporated_date DATE,
    notes TEXT,
    
    created_at TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE research_discoveries IS 'Unexpected prepay patterns not in original assumptions';

-- =============================================================================
-- Table: research_weight_history
-- Audit trail of composite score weight changes
-- =============================================================================
CREATE TABLE IF NOT EXISTS research_weight_history (
    id SERIAL PRIMARY KEY,
    effective_date DATE NOT NULL,
    factor TEXT NOT NULL,                   -- 'loan_program', 'balance_tier', etc.
    factor_value TEXT NOT NULL,             -- 'VA', 'LLB_85', etc.
    weight INTEGER NOT NULL,                -- Score adjustment (e.g., +15)
    
    -- Derivation
    based_on_validation_id INTEGER REFERENCES research_validation_results(id),
    methodology TEXT,                       -- 'empirical', 'market_consensus', 'placeholder'
    
    -- Change tracking
    previous_weight INTEGER,
    change_reason TEXT,
    
    created_at TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE research_weight_history IS 'Audit trail of composite score weight calibrations';

-- =============================================================================
-- Table: factor_effect_timeseries
-- Monthly factor effects for regime change detection
-- =============================================================================
CREATE TABLE IF NOT EXISTS factor_effect_timeseries (
    id SERIAL PRIMARY KEY,
    as_of_month DATE NOT NULL,
    factor TEXT NOT NULL,
    factor_value TEXT NOT NULL,
    
    -- Effect metrics
    segment_cpr NUMERIC(5,2),              -- Average CPR for this segment
    baseline_cpr NUMERIC(5,2),             -- Universe baseline CPR
    multiplier NUMERIC(5,3),               -- segment / baseline
    
    -- Sample info
    pool_count INTEGER,
    month_count INTEGER,
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    
    UNIQUE (as_of_month, factor, factor_value)
);

COMMENT ON TABLE factor_effect_timeseries IS 'Monthly factor effects for detecting regime changes over time';

-- =============================================================================
-- Indexes for query performance
-- =============================================================================
CREATE INDEX IF NOT EXISTS idx_validation_assumption ON research_validation_results(assumption_id);
CREATE INDEX IF NOT EXISTS idx_validation_date ON research_validation_results(run_date);
CREATE INDEX IF NOT EXISTS idx_validation_validated ON research_validation_results(validated);

CREATE INDEX IF NOT EXISTS idx_discoveries_type ON research_discoveries(discovery_type);
CREATE INDEX IF NOT EXISTS idx_discoveries_date ON research_discoveries(discovery_date);
CREATE INDEX IF NOT EXISTS idx_discoveries_incorporated ON research_discoveries(incorporated_into_model);

CREATE INDEX IF NOT EXISTS idx_weight_factor ON research_weight_history(factor, factor_value);
CREATE INDEX IF NOT EXISTS idx_weight_date ON research_weight_history(effective_date);

CREATE INDEX IF NOT EXISTS idx_effect_ts_month ON factor_effect_timeseries(as_of_month);
CREATE INDEX IF NOT EXISTS idx_effect_ts_factor ON factor_effect_timeseries(factor, factor_value);

-- =============================================================================
-- Initial placeholder weights (to be replaced by empirical values)
-- =============================================================================
INSERT INTO research_weight_history (effective_date, factor, factor_value, weight, methodology, change_reason)
VALUES
    -- Loan Program
    ('2025-01-09', 'loan_program', 'VA', 15, 'market_consensus', 'Initial placeholder based on market knowledge'),
    ('2025-01-09', 'loan_program', 'USDA', 12, 'market_consensus', 'Initial placeholder'),
    ('2025-01-09', 'loan_program', 'FHA', 5, 'market_consensus', 'Initial placeholder'),
    ('2025-01-09', 'loan_program', 'CONV', 0, 'market_consensus', 'Baseline'),
    
    -- Loan Balance Tiers
    ('2025-01-09', 'balance_tier', 'LLB_85', 15, 'market_consensus', 'Initial placeholder'),
    ('2025-01-09', 'balance_tier', 'LLB_110', 10, 'market_consensus', 'Initial placeholder'),
    ('2025-01-09', 'balance_tier', 'LLB_150', 5, 'market_consensus', 'Initial placeholder'),
    ('2025-01-09', 'balance_tier', 'HLB', 0, 'market_consensus', 'Baseline'),
    ('2025-01-09', 'balance_tier', 'JUMBO', -10, 'market_consensus', 'Initial placeholder'),
    
    -- LTV
    ('2025-01-09', 'ltv', '>90%', 10, 'market_consensus', 'Initial placeholder'),
    ('2025-01-09', 'ltv', '80-90%', 5, 'market_consensus', 'Initial placeholder'),
    ('2025-01-09', 'ltv', '<60%', -5, 'market_consensus', 'Initial placeholder'),
    
    -- FICO
    ('2025-01-09', 'fico', '<680', 10, 'market_consensus', 'Initial placeholder'),
    ('2025-01-09', 'fico', '680-720', 5, 'market_consensus', 'Initial placeholder'),
    ('2025-01-09', 'fico', '>780', -10, 'market_consensus', 'Initial placeholder'),
    
    -- Occupancy
    ('2025-01-09', 'occupancy', 'INVESTOR', 10, 'market_consensus', 'Initial placeholder'),
    ('2025-01-09', 'occupancy', 'SECOND_HOME', 5, 'market_consensus', 'Initial placeholder'),
    ('2025-01-09', 'occupancy', 'OWNER_OCC', 0, 'market_consensus', 'Baseline'),
    
    -- State Friction
    ('2025-01-09', 'state_friction', 'high', 8, 'market_consensus', 'NY, NJ, etc.'),
    ('2025-01-09', 'state_friction', 'moderate', 0, 'market_consensus', 'Baseline'),
    ('2025-01-09', 'state_friction', 'low', -5, 'market_consensus', 'CA, TX, FL'),
    
    -- Servicer Risk
    ('2025-01-09', 'servicer_risk', 'prepay_protected', 8, 'market_consensus', 'Bank servicers'),
    ('2025-01-09', 'servicer_risk', 'neutral', 0, 'market_consensus', 'Baseline'),
    ('2025-01-09', 'servicer_risk', 'prepay_exposed', -10, 'market_consensus', 'Digital servicers')
ON CONFLICT DO NOTHING;
