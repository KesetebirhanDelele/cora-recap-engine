FROM python:3.12-slim

WORKDIR /app

# Install build deps for psycopg2-binary and other compiled extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
 && rm -rf /var/lib/apt/lists/*

# Copy project metadata first for layer caching
COPY pyproject.toml ./

# Copy source
COPY app/ ./app/
COPY migrations/ ./migrations/
COPY alembic.ini ./

# Install the package and all runtime dependencies
RUN pip install --no-cache-dir -e .

EXPOSE 8000
