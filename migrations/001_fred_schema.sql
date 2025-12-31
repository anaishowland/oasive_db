-- Migration 001: FRED Data Schema
-- Creates tables for storing FRED economic indicator data

-- Table 1: fred_series (metadata about each series)
-- Maps FRED series to your internal indicator taxonomy
CREATE TABLE IF NOT EXISTS fred_series (
    id SERIAL PRIMARY KEY,
    series_id TEXT UNIQUE NOT NULL,           -- FRED series_id, e.g., "UNRATE"
    indicator_id TEXT UNIQUE NOT NULL,        -- Internal indicator ID
    name TEXT NOT NULL,                       -- Human-readable name
    description TEXT,                         -- Full description
    domain TEXT NOT NULL,                     -- e.g., "macro", "housing", "mortgage"
    subcategory TEXT,                         -- e.g., "home_prices", "primary_rates"
    frequency TEXT,                           -- "daily", "weekly", "monthly", "quarterly"
    source TEXT,                              -- Original source (BLS, Freddie, Treasury, etc)
    fred_url TEXT,                            -- Convenience link to FRED page
    is_active BOOLEAN DEFAULT TRUE,           -- Whether to fetch this series
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for common queries
CREATE INDEX IF NOT EXISTS idx_fred_series_domain ON fred_series(domain);
CREATE INDEX IF NOT EXISTS idx_fred_series_active ON fred_series(is_active);

-- Table 2: fred_observation (time series values)
-- One row per observation per series
CREATE TABLE IF NOT EXISTS fred_observation (
    series_id TEXT NOT NULL REFERENCES fred_series(series_id) ON DELETE CASCADE,
    obs_date DATE NOT NULL,
    value NUMERIC,
    vintage_date DATE DEFAULT '0001-01-01',
    raw_payload JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (series_id, obs_date, vintage_date)
);

-- Index for time-based queries
CREATE INDEX IF NOT EXISTS idx_fred_observation_date ON fred_observation(obs_date DESC);
CREATE INDEX IF NOT EXISTS idx_fred_observation_series_date ON fred_observation(series_id, obs_date DESC);

-- Table 3: fred_ingest_log (audit trail for ingestion runs)
CREATE TABLE IF NOT EXISTS fred_ingest_log (
    id SERIAL PRIMARY KEY,
    series_id TEXT REFERENCES fred_series(series_id),
    run_started_at TIMESTAMPTZ NOT NULL,
    run_completed_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'running',   -- 'running', 'success', 'error'
    rows_inserted INTEGER DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fred_ingest_log_series ON fred_ingest_log(series_id);
CREATE INDEX IF NOT EXISTS idx_fred_ingest_log_status ON fred_ingest_log(status);

-- View: fred_latest (most recent value for each series)
CREATE OR REPLACE VIEW fred_latest AS
SELECT DISTINCT ON (series_id)
    series_id,
    obs_date,
    value,
    created_at
FROM fred_observation
ORDER BY series_id, obs_date DESC;

-- View: fred_series_status (series with latest observation info)
CREATE OR REPLACE VIEW fred_series_status AS
SELECT 
    s.series_id,
    s.name,
    s.domain,
    s.frequency,
    s.is_active,
    l.obs_date AS latest_obs_date,
    l.value AS latest_value,
    s.updated_at
FROM fred_series s
LEFT JOIN fred_latest l ON s.series_id = l.series_id;

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Trigger to auto-update updated_at on fred_series
DROP TRIGGER IF EXISTS update_fred_series_updated_at ON fred_series;
CREATE TRIGGER update_fred_series_updated_at
    BEFORE UPDATE ON fred_series
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
