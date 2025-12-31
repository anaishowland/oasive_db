-- Migration 003: Freddie Mac Disclosure Data Schema
-- Stores metadata about downloaded files and processed loan-level data

-- Table: freddie_file_catalog
-- Tracks all files discovered and downloaded from Freddie Mac SFTP
CREATE TABLE IF NOT EXISTS freddie_file_catalog (
    id SERIAL PRIMARY KEY,
    remote_path TEXT NOT NULL,                -- Full path on SFTP server
    filename TEXT NOT NULL,                   -- File name only
    file_type TEXT,                           -- e.g., "loan_level", "factor", "pool"
    file_date DATE,                           -- Date from filename if available
    remote_size BIGINT,                       -- File size on remote server (bytes)
    remote_modified_at TIMESTAMPTZ,           -- Last modified time on server
    local_gcs_path TEXT,                      -- GCS path after download
    download_status TEXT DEFAULT 'pending',   -- 'pending', 'downloaded', 'processed', 'error'
    downloaded_at TIMESTAMPTZ,
    processed_at TIMESTAMPTZ,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (remote_path)
);

CREATE INDEX IF NOT EXISTS idx_freddie_file_status ON freddie_file_catalog(download_status);
CREATE INDEX IF NOT EXISTS idx_freddie_file_type ON freddie_file_catalog(file_type);
CREATE INDEX IF NOT EXISTS idx_freddie_file_date ON freddie_file_catalog(file_date);

-- Table: freddie_ingest_log
-- Audit trail for SFTP sync runs
CREATE TABLE IF NOT EXISTS freddie_ingest_log (
    id SERIAL PRIMARY KEY,
    run_started_at TIMESTAMPTZ NOT NULL,
    run_completed_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'running',   -- 'running', 'success', 'error'
    files_discovered INTEGER DEFAULT 0,
    files_downloaded INTEGER DEFAULT 0,
    bytes_downloaded BIGINT DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Trigger to auto-update updated_at on freddie_file_catalog
DROP TRIGGER IF EXISTS update_freddie_file_catalog_updated_at ON freddie_file_catalog;
CREATE TRIGGER update_freddie_file_catalog_updated_at
    BEFORE UPDATE ON freddie_file_catalog
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Note: Actual loan-level data will go into BigQuery for scale
-- This Postgres schema is for file metadata and orchestration only
