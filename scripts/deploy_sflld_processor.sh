#!/bin/bash
# Deploy SFLLD Cloud Run Job for processing historical data in the cloud
# This processes files from GCS, not local storage

set -e

PROJECT_ID="gen-lang-client-0343560978"
REGION="us-central1"
JOB_NAME="sflld-processor"
IMAGE="us-central1-docker.pkg.dev/$PROJECT_ID/oasive-images/freddie-ingestor:latest"

echo "=============================================="
echo "Deploying SFLLD Cloud Processor"
echo "=============================================="

# Create/Update the Cloud Run job
echo ""
echo "🚀 Creating Cloud Run job: $JOB_NAME"

gcloud run jobs create $JOB_NAME \
    --image=$IMAGE \
    --region=$REGION \
    --project=$PROJECT_ID \
    --memory=4Gi \
    --cpu=2 \
    --task-timeout=4h \
    --max-retries=1 \
    --set-env-vars="CLOUDSQL_CONNECTION_NAME=$PROJECT_ID:$REGION:oasive-postgres,GCP_PROJECT_ID=$PROJECT_ID,GCS_RAW_BUCKET=oasive-raw-data,POSTGRES_DB=oasive,POSTGRES_USER=postgres" \
    --set-secrets="POSTGRES_PASSWORD=postgres-password:latest" \
    --vpc-connector=data-feeds-vpc-1 \
    --vpc-egress=all-traffic \
    --set-cloudsql-instances=$PROJECT_ID:$REGION:oasive-postgres \
    --service-account=cloud-run-jobs-sa@$PROJECT_ID.iam.gserviceaccount.com \
    --args="-m,src.ingestors.sflld_ingestor,--process-gcs,gs://oasive-raw-data/sflld" \
    2>/dev/null || \
gcloud run jobs update $JOB_NAME \
    --image=$IMAGE \
    --region=$REGION \
    --project=$PROJECT_ID \
    --memory=4Gi \
    --cpu=2 \
    --task-timeout=4h \
    --args="-m,src.ingestors.sflld_ingestor,--process-gcs,gs://oasive-raw-data/sflld"

echo ""
echo "✅ Job created/updated: $JOB_NAME"
echo ""
echo "To run the processor:"
echo "  gcloud run jobs execute $JOB_NAME --region=$REGION --project=$PROJECT_ID"
echo ""
echo "To check logs:"
echo "  gcloud logging read 'resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"$JOB_NAME\"' --project=$PROJECT_ID --limit=50"
