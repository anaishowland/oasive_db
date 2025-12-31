# Oasive Data Ingestion Jobs
# Base image for all ingestion Cloud Run jobs

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./src/
COPY migrations/ ./migrations/

# Set Python path
ENV PYTHONPATH=/app

# Default command (override in Cloud Run job definition)
CMD ["python", "-m", "src.ingestors.fred_ingestor"]
