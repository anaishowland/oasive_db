#!/bin/bash
# Upload SFLLD historical data to GCS
# This script uploads extracted files and yearly ZIPs to Google Cloud Storage

set -e

SFLLD_DIR=~/Downloads/sflld
GCS_BUCKET="gs://oasive-raw-data/sflld"

echo "=============================================="
echo "SFLLD Upload to GCS"
echo "=============================================="

# Check if gsutil is available
if ! command -v gsutil &> /dev/null; then
    echo "❌ gsutil not found. Please install Google Cloud SDK."
    exit 1
fi

# Step 1: Upload extracted folder (preserves your extraction work)
echo ""
echo "📤 Step 1: Uploading extracted files to GCS..."
echo "   Source: $SFLLD_DIR/extracted/"
echo "   Dest:   $GCS_BUCKET/extracted/"
gsutil -m cp -r "$SFLLD_DIR/extracted" "$GCS_BUCKET/"

# Step 2: Upload original yearly ZIPs (for remaining extractions in cloud)
echo ""
echo "📤 Step 2: Uploading yearly ZIPs to GCS..."
echo "   Source: $SFLLD_DIR/historical_data_*.zip"
echo "   Dest:   $GCS_BUCKET/yearly/"
gsutil -m cp "$SFLLD_DIR"/historical_data_*.zip "$GCS_BUCKET/yearly/"

# Step 3: Verify upload
echo ""
echo "✅ Step 3: Verifying upload..."
echo ""
echo "Extracted files in GCS:"
gsutil ls "$GCS_BUCKET/extracted/" | head -20
echo "..."
gsutil ls "$GCS_BUCKET/extracted/*.txt" 2>/dev/null | wc -l | xargs -I {} echo "{} txt files uploaded"

echo ""
echo "Yearly ZIPs in GCS:"
gsutil ls "$GCS_BUCKET/yearly/" | wc -l | xargs -I {} echo "{} yearly ZIP files uploaded"

echo ""
echo "=============================================="
echo "✅ Upload complete!"
echo ""
echo "To delete local files after verification, run:"
echo "  rm -rf ~/Downloads/sflld"
echo "=============================================="
