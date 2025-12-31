## **v0 Ingestion Plan**

Storage (now, simple & fast)

- FRED data: Cloud SQL Postgres, small tables \+ *\[Optional: GCS bucket for raw JSON snapshots if you want full reproducibility and cheap cold storage.\]*  
- Fannie & Freddie loan level in BigQuery (FRED mirror aggregates into BigQuery or move there)

**FRED Series**

**IndicatorSeries**:

* Macro: unemployment, payrolls, CPI, PCE, GDP, etc.  
* Housing: housing starts, permits, completions  
* Home prices: FHFA HPI  
* Mortgage: MBA applications, Freddie PMMS primary rates  
* Policy: fed funds, SOFR, etc  
* Rates curve: Treasury yields and spreads

**Postgres Schema for FRED**

Table0: indicator or kg\_nodes (knowledge graph nodes as reference)

* 1 row per data point “US unemployment rate”, “MBA mortgage applications index”, “FHFA national HPI”  
* Columns: indicator\_id, name, domain, subcategory, description, etc.

Table1: fred\_series

→ mapping from indicator to FRED, tell ingestor what FRED series to fetch in API 

```sql
CREATE TABLE fred_series (
  id SERIAL PRIMARY KEY,
  series_id TEXT UNIQUE NOT NULL,       -- FRED series_id, eg "UNRATE"
  indicator_id TEXT UNIQUE NOT NULL,    -- map indicator to FRED
  name TEXT NOT NULL,                   -- human readable name
  domain TEXT NOT NULL,                 -- eg "macro", "housing", "mortgage"
  subcategory TEXT,                     -- eg "home_prices", "primary_rates"
  frequency TEXT,                       -- "daily", "weekly", "monthly", etc
  source TEXT,                          -- original source (BLS, Freddie, Treasury, etc)
  fred_url TEXT,                        -- optional convenience link
  is_active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);
```

Table2: fred\_observation

→ actual time series values, one row per value (biggest table for FRED)

```sql
CREATE TABLE fred_observation (
  series_id TEXT NOT NULL REFERENCES fred_series(series_id),
  obs_date DATE NOT NULL,
  value NUMERIC,                        -- FRED gives text, cast to numeric
  vintage_date DATE,                    -- if you care about vintage
  raw_payload JSONB,                    -- optional raw FRED response slice
  created_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (series_id, obs_date, COALESCE(vintage_date, DATE '0001-01-01'))
);
```

Table3: fred\_latest

→ view not a table, one row per series with most recent obs\_date for quick retrieval of latest snapshot 

```sql
CREATE VIEW fred_latest AS
SELECT DISTINCT ON (series_id)
  series_id,
  obs_date,
  value,
  created_at
FROM fred_observation
ORDER BY series_id, obs_date DESC;
```

*Match to knowledge graph*

- *1 row per FRED series in fred\_series using fields in KeyAtributes*  
- *Use same domain and subcategory tags*

**Cloud Run jobs**

- Service: fred\_ingestor  
- Scheduler:   
  - optionA: triggers fred-ingestor on a cron daily for all, \~ 6:30am ET  
  - *optionB: let the code decide when to run per series based on frequency value*  
- Secret manager: store FRED API key  
- Service account for Cloud Run service with cloudsql.client to reach postgres, and secretmanager.secretAccessor for read API key

**Ingest logic** (cloud run container via python)

1. Query postgres fred\_series  
2. For each series\_id, find latest obs\_date stored  
3. Call FRED API with observation\_start set to latest\_date \+ 1 to avoid repulling history  
4. Insert rows into fred\_observation via INSERT … ON CONFLICT DO NOTHING (for dupe)  
5. Optional: write full API response JSON into GCS as backup

```py
for series in fred_series:
    latest_date = get_latest_date_from_db(series.series_id)
    params = {
        "series_id": series.series_id,
        "api_key": API_KEY,
        "file_type": "json",
        "observation_start": (latest_date + datetime.timedelta(days=1)).isoformat()
    }
    resp = requests.get(FRED_SERIES_OBS_URL, params=params)
    data = resp.json()["observations"]
    rows = [
        (series.series_id, o["date"], o["value"], None, json.dumps(o))
        for o in data
    ]
    bulk_insert_into_fred_observation(rows)
```

**Optional GCS archive:**

- Bucket: oasive-fred-raw  
- Path: fred/raw/{series\_id}/date={YYYYMMDD}/response.json

```py
blob_path = f"fred/raw/{series_id}/date={today}/response.json"
bucket.blob(blob_path).upload_from_string(resp.text, content_type="application/json")
```

