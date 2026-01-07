# Oasive Database Schema

## Overview

| Database | Instance | Location |
|----------|----------|----------|
| PostgreSQL | `oasive-postgres` | `us-central1` |
| Database Name | `oasive` | |
| Connection | `gen-lang-client-0343560978:us-central1:oasive-postgres` |

---

## FRED Data Tables

### `fred_series` — Series Metadata

Maps indicators to FRED API series. One row per data series.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `id` | SERIAL | NO | Auto-increment PK |
| `series_id` | TEXT | NO | FRED series ID (e.g., "UNRATE") — **UNIQUE** |
| `indicator_id` | TEXT | NO | Internal indicator name — **UNIQUE** |
| `name` | TEXT | NO | Human-readable name |
| `description` | TEXT | YES | Full description |
| `domain` | TEXT | NO | Category: macro, housing, mortgage, policy, rates_curve |
| `subcategory` | TEXT | YES | Sub-category within domain |
| `frequency` | TEXT | YES | daily, weekly, monthly, quarterly |
| `source` | TEXT | YES | Original data source (BLS, Treasury, etc.) |
| `fred_url` | TEXT | YES | Link to FRED page |
| `is_active` | BOOLEAN | YES | Whether to fetch this series (default: true) |
| `created_at` | TIMESTAMPTZ | YES | Row creation time |
| `updated_at` | TIMESTAMPTZ | YES | Last update time |

**Indexes**: Primary key on `id`, unique on `series_id`, unique on `indicator_id`

---

### `fred_observation` — Time Series Data

Stores actual values. One row per observation date per series. **Largest table.**

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `series_id` | TEXT | NO | FK → `fred_series.series_id` |
| `obs_date` | DATE | NO | Observation date |
| `value` | NUMERIC | YES | Data value (NULL if FRED returns ".") |
| `vintage_date` | DATE | NO | Revision date (default: 0001-01-01) |
| `raw_payload` | JSONB | YES | Original FRED API response |
| `created_at` | TIMESTAMPTZ | YES | Row creation time |

**Primary Key**: `(series_id, obs_date, vintage_date)`

**Current Stats**: ~106,000 observations, dating back to 1919

---

### `fred_ingest_log` — Ingestion Audit Log

Tracks each ingestion run for monitoring and debugging.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `id` | SERIAL | NO | Auto-increment PK |
| `series_id` | TEXT | YES | Which series was processed |
| `run_started_at` | TIMESTAMPTZ | NO | Job start time |
| `run_completed_at` | TIMESTAMPTZ | YES | Job end time |
| `status` | TEXT | NO | running, success, error |
| `rows_inserted` | INTEGER | YES | Count of new rows added |
| `error_message` | TEXT | YES | Error details if failed |
| `created_at` | TIMESTAMPTZ | YES | Row creation time |

---

## FRED Views

### `fred_latest` — Most Recent Values

Returns the latest observation for each series. Useful for dashboards.

```sql
SELECT DISTINCT ON (series_id)
    series_id,
    obs_date,
    value,
    created_at
FROM fred_observation
ORDER BY series_id, obs_date DESC;
```

### `fred_series_status` — Series Health Check

Shows each series with its latest date and value.

### `fred_series_catalog` — Full Catalog (matches FRED_data.csv)

Complete series metadata with coverage statistics. **This view matches the `FRED_data.csv` format exactly.**

| Column | Description |
|--------|-------------|
| `fred_id` | FRED series ID |
| `indicator_id` | Internal indicator name |
| `name` | Human-readable name |
| `description` | Full description |
| `domain` | Category |
| `subcategory` | Sub-category |
| `frequency` | Update frequency |
| `source` | Data source |
| `url` | FRED page URL |
| `is_active` | Whether actively fetched |
| `data_starts` | First observation date |
| `data_ends` | Latest observation date |
| `observation_count` | Total rows |

```sql
SELECT * FROM fred_series_catalog;
```

---

## Freddie Mac Tables

### `freddie_file_catalog` — SFTP File Inventory

