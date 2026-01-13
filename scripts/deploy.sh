#!/bin/bash
# Deploy Oasive data ingestion jobs to Cloud Run
#
# Prerequisites:
# 1. gcloud CLI authenticated
# 2. Required APIs enabled (Cloud Run, Cloud SQL, Secret Manager)
# 3. Secrets created in Secret Manager
#
# Usage:
#   ./scripts/deploy.sh [fred|freddie|all]

set -e

# Configuration
PROJECT_ID="${GCP_PROJECT_ID:-gen-lang-client-0343560978}"
REGION="${GCP_REGION:-us-central1}"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT:-cloud-run-jobs-sa@${PROJECT_ID}.iam.gserviceaccount.com}"
IMAGE_REPO="gcr.io/${PROJECT_ID}/oasive-ingestor"
CLOUDSQL_CONNECTION="${CLOUDSQL_CONNECTION_NAME:-${PROJECT_ID}:${REGION}:oasive-postgres}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Build and push Docker image
build_image() {
    log_info "Building Docker image..."
    docker build -t ${IMAGE_REPO}:latest .
    
    log_info "Pushing to GCR..."
    docker push ${IMAGE_REPO}:latest
}

# Deploy FRED ingestion job
deploy_fred() {
    log_info "Deploying FRED ingestion job..."
    
    gcloud run jobs create fred-ingestor \
        --project=${PROJECT_ID} \
        --region=${REGION} \
        --image=${IMAGE_REPO}:latest \
        --service-account=${SERVICE_ACCOUNT} \
        --set-cloudsql-instances=${CLOUDSQL_CONNECTION} \
        --set-secrets="FRED_API_KEY=fred-api-key:latest,POSTGRES_PASSWORD=postgres-password:latest" \
        --set-env-vars="CLOUDSQL_CONNECTION_NAME=${CLOUDSQL_CONNECTION},POSTGRES_DB=postgres,POSTGRES_USER=postgres" \
        --memory=1Gi \
        --cpu=1 \
        --max-retries=1 \
        --task-timeout=1800s \
        --execute-now=false \
        2>/dev/null || \
    gcloud run jobs update fred-ingestor \
        --project=${PROJECT_ID} \
        --region=${REGION} \
        --image=${IMAGE_REPO}:latest \
        --set-secrets="FRED_API_KEY=fred-api-key:latest,POSTGRES_PASSWORD=postgres-password:latest" \
        --set-env-vars="CLOUDSQL_CONNECTION_NAME=${CLOUDSQL_CONNECTION},POSTGRES_DB=postgres,POSTGRES_USER=postgres"
    
    log_info "FRED job deployed"
    
    # Create Cloud Scheduler trigger (daily at 6:30 AM ET = 11:30 UTC)
    log_info "Setting up daily schedule..."
    gcloud scheduler jobs create http fred-ingestor-daily \
        --project=${PROJECT_ID} \
        --location=${REGION} \
        --schedule="30 11 * * *" \
        --time-zone="UTC" \
        --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/fred-ingestor:run" \
        --http-method=POST \
        --oauth-service-account-email=${SERVICE_ACCOUNT} \
        2>/dev/null || log_warn "Scheduler job may already exist"
}

# Deploy Freddie Mac ingestion job
deploy_freddie() {
    log_info "Deploying Freddie Mac ingestion job..."
    
    gcloud run jobs create freddie-ingestor \
        --project=${PROJECT_ID} \
        --region=${REGION} \
        --image=${IMAGE_REPO}:latest \
        --service-account=${SERVICE_ACCOUNT} \
        --set-cloudsql-instances=${CLOUDSQL_CONNECTION} \
        --set-secrets="FREDDIE_USERNAME=freddie-username:latest,FREDDIE_PASSWORD=freddie-password:latest,POSTGRES_PASSWORD=postgres-password:latest" \
        --set-env-vars="CLOUDSQL_CONNECTION_NAME=${CLOUDSQL_CONNECTION},POSTGRES_DB=postgres,POSTGRES_USER=postgres,GCS_RAW_BUCKET=oasive-raw-data" \
        --command="python" \
        --args="-m,src.ingestors.freddie_ingestor" \
        --memory=2Gi \
        --cpu=2 \
        --max-retries=1 \
        --task-timeout=3600s \
        --execute-now=false \
        2>/dev/null || \
    gcloud run jobs update freddie-ingestor \
        --project=${PROJECT_ID} \
        --region=${REGION} \
        --image=${IMAGE_REPO}:latest \
        --set-secrets="FREDDIE_USERNAME=freddie-username:latest,FREDDIE_PASSWORD=freddie-password:latest,POSTGRES_PASSWORD=postgres-password:latest" \
        --set-env-vars="CLOUDSQL_CONNECTION_NAME=${CLOUDSQL_CONNECTION},POSTGRES_DB=postgres,POSTGRES_USER=postgres,GCS_RAW_BUCKET=oasive-raw-data"
    
    log_info "Freddie job deployed"
    
    # Create Cloud Scheduler trigger (daily at 7:00 AM ET = 12:00 UTC)
    log_info "Setting up daily schedule..."
    gcloud scheduler jobs create http freddie-ingestor-daily \
        --project=${PROJECT_ID} \
        --location=${REGION} \
        --schedule="0 12 * * *" \
        --time-zone="UTC" \
        --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/freddie-ingestor:run" \
        --http-method=POST \
        --oauth-service-account-email=${SERVICE_ACCOUNT} \
        2>/dev/null || log_warn "Scheduler job may already exist"
}

# Main
case "${1:-all}" in
    fred)
        build_image
        deploy_fred
        ;;
    freddie)
        build_image
        deploy_freddie
        ;;
    all)
        build_image
        deploy_fred
        deploy_freddie
        ;;
    build)
        build_image
        ;;
    *)
        echo "Usage: $0 [fred|freddie|all|build]"
        exit 1
        ;;
esac

log_info "Deployment complete!"
