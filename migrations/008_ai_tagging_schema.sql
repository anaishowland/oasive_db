-- Migration 008: AI Tagging Schema Updates
-- Based on: docs/ai_tagging_design.md v2.0
-- Date: 2026-01-10

-- ============================================================================
-- SECTION 1: Rename existing columns to match new naming convention
-- ============================================================================

-- Rename servicer_quality_tag to servicer_prepay_risk (investor perspective)
ALTER TABLE dim_pool RENAME COLUMN servicer_quality_tag TO servicer_prepay_risk;

-- ============================================================================
-- SECTION 2: Add new static attribute columns (Layer 1)
-- ============================================================================

-- Loan balance tier (LLB1-LLB7, MLB, STD, JUMBO)
ALTER TABLE dim_pool ADD COLUMN IF NOT EXISTS loan_balance_tier TEXT;

-- Loan program (VA, FHA, USDA, CONV)
ALTER TABLE dim_pool ADD COLUMN IF NOT EXISTS loan_program TEXT;

-- FICO bucket (FICO_LOW, FICO_SUBPRIME, FICO_FAIR, FICO_GOOD, FICO_EXCELLENT, FICO_SUPER)
ALTER TABLE dim_pool ADD COLUMN IF NOT EXISTS fico_bucket TEXT;

-- LTV bucket (LTV_LOW, LTV_MOD, LTV_STANDARD, LTV_HIGH, LTV_VERY_HIGH, LTV_EXTREME)
ALTER TABLE dim_pool ADD COLUMN IF NOT EXISTS ltv_bucket TEXT;

-- Occupancy type (OWNER, INVESTOR, SECOND_HOME)
ALTER TABLE dim_pool ADD COLUMN IF NOT EXISTS occupancy_type TEXT;

-- Loan purpose (PURCHASE, RATE_TERM_REFI, CASH_OUT_REFI)
ALTER TABLE dim_pool ADD COLUMN IF NOT EXISTS loan_purpose TEXT;

-- State prepay friction (HIGH_FRICTION, MODERATE_FRICTION, LOW_FRICTION)
ALTER TABLE dim_pool ADD COLUMN IF NOT EXISTS state_prepay_friction TEXT;

-- Seasoning stage (NEW_PRODUCTION, RAMPING, SEASONED, FULLY_SEASONED, WELL_SEASONED, BURNED_OUT)
ALTER TABLE dim_pool ADD COLUMN IF NOT EXISTS seasoning_stage TEXT;

-- Property type (SINGLE_FAMILY, CONDO, PUD, MULTI_FAMILY, MANUFACTURED)
ALTER TABLE dim_pool ADD COLUMN IF NOT EXISTS property_type TEXT;

-- Origination channel (RETAIL, CORRESPONDENT, BROKER, DIRECT)
ALTER TABLE dim_pool ADD COLUMN IF NOT EXISTS origination_channel TEXT;

-- Rate buydown flag (NO_BUYDOWN, BUYDOWN_2_1, BUYDOWN_3_2_1, PERMANENT_BUYDOWN)
ALTER TABLE dim_pool ADD COLUMN IF NOT EXISTS has_rate_buydown TEXT;

-- ============================================================================
-- SECTION 3: Add derived metrics columns (Layer 2)
-- ============================================================================

-- Refi incentive in basis points (WAC - current rate)
ALTER TABLE dim_pool ADD COLUMN IF NOT EXISTS refi_incentive_bps NUMERIC(8,2);

-- Premium CPR multiplier (prepay speed when ITM)
ALTER TABLE dim_pool ADD COLUMN IF NOT EXISTS premium_cpr_mult NUMERIC(5,3);

-- Discount CPR multiplier (prepay speed when OTM)
ALTER TABLE dim_pool ADD COLUMN IF NOT EXISTS discount_cpr_mult NUMERIC(5,3);

-- Convexity score (premium_mult / discount_mult - lower is better)
ALTER TABLE dim_pool ADD COLUMN IF NOT EXISTS convexity_score NUMERIC(5,3);

-- Contraction risk score (0-100, higher = more risk in bull market)
ALTER TABLE dim_pool ADD COLUMN IF NOT EXISTS contraction_risk_score NUMERIC(5,2);

-- Extension risk score (0-100, higher = more risk in bear market)
ALTER TABLE dim_pool ADD COLUMN IF NOT EXISTS extension_risk_score NUMERIC(5,2);

-- S-curve position (LEFT_TAIL, LEFT_SHOULDER, INFLECTION, RIGHT_SHOULDER, RIGHT_TAIL)
ALTER TABLE dim_pool ADD COLUMN IF NOT EXISTS s_curve_position TEXT;

-- ============================================================================
-- SECTION 4: Add composite score columns (Layer 4)
-- ============================================================================

-- Overall prepay protection score (0-100)
ALTER TABLE dim_pool ADD COLUMN IF NOT EXISTS composite_prepay_score NUMERIC(5,2);

-- Bull market scenario score (0-100)
ALTER TABLE dim_pool ADD COLUMN IF NOT EXISTS bull_scenario_score NUMERIC(5,2);