Tracks files discovered and downloaded from CSS SFTP server.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `id` | SERIAL | NO | Auto-increment PK |
| `remote_path` | TEXT | NO | Full path on SFTP server |
| `filename` | TEXT | NO | File name |
| `file_type` | TEXT | YES | loan_level, pool, factor, disclosure |
| `file_date` | DATE | YES | Date from filename |
| `remote_size` | BIGINT | YES | File size in bytes |
| `remote_modified_at` | TIMESTAMPTZ | YES | Last modified on server |
| `local_gcs_path` | TEXT | YES | GCS location after download |
| `download_status` | TEXT | YES | pending, downloaded, processed, error |
| `downloaded_at` | TIMESTAMPTZ | YES | When downloaded |
| `processed_at` | TIMESTAMPTZ | YES | When parsed/loaded |
| `error_message` | TEXT | YES | Error details if failed |
| `created_at` | TIMESTAMPTZ | YES | Row creation time |
| `updated_at` | TIMESTAMPTZ | YES | Last update time |

---

### `freddie_ingest_log` — SFTP Run Log

Tracks each SFTP sync run.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `id` | SERIAL | NO | Auto-increment PK |
| `run_started_at` | TIMESTAMPTZ | NO | Job start time |
| `run_completed_at` | TIMESTAMPTZ | YES | Job end time |
| `status` | TEXT | NO | running, success, error |
| `files_discovered` | INTEGER | YES | Files found on server |
| `files_downloaded` | INTEGER | YES | Files successfully downloaded |
| `bytes_downloaded` | BIGINT | YES | Total bytes transferred |
| `error_message` | TEXT | YES | Error details if failed |
| `created_at` | TIMESTAMPTZ | YES | Row creation time |

---

## Entity Relationship Diagram

```
┌─────────────────┐       ┌─────────────────────┐
│   fred_series   │       │  fred_observation   │
├─────────────────┤       ├─────────────────────┤
│ series_id (PK)  │◄──────│ series_id (FK)      │
│ indicator_id    │       │ obs_date            │
│ name            │       │ value               │
│ domain          │       │ vintage_date        │
│ frequency       │       │ raw_payload         │
│ is_active       │       └─────────────────────┘
└─────────────────┘
        │
        │ (logged by)
        ▼
┌─────────────────┐
│ fred_ingest_log │
├─────────────────┤
│ series_id       │
│ status          │
│ rows_inserted   │
└─────────────────┘

┌─────────────────────┐       ┌─────────────────────┐
│freddie_file_catalog │       │  freddie_ingest_log │
├─────────────────────┤       ├─────────────────────┤
│ remote_path         │       │ files_discovered    │
│ download_status     │       │ files_downloaded    │
│ local_gcs_path      │       │ status              │
└─────────────────────┘       └─────────────────────┘
```

---

## Quick Reference Queries

```sql
-- Latest value for each series
SELECT * FROM fred_latest;

-- Series health check
SELECT * FROM fred_series_status;

-- Get all 10Y Treasury yields
SELECT obs_date, value 
FROM fred_observation 
WHERE series_id = 'DGS10' 
ORDER BY obs_date DESC 
LIMIT 30;

-- Count observations by domain
SELECT s.domain, COUNT(o.*) 
FROM fred_series s 
JOIN fred_observation o ON s.series_id = o.series_id 
GROUP BY s.domain;

-- Find gaps in daily series
SELECT series_id, obs_date, 
       obs_date - LAG(obs_date) OVER (ORDER BY obs_date) as gap
FROM fred_observation 
WHERE series_id = 'DGS10' AND obs_date >= '2025-01-01'
ORDER BY obs_date;
```

---

## Migrations

| File | Description |
|------|-------------|
| `001_fred_schema.sql` | Creates fred_series, fred_observation, fred_ingest_log, views |
| `002_seed_fred_series.sql` | Inserts initial 38 series definitions |
| `003_freddie_schema.sql` | Creates freddie_file_catalog, freddie_ingest_log |

Run migrations:
```bash
cd /Users/anaishowland/oasive_db
source venv/bin/activate
PYTHONPATH=/Users/anaishowland/oasive_db python scripts/run_migrations.py
```
