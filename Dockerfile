# Dockerfile for Ginnie Mae Ingestor
# Uses standard Python image with Playwright installed at runtime
#
# Note: For Cloud Run Jobs, we install Playwright deps on first run

FROM python:3.11-slim-bookworm

# Set working directory
WORKDIR /app

# Install system dependencies for psycopg2 and Playwright
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    wget \
    gnupg \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    libatspi2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright and browsers
RUN pip install playwright && playwright install chromium --with-deps

# Copy application code
COPY src/ ./src/
COPY migrations/ ./migrations/
COPY scripts/ ./scripts/

# Set Python path
ENV PYTHONPATH=/app

# Flexible entrypoint - allows running any module
# Default: ginnie_ingestor daily mode
# Override with: --args="python,-m,src.ingestors.OTHER_MODULE,--args"
ENTRYPOINT ["python"]
CMD ["-m", "src.ingestors.ginnie_ingestor", "--mode", "daily"]