-- Bear market scenario score (0-100)
ALTER TABLE dim_pool ADD COLUMN IF NOT EXISTS bear_scenario_score NUMERIC(5,2);

-- Neutral market scenario score (0-100)
ALTER TABLE dim_pool ADD COLUMN IF NOT EXISTS neutral_scenario_score NUMERIC(5,2);

-- Payup efficiency score (protection per unit cost)
ALTER TABLE dim_pool ADD COLUMN IF NOT EXISTS payup_efficiency_score NUMERIC(5,2);

-- Tag generation timestamp
ALTER TABLE dim_pool ADD COLUMN IF NOT EXISTS tags_updated_at TIMESTAMPTZ;

-- ============================================================================
-- SECTION 5: Create indexes for common queries
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_pool_balance_tier ON dim_pool(loan_balance_tier);
CREATE INDEX IF NOT EXISTS idx_pool_loan_program ON dim_pool(loan_program);
CREATE INDEX IF NOT EXISTS idx_pool_fico_bucket ON dim_pool(fico_bucket);
CREATE INDEX IF NOT EXISTS idx_pool_state_friction ON dim_pool(state_prepay_friction);
CREATE INDEX IF NOT EXISTS idx_pool_servicer_risk ON dim_pool(servicer_prepay_risk);
CREATE INDEX IF NOT EXISTS idx_pool_composite_score ON dim_pool(composite_prepay_score);
CREATE INDEX IF NOT EXISTS idx_pool_convexity ON dim_pool(convexity_score);
CREATE INDEX IF NOT EXISTS idx_pool_scenario_scores ON dim_pool(bull_scenario_score, bear_scenario_score);
CREATE INDEX IF NOT EXISTS idx_pool_seasoning ON dim_pool(seasoning_stage);

-- ============================================================================
-- SECTION 6: Create factor multipliers lookup table
-- ============================================================================

CREATE TABLE IF NOT EXISTS factor_multipliers (
    factor_type TEXT NOT NULL,          -- 'loan_balance', 'loan_program', 'fico', 'ltv', etc.
    factor_value TEXT NOT NULL,         -- 'LLB1', 'VA', 'FICO_LOW', etc.
    premium_mult NUMERIC(5,3),          -- Multiplier when in-the-money
    baseline_mult NUMERIC(5,3),         -- Baseline multiplier
    discount_mult NUMERIC(5,3),         -- Multiplier when out-of-the-money
    typical_payup_32nds INTEGER,        -- Expected payup in 32nds
    confidence TEXT DEFAULT 'medium',   -- 'high', 'medium', 'low'
    data_source TEXT DEFAULT 'market_consensus',  -- 'empirical', 'market_consensus'
    last_updated DATE DEFAULT CURRENT_DATE,
    PRIMARY KEY (factor_type, factor_value)
);

-- ============================================================================
-- SECTION 7: Seed factor multipliers with initial values from design doc
-- ============================================================================

