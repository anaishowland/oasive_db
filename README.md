# Oasive Data Ingestion Platform

AI-powered fixed-income analytics platform ingesting economic data (FRED) and MBS disclosure data (Freddie Mac).

## Quick Start

```bash
cd /Users/anaishowland/oasive_db
source venv/bin/activate
pip install -r requirements.txt

# Run migrations
python scripts/run_migrations.py

# Test FRED locally
python -m src.ingestors.fred_ingestor

# Freddie Mac must run via Cloud Run (IP whitelisted)
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Cloud Run Jobs                           │
│  ┌─────────────────┐              ┌─────────────────┐          │
│  │ fred-ingestor   │              │ freddie-ingestor│          │
│  │ (daily 6:30 ET) │              │ (daily 11:45 ET)│          │
│  └────────┬────────┘              └────────┬────────┘          │
│           │                     VPC → NAT → 34.121.116.34      │
└───────────┼────────────────────────────────┼────────────────────┘
            ▼                                ▼
     FRED API                         CSS SFTP Server
            │                                │
            └────────────┬───────────────────┘
                         ▼
              ┌─────────────────────┐
              │   Cloud SQL         │
              │   (PostgreSQL)      │
              │                     │
              │ FRED: 106K+ rows    │
              │ Freddie: 45K files  │
              │ Pools: 2,333+       │
              └─────────────────────┘
```

## Project Structure

```
oasive_db/
├── src/
│   ├── config.py                 # Configuration
│   ├── db/connection.py          # Cloud SQL connector
│   ├── ingestors/
│   │   ├── fred_ingestor.py      # FRED API
│   │   └── freddie_ingestor.py   # Freddie SFTP
│   └── parsers/
│       └── freddie_parser.py     # Parse disclosure files
├── migrations/                    # SQL migrations (001-007)
├── scripts/                       # Utility scripts
├── docs/                          # Topic documentation
│   ├── ai_tagging_design.md      # AI tag rules & composite score
│   ├── database_schema.md        # Full DB documentation
│   ├── prepay_research_framework.md  # Empirical research plan
│   └── ...
├── HANDOFF.md                     # Agent context & status
└── requirements.txt
```

## Current Status

| Component | Status | Details |
|-----------|--------|---------|
| FRED Ingestion | ✅ Live | 34 series, 106K+ observations, daily scheduler |
| Freddie Download | 🔄 28.6% | 12,959 / 45,356 files downloaded |
| Freddie Parse | 🔄 Running | 2,333 pools loaded |
| AI Tagging | 📋 Designed | Composite score, servicer/state friction |
| Research Framework | 📋 Designed | 20 assumptions to validate |

## Key Commands

```bash
# Check download progress
python -c "from src.db.connection import get_engine; from sqlalchemy import text; e=get_engine(); print(e.connect().execute(text('SELECT COUNT(*) FROM freddie_file_catalog WHERE downloaded_at IS NOT NULL')).fetchone())"

# Execute Freddie download job
gcloud run jobs execute freddie-ingestor --region=us-central1 \
  --project=gen-lang-client-0343560978 \
  --args="-m,src.ingestors.freddie_ingestor,--mode,backfill,--max-files,2000"

# Run parser
python -m src.parsers.freddie_parser --file-type issuance

# View logs
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=freddie-ingestor" --limit=20
```

## GCP Resources

| Resource | Name |
|----------|------|
| Project | `gen-lang-client-0343560978` |
| Cloud SQL | `oasive-postgres` (us-central1) |
| Cloud Run Jobs | `fred-ingestor`, `freddie-ingestor` |
| Static IP | `34.121.116.34` (whitelisted) |
| GCS Bucket | `oasive-raw-data` |

## Documentation

| Doc | Purpose |
|-----|---------|
| [HANDOFF.md](HANDOFF.md) | Agent handoff with full context |
| [docs/database_schema.md](docs/database_schema.md) | Database tables & relationships |
| [docs/ai_tagging_design.md](docs/ai_tagging_design.md) | AI tag rules & composite score |
| [docs/prepay_research_framework.md](docs/prepay_research_framework.md) | Empirical validation plan |

## Environment Variables

```bash
# Required in .env
FRED_API_KEY=your_key
FREDDIE_USERNAME=svcfre-oasive
FREDDIE_PASSWORD=your_password
CLOUDSQL_CONNECTION_NAME=gen-lang-client-0343560978:us-central1:oasive-postgres
POSTGRES_DB=oasive
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your_password
GCP_PROJECT_ID=gen-lang-client-0343560978
GCS_RAW_BUCKET=oasive-raw-data
```
