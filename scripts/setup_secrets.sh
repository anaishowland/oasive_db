#!/bin/bash
# Create secrets in GCP Secret Manager from .env file
# Usage: ./scripts/setup_secrets.sh

set -e

PROJECT_ID="gen-lang-client-0343560978"

# Load .env file
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
else
    echo "Error: .env file not found"
    exit 1
fi

echo "Creating secrets in project: $PROJECT_ID"

# Function to create or update a secret
create_secret() {
    local secret_name=$1
    local secret_value=$2
    
    if [ -z "$secret_value" ]; then
        echo "⚠️  Skipping $secret_name (empty value)"
        return
    fi
    
    # Check if secret exists
    if gcloud secrets describe "$secret_name" --project="$PROJECT_ID" &>/dev/null; then
        echo "📝 Updating existing secret: $secret_name"
        echo -n "$secret_value" | gcloud secrets versions add "$secret_name" \
            --data-file=- \
            --project="$PROJECT_ID"
    else
        echo "✨ Creating new secret: $secret_name"
        echo -n "$secret_value" | gcloud secrets create "$secret_name" \
            --data-file=- \
            --replication-policy="automatic" \
            --project="$PROJECT_ID"
    fi
}

# Create each secret
create_secret "fred-api-key" "$FRED_API_KEY"
create_secret "postgres-password" "$POSTGRES_PASSWORD"
create_secret "freddie-username" "$FREDDIE_USERNAME"
create_secret "freddie-password" "$FREDDIE_PASSWORD"

echo ""
echo "✅ Done! Secrets created in Secret Manager."
echo ""
echo "View them at: https://console.cloud.google.com/security/secret-manager?project=$PROJECT_ID"