INSERT INTO factor_multipliers (factor_type, factor_value, premium_mult, baseline_mult, discount_mult, typical_payup_32nds, confidence)
VALUES
    -- Loan Balance Tiers
    ('loan_balance', 'LLB1', 0.65, 0.68, 1.20, 24, 'high'),
    ('loan_balance', 'LLB2', 0.70, 0.73, 1.15, 18, 'high'),
    ('loan_balance', 'LLB3', 0.75, 0.78, 1.12, 14, 'high'),
    ('loan_balance', 'LLB4', 0.80, 0.83, 1.10, 10, 'high'),
    ('loan_balance', 'LLB5', 0.84, 0.86, 1.08, 8, 'high'),
    ('loan_balance', 'LLB6', 0.87, 0.88, 1.05, 6, 'high'),
    ('loan_balance', 'LLB7', 0.90, 0.91, 1.03, 4, 'high'),
    ('loan_balance', 'MLB', 0.95, 0.95, 1.00, 2, 'high'),
    ('loan_balance', 'STD', 1.00, 1.00, 1.00, 0, 'high'),
    ('loan_balance', 'JUMBO', 1.10, 1.05, 0.80, -4, 'high'),
    -- Loan Programs
    ('loan_program', 'VA', 0.70, 0.72, 1.00, 12, 'high'),
    ('loan_program', 'FHA', 0.82, 0.85, 0.95, 6, 'high'),
    ('loan_program', 'USDA', 0.65, 0.68, 0.90, 15, 'high'),
    ('loan_program', 'CONV', 1.00, 1.00, 1.00, 0, 'high'),
    -- FICO Buckets
    ('fico', 'FICO_LOW', 0.75, 0.78, 1.15, 8, 'medium'),
    ('fico', 'FICO_SUBPRIME', 0.80, 0.82, 1.10, 6, 'medium'),
    ('fico', 'FICO_FAIR', 0.90, 0.92, 1.05, 3, 'medium'),
    ('fico', 'FICO_GOOD', 1.00, 1.00, 1.00, 0, 'medium'),
    ('fico', 'FICO_EXCELLENT', 1.08, 1.05, 0.90, -2, 'medium'),
    ('fico', 'FICO_SUPER', 1.12, 1.08, 0.85, -3, 'medium'),
    -- LTV Buckets
    ('ltv', 'LTV_LOW', 1.05, 1.02, 0.95, -1, 'medium'),
    ('ltv', 'LTV_MOD', 1.00, 1.00, 1.00, 0, 'medium'),
    ('ltv', 'LTV_STANDARD', 0.98, 0.98, 1.02, 1, 'medium'),
    ('ltv', 'LTV_HIGH', 0.90, 0.92, 1.05, 3, 'medium'),
    ('ltv', 'LTV_VERY_HIGH', 0.85, 0.88, 1.08, 5, 'medium'),
    ('ltv', 'LTV_EXTREME', 0.78, 0.82, 1.12, 7, 'medium'),
    -- Occupancy Types
    ('occupancy', 'OWNER', 1.00, 1.00, 1.00, 0, 'high'),
    ('occupancy', 'INVESTOR', 0.78, 0.80, 0.92, 4, 'high'),
    ('occupancy', 'SECOND_HOME', 0.88, 0.90, 0.95, 2, 'high'),
    -- Loan Purpose
    ('purpose', 'PURCHASE', 0.88, 0.90, 1.02, 2, 'medium'),
    ('purpose', 'RATE_TERM_REFI', 1.00, 1.00, 1.00, 0, 'medium'),
    ('purpose', 'CASH_OUT_REFI', 1.05, 1.02, 1.15, -2, 'medium'),
    -- State Friction
    ('state_friction', 'HIGH_FRICTION', 0.85, 0.87, 0.95, 4, 'high'),
    ('state_friction', 'MODERATE_FRICTION', 1.00, 1.00, 1.00, 0, 'high'),
    ('state_friction', 'LOW_FRICTION', 1.10, 1.05, 1.05, -2, 'high'),
    -- Servicer Risk
    ('servicer', 'PREPAY_PROTECTED', 0.85, 0.87, 0.90, 4, 'high'),
    ('servicer', 'NEUTRAL', 1.00, 1.00, 1.00, 0, 'high'),
    ('servicer', 'PREPAY_EXPOSED', 1.15, 1.12, 0.70, -4, 'high'),
    -- Seasoning Stages
    ('seasoning', 'NEW_PRODUCTION', 0.72, 0.75, 0.85, 0, 'high'),
    ('seasoning', 'RAMPING', 0.85, 0.88, 0.92, 0, 'high'),
    ('seasoning', 'SEASONED', 0.95, 0.96, 0.98, 0, 'high'),
    ('seasoning', 'FULLY_SEASONED', 1.00, 1.00, 1.00, 0, 'high'),
    ('seasoning', 'WELL_SEASONED', 0.85, 0.87, 1.05, 0, 'high'),
    ('seasoning', 'BURNED_OUT', 0.75, 0.78, 1.10, 0, 'high')
ON CONFLICT (factor_type, factor_value) DO UPDATE SET
    premium_mult = EXCLUDED.premium_mult,
    baseline_mult = EXCLUDED.baseline_mult,
    discount_mult = EXCLUDED.discount_mult,
    typical_payup_32nds = EXCLUDED.typical_payup_32nds,
    confidence = EXCLUDED.confidence,
    last_updated = CURRENT_DATE;

-- ============================================================================
-- SECTION 8: Create servicer prepay metrics tables (for dynamic scoring)
-- ============================================================================

CREATE TABLE IF NOT EXISTS servicer_prepay_metrics (
    servicer_id TEXT NOT NULL,
    servicer_name TEXT NOT NULL,
    as_of_month DATE NOT NULL,
    pool_count INTEGER,
    total_upb NUMERIC(18,2),
    avg_cpr NUMERIC(6,3),
    median_cpr NUMERIC(6,3),
    universe_avg_cpr NUMERIC(6,3),
    cpr_vs_universe NUMERIC(6,3),
    cpr_ratio NUMERIC(6,4),
    avg_wac NUMERIC(5,3),
    avg_wala INTEGER,
    avg_fico INTEGER,
    avg_ltv NUMERIC(5,2),
    avg_incentive NUMERIC(6,2),
    PRIMARY KEY (servicer_id, as_of_month),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS servicer_prepay_scores (
    servicer_id TEXT PRIMARY KEY,
    servicer_name TEXT NOT NULL,
    prepay_speed_score NUMERIC(5,2),
    prepay_speed_percentile INTEGER,
    cpr_3mo_avg NUMERIC(6,3),
    cpr_6mo_avg NUMERIC(6,3),
    cpr_12mo_avg NUMERIC(6,3),
    trend_3mo_vs_12mo NUMERIC(6,3),
    trend_direction TEXT,
    lifetime_avg_cpr NUMERIC(6,3),
    prepay_risk_category TEXT,
    pool_count INTEGER,
    last_updated TIMESTAMPTZ,
    months_of_data INTEGER
);

-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================
