#!/bin/bash
# =============================================================================
# SFLLD Cloud Migration Script
# Run this script to upload historical data to GCS and clean up local storage
# =============================================================================

set -e

echo "=============================================="
echo "SFLLD Cloud Migration"
echo "=============================================="

# Step 0: Commit code changes
echo ""
echo "📝 Step 0: Committing code changes..."
cd /Users/anaishowland/oasive_db
git add -A
git commit -m "Add GCS processing support for SFLLD historical data

- Added GCSSFLLDProcessor class for cloud-based processing
- Added --process-gcs flag to sflld_ingestor
- Created upload_sflld_to_gcs.sh script
- Created deploy_sflld_processor.sh script
- Supports processing TXT files and quarterly ZIPs from GCS"
git push origin main
echo "✅ Code committed and pushed"

# Step 1: Upload to GCS
echo ""
echo "📤 Step 1: Uploading extracted files to GCS..."
gsutil -m cp -r ~/Downloads/sflld/extracted gs://oasive-raw-data/sflld/
echo "✅ Extracted files uploaded"

echo ""
echo "📤 Step 2: Uploading yearly ZIPs to GCS..."
mkdir -p /tmp/sflld_yearly
gsutil mb -p gen-lang-client-0343560978 -l us-central1 gs://oasive-raw-data 2>/dev/null || true
gsutil -m cp ~/Downloads/sflld/historical_data_*.zip gs://oasive-raw-data/sflld/yearly/
echo "✅ Yearly ZIPs uploaded"

# Step 3: Verify upload
echo ""
echo "✅ Step 3: Verifying upload..."
echo "Files in GCS:"
gsutil ls gs://oasive-raw-data/sflld/extracted/ | wc -l | xargs -I {} echo "  {} files in extracted/"
gsutil ls gs://oasive-raw-data/sflld/yearly/ | wc -l | xargs -I {} echo "  {} files in yearly/"

# Step 4: Rebuild Docker image with new code
echo ""
echo "🔨 Step 4: Rebuilding Docker image..."
gcloud builds submit --tag us-central1-docker.pkg.dev/gen-lang-client-0343560978/oasive-images/freddie-ingestor:latest . \
  --project=gen-lang-client-0343560978

# Step 5: Create/Update Cloud Run job for SFLLD processing
echo ""
echo "🚀 Step 5: Creating SFLLD processor Cloud Run job..."
gcloud run jobs create sflld-processor \
    --image=us-central1-docker.pkg.dev/gen-lang-client-0343560978/oasive-images/freddie-ingestor:latest \
    --region=us-central1 \
    --project=gen-lang-client-0343560978 \
    --memory=4Gi \
    --cpu=2 \
    --timeout=4h \
    --max-retries=1 \
    --set-env-vars="CLOUDSQL_CONNECTION_NAME=gen-lang-client-0343560978:us-central1:oasive-postgres,GCP_PROJECT_ID=gen-lang-client-0343560978,GCS_RAW_BUCKET=oasive-raw-data,POSTGRES_DB=oasive,POSTGRES_USER=postgres" \
    --set-secrets="POSTGRES_PASSWORD=postgres-password:latest" \
    --vpc-connector=data-feeds-vpc-1 \
    --vpc-egress=all-traffic \
    --set-cloudsql-instances=gen-lang-client-0343560978:us-central1:oasive-postgres \
    --service-account=cloud-run-jobs-sa@gen-lang-client-0343560978.iam.gserviceaccount.com \
    2>/dev/null || \
gcloud run jobs update sflld-processor \
    --image=us-central1-docker.pkg.dev/gen-lang-client-0343560978/oasive-images/freddie-ingestor:latest \
    --region=us-central1 \
    --project=gen-lang-client-0343560978 \
    --memory=4Gi \
    --cpu=2 \
    --timeout=4h

echo "✅ Cloud Run job ready"

# Step 6: Start the SFLLD processor
echo ""
echo "🚀 Step 6: Starting SFLLD processor job..."
gcloud run jobs execute sflld-processor \
    --region=us-central1 \
    --project=gen-lang-client-0343560978 \
    --args="-m,src.ingestors.sflld_ingestor,--process-gcs,gs://oasive-raw-data/sflld"

echo ""
echo "=============================================="
echo "✅ Migration complete!"
echo ""
echo "The SFLLD processor is now running in the cloud."
echo ""
echo "To monitor progress:"
echo "  gcloud logging read 'resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"sflld-processor\"' --project=gen-lang-client-0343560978 --limit=50"
echo ""
echo "Once verified, delete local files with:"
echo "  rm -rf ~/Downloads/sflld"
echo "=============================================="
