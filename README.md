# Oasive Data Ingestion Platform

Automated data pipelines for fixed-income analytics, ingesting economic indicators from FRED and MBS disclosure data from Freddie Mac.

## Quick Start

```bash
# Setup
cd /Users/anaishowland/oasive_db
source venv/bin/activate
pip install -r requirements.txt

# Run migrations
PYTHONPATH=/Users/anaishowland/oasive_db python scripts/run_migrations.py

# Test FRED locally (Freddie must run via Cloud Run - IP whitelisted)
PYTHONPATH=/Users/anaishowland/oasive_db python -m src.ingestors.fred_ingestor
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Cloud Run Jobs                           │
│  ┌─────────────────┐              ┌─────────────────┐          │
│  │ fred-ingestor   │              │ freddie-ingestor│          │
│  │ (daily 6:30 ET) │              │ (auth working)  │          │
│  └────────┬────────┘              └────────┬────────┘          │
│           │                                │                    │
│           │                     ┌──────────┴──────────┐        │
│           │                     │ VPC Connector       │        │
│           │                     │ → Cloud NAT         │        │
│           │                     │ → IP: 34.121.116.34 │        │
│           │                     └──────────┬──────────┘        │
└───────────┼────────────────────────────────┼────────────────────┘
            │                                │
            ▼                                ▼
┌─────────────────────┐          ┌─────────────────────┐
│    FRED API         │          │  CSS SFTP Server    │
│ (api.stlouisfed.org)│          │(data.mbs-securities │
└─────────────────────┘          │        .com)        │
                                 └─────────────────────┘
            │                                │
            ▼                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Cloud SQL (Postgres)                        │
│                   Instance: oasive-postgres                     │
│                   Database: oasive                              │
│                                                                 │
│  FRED Tables:           Freddie Tables:                         │
│  - fred_series          - freddie_file_catalog                  │
│  - fred_observation     - dim_pool, dim_loan                    │
│  - fred_ingest_log      - fact_pool_month, fact_loan_month      │
│                         - freddie_security_issuance             │
└─────────────────────────────────────────────────────────────────┘
```

## Project Structure

```
oasive_db/
├── src/
│   ├── config.py              # Configuration from env vars
│   ├── db/
│   │   └── connection.py      # Cloud SQL connector
│   └── ingestors/
│       ├── fred_ingestor.py   # FRED API ingestion
│       └── freddie_ingestor.py # Freddie Mac SFTP ingestion
├── migrations/
│   ├── 001_fred_schema.sql
│   ├── 002_seed_fred_series.sql
│   ├── 003_freddie_schema.sql
│   └── 004_freddie_data_schema.sql  # Pool/loan dimension & fact tables
├── scripts/
│   ├── run_migrations.py      # Database migrations
│   ├── analyze_sftp.py        # SFTP inventory analysis
│   ├── deploy.sh              # Cloud Run deployment
│   └── setup_secrets.sh       # Secret Manager setup
├── docs/                      # Reference documentation
├── Dockerfile
├── requirements.txt
└── .env                       # Local secrets (not in git)
```

## GCP Resources

| Resource | Name | Details |
|----------|------|---------|
| Project | gen-lang-client-0343560978 | |
| Cloud SQL | oasive-postgres | us-central1, Postgres |
| Database | oasive | Contains all tables |
| Cloud Run Job | fred-ingestor | Daily FRED sync |
| Cloud Run Job | freddie-ingestor | Freddie Mac SFTP |
| Cloud Scheduler | fred-ingestor-daily | 30 11 * * * UTC |
| VPC Connector | data-feeds-vpc-1 | For NAT routing |
| Cloud NAT | data-feeds-nat-1 | Static IP: 34.121.116.34 |
| Artifact Registry | oasive-images | Docker images |
| GCS Bucket | oasive-raw-data | Raw file storage |
| Service Account | cloud-run-jobs-sa | For Cloud Run jobs |

## Current Status

### ✅ FRED Ingestion (Live)
- 34/38 series successfully ingesting
- 106,000+ historical observations loaded
- Daily scheduler LIVE (6:30 AM ET)
- 2 series inactive (NAPM, MORTGAGE5US - discontinued)

### ✅ Freddie Mac Ingestion (Downloading)
- SFTP authentication working (username: `svcfre-oasive`)
- Cloud Run job deployed with VPC connector
- 45,353 files available (76.73 GB)
- Batched download with retry logic implemented
- Historical backfill in progress via parallel Cloud Run jobs
- Database schema created (dim_pool, dim_loan, fact tables)

### 🔜 Pending
- Set up Cloud Scheduler for recurring Freddie downloads
- Parse downloaded files to populate dimension/fact tables
- Implement AI tag generation for pools

## Environment Variables

Required in `.env` for local development:
```bash
# FRED
FRED_API_KEY=your_key

# Freddie Mac (CANNOT test locally - IP whitelisted)
FREDDIE_USERNAME=svcfre-oasive
FREDDIE_PASSWORD=your_password

# Cloud SQL
CLOUDSQL_CONNECTION_NAME=gen-lang-client-0343560978:us-central1:oasive-postgres
POSTGRES_DB=oasive
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your_password

# GCP
GCP_PROJECT_ID=gen-lang-client-0343560978
GCS_RAW_BUCKET=oasive-raw-data
GCP_PUBLIC_IP=34.121.116.34
```

## Useful Commands

```bash
# Execute FRED job manually
gcloud run jobs execute fred-ingestor --region=us-central1

# Execute Freddie job (backfill mode)
gcloud run jobs execute freddie-ingestor --region=us-central1 \
  --args="-m,src.ingestors.freddie_ingestor,--mode,backfill,--max-files,1000"

# View job logs
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=freddie-ingestor" --limit=20

# Check download progress
psql -h 127.0.0.1 -U postgres -d oasive -c \
  "SELECT download_status, COUNT(*) FROM freddie_file_catalog GROUP BY download_status"

# Rebuild and deploy
gcloud builds submit --tag us-central1-docker.pkg.dev/gen-lang-client-0343560978/oasive-images/oasive-ingestor:latest
gcloud run jobs update freddie-ingestor --region=us-central1 --image=us-central1-docker.pkg.dev/gen-lang-client-0343560978/oasive-images/oasive-ingestor:latest
```

## Documentation

- [SETUP.md](SETUP.md) - Detailed setup instructions
- [HANDOFF.md](HANDOFF.md) - Agent handoff document with full context
- [docs/database_schema.md](docs/database_schema.md) - Database schema documentation
- [docs/oasive_company_business_plan.md](docs/oasive_company_business_plan.md) - Product vision
- [docs/](docs/) - Reference materials and data specifications
