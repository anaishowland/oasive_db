-- ============================================================================
-- Migration 010: Unified Research Views
-- ============================================================================
-- 
-- Creates views that span both current (SFTP 2019+) and historical (SFLLD 1999+)
-- data for cross-era prepay research and analysis.
-- ============================================================================

-- =============================================================================
-- View 1: Unified Loan View (All loans across both sources)
-- =============================================================================
CREATE OR REPLACE VIEW v_all_loans AS
SELECT 
    loan_id::VARCHAR(20) AS loan_id,
    pool_id,
    fico AS credit_score,
    ltv,
    dti,
    loan_amount AS orig_upb,
    note_rate AS orig_rate,
    loan_term,
    loan_purpose,
    occupancy_type AS occupancy,
    property_type,
    state,
    servicer,
    first_payment_date,
    'sftp_2019' AS source,
    EXTRACT(YEAR FROM first_payment_date)::INTEGER AS vintage_year,
    created_at
FROM dim_loan

UNION ALL

SELECT 
    loan_sequence AS loan_id,
    NULL AS pool_id,  -- No pool association in historical data
    credit_score,
    ltv,
    dti,
    orig_upb,
    orig_rate,
    loan_term,
    loan_purpose,
    occupancy,
    property_type,
    state,
    servicer_name AS servicer,
    first_payment_date,
    'sflld_historical' AS source,
    vintage_year,
    created_at
FROM dim_loan_historical;

COMMENT ON VIEW v_all_loans IS 'Unified view of all loans from SFTP (2019+) and SFLLD historical (1999+) sources';

-- =============================================================================
-- View 2: Historical Prepay Events (Loans that prepaid)
-- =============================================================================
CREATE OR REPLACE VIEW v_prepay_events AS
SELECT 
    h.loan_sequence AS loan_id,
    h.credit_score,
    h.ltv,
    h.orig_rate,
    h.loan_purpose,
    h.occupancy,
    h.state,
    h.servicer_name AS servicer,
    h.vintage_year,
    h.first_payment_date,
    f.zero_balance_date AS prepay_date,
    f.loan_age AS months_to_prepay,
    f.zero_balance_code,
    CASE f.zero_balance_code
        WHEN '01' THEN 'Prepaid/Matured'
        WHEN '02' THEN 'Third Party Sale'
        WHEN '03' THEN 'Short Sale'
        WHEN '06' THEN 'Repurchased'
        WHEN '09' THEN 'REO'
        WHEN '15' THEN 'Note Sale'
        ELSE 'Other'
    END AS termination_reason,
    'sflld_historical' AS source
FROM dim_loan_historical h
JOIN fact_loan_month_historical f 
    ON h.loan_sequence = f.loan_sequence
WHERE f.zero_balance_code IS NOT NULL
  AND f.zero_balance_date IS NOT NULL;

COMMENT ON VIEW v_prepay_events IS 'All loan termination events from historical data with prepay reason';

-- =============================================================================
-- View 3: Prepay Speed by Vintage/Servicer/State (Research aggregates)
-- =============================================================================
CREATE OR REPLACE VIEW v_prepay_by_segment AS
SELECT 
    vintage_year,
    servicer_name AS servicer,
    state,
    loan_purpose,
    COUNT(*) AS total_loans,
    SUM(CASE WHEN f.zero_balance_code = '01' THEN 1 ELSE 0 END) AS prepaid_count,
    AVG(CASE WHEN f.zero_balance_code = '01' THEN f.loan_age END) AS avg_months_to_prepay,
    ROUND(100.0 * SUM(CASE WHEN f.zero_balance_code = '01' THEN 1 ELSE 0 END) / COUNT(*), 2) AS prepay_rate_pct
FROM dim_loan_historical h
LEFT JOIN (
    SELECT DISTINCT ON (loan_sequence) 
        loan_sequence, zero_balance_code, loan_age
    FROM fact_loan_month_historical
    WHERE zero_balance_code IS NOT NULL
    ORDER BY loan_sequence, report_date DESC
) f ON h.loan_sequence = f.loan_sequence
GROUP BY vintage_year, servicer_name, state, loan_purpose;

COMMENT ON VIEW v_prepay_by_segment IS 'Aggregated prepay statistics by vintage, servicer, state, and loan purpose';

