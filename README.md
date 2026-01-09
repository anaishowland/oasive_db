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

# Test FRED locally
PYTHONPATH=/Users/anaishowland/oasive_db python -m src.ingestors.fred_ingestor
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Cloud Run Jobs                           │
│  ┌─────────────────┐              ┌─────────────────┐          │
│  │ fred-ingestor   │              │ freddie-ingestor│          │
│  │ (daily 6:30 ET) │              │ (pending auth)  │          │
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
│  Tables:                                                        │
│  - fred_series (38 indicators)                                  │
│  - fred_observation (106K+ rows)                                │
│  - fred_ingest_log                                              │
│  - freddie_file_catalog                                         │
│  - freddie_ingest_log                                           │
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
│   └── 003_freddie_schema.sql
├── scripts/
│   ├── run_migrations.py      # Database migrations
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
| Service Account | cloud-run-jobs-sa | For Cloud Run jobs |

## Current Status

### ✅ FRED Ingestion (Working)
- 35/36 series successfully ingesting
- 106,000+ historical observations loaded
- Daily scheduler LIVE (6:30 AM ET)
- 1 series fails (NAPM - discontinued)

### ✅ Freddie Mac Ingestion (Working)
- Cloud Run job deployed with VPC connector
- SFTP authentication working (username: `svcfre-oasive`)
- 45,353 files available (76.73 GB)
- Batched download with retry logic implemented
- Historical backfill in progress

## Environment Variables

Required in `.env` for local development:
```bash
# FRED
FRED_API_KEY=your_key

# Freddie Mac
FREDDIE_USERNAME=svcFRE-OasiveInc
FREDDIE_PASSWORD=your_password

# Cloud SQL
CLOUDSQL_CONNECTION_NAME=gen-lang-client-0343560978:us-central1:oasive-postgres
POSTGRES_DB=oasive
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your_password

# GCP
GCP_PROJECT_ID=gen-lang-client-0343560978
GCS_RAW_BUCKET=oasive-raw-data
```

## Useful Commands

```bash
# Execute FRED job manually
gcloud run jobs execute fred-ingestor --region=us-central1

# Execute Freddie job manually
gcloud run jobs execute freddie-ingestor --region=us-central1

# View job logs
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=fred-ingestor" --limit=20

# Rebuild and deploy
cd /Users/anaishowland/oasive_db
gcloud builds submit --tag us-central1-docker.pkg.dev/gen-lang-client-0343560978/oasive-images/oasive-ingestor:latest --project=gen-lang-client-0343560978
gcloud run jobs update fred-ingestor --region=us-central1 --image=us-central1-docker.pkg.dev/gen-lang-client-0343560978/oasive-images/oasive-ingestor:latest
```

## Documentation

- [SETUP.md](SETUP.md) - Detailed setup instructions
- [docs/](docs/) - Reference materials and data specifications