-- =============================================================================
-- View 4: Servicer Prepay Comparison (for calibrating servicer scores)
-- =============================================================================
CREATE OR REPLACE VIEW v_servicer_prepay_comparison AS
SELECT 
    servicer_name AS servicer,
    COUNT(*) AS total_loans,
    SUM(CASE WHEN f.zero_balance_code = '01' THEN 1 ELSE 0 END) AS prepaid_count,
    ROUND(100.0 * SUM(CASE WHEN f.zero_balance_code = '01' THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 2) AS prepay_rate_pct,
    AVG(CASE WHEN f.zero_balance_code = '01' THEN f.loan_age END)::INTEGER AS avg_months_to_prepay,
    AVG(h.credit_score)::INTEGER AS avg_fico,
    AVG(h.ltv)::DECIMAL(5,1) AS avg_ltv,
    AVG(h.orig_rate)::DECIMAL(5,3) AS avg_rate
FROM dim_loan_historical h
LEFT JOIN (
    SELECT DISTINCT ON (loan_sequence) 
        loan_sequence, zero_balance_code, loan_age
    FROM fact_loan_month_historical
    WHERE zero_balance_code IS NOT NULL
    ORDER BY loan_sequence, report_date DESC
) f ON h.loan_sequence = f.loan_sequence
GROUP BY servicer_name
HAVING COUNT(*) > 10000  -- Only servicers with significant volume
ORDER BY prepay_rate_pct ASC;  -- Slowest prepayers first (most prepay-protected)

COMMENT ON VIEW v_servicer_prepay_comparison IS 'Servicer-level prepay comparison for calibrating prepay risk scores';

-- =============================================================================
-- View 5: State Prepay Comparison (for calibrating state friction scores)
-- =============================================================================
CREATE OR REPLACE VIEW v_state_prepay_comparison AS
SELECT 
    state,
    COUNT(*) AS total_loans,
    SUM(CASE WHEN f.zero_balance_code = '01' THEN 1 ELSE 0 END) AS prepaid_count,
    ROUND(100.0 * SUM(CASE WHEN f.zero_balance_code = '01' THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 2) AS prepay_rate_pct,
    AVG(CASE WHEN f.zero_balance_code = '01' THEN f.loan_age END)::INTEGER AS avg_months_to_prepay,
    AVG(h.credit_score)::INTEGER AS avg_fico,
    AVG(h.orig_rate)::DECIMAL(5,3) AS avg_rate
FROM dim_loan_historical h
LEFT JOIN (
    SELECT DISTINCT ON (loan_sequence) 
        loan_sequence, zero_balance_code, loan_age
    FROM fact_loan_month_historical
    WHERE zero_balance_code IS NOT NULL
    ORDER BY loan_sequence, report_date DESC
) f ON h.loan_sequence = f.loan_sequence
WHERE h.state IS NOT NULL
GROUP BY state
ORDER BY prepay_rate_pct ASC;  -- Slowest prepayers first (high friction)

COMMENT ON VIEW v_state_prepay_comparison IS 'State-level prepay comparison for calibrating state friction scores';

-- =============================================================================
-- View 6: Rate Environment Analysis (prepay by rate regime)
-- =============================================================================
CREATE OR REPLACE VIEW v_prepay_by_rate_environment AS
SELECT 
    vintage_year,
    CASE 
        WHEN AVG(orig_rate) < 4.0 THEN 'ultra_low (<4%)'
        WHEN AVG(orig_rate) < 5.0 THEN 'low (4-5%)'
        WHEN AVG(orig_rate) < 6.5 THEN 'moderate (5-6.5%)'
        WHEN AVG(orig_rate) < 8.0 THEN 'elevated (6.5-8%)'
        ELSE 'high (>8%)'
    END AS rate_environment,
    AVG(orig_rate)::DECIMAL(5,3) AS avg_orig_rate,
    COUNT(*) AS total_loans,
    SUM(CASE WHEN f.zero_balance_code = '01' THEN 1 ELSE 0 END) AS prepaid_count,
    ROUND(100.0 * SUM(CASE WHEN f.zero_balance_code = '01' THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 2) AS prepay_rate_pct,
    AVG(CASE WHEN f.zero_balance_code = '01' THEN f.loan_age END)::INTEGER AS avg_months_to_prepay
FROM dim_loan_historical h
LEFT JOIN (
    SELECT DISTINCT ON (loan_sequence) 
        loan_sequence, zero_balance_code, loan_age
    FROM fact_loan_month_historical
    WHERE zero_balance_code IS NOT NULL
    ORDER BY loan_sequence, report_date DESC
) f ON h.loan_sequence = f.loan_sequence
GROUP BY vintage_year
ORDER BY vintage_year;

COMMENT ON VIEW v_prepay_by_rate_environment IS 'Prepay analysis by vintage year and rate environment for cycle research';
