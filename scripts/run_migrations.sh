#!/usr/bin/env bash
# VisionAI - Database migration and seed script
# Waits for PostgreSQL, runs Alembic migrations, then seeds initial data.

set -e

echo "=========================================="
echo "VisionAI Database Setup"
echo "=========================================="

# Configuration
DB_HOST="${POSTGRES_HOST:-localhost}"
DB_PORT="${POSTGRES_PORT:-5432}"
DB_USER="${POSTGRES_USER:-visionai}"
MAX_RETRIES=30
RETRY_INTERVAL=2

# Wait for PostgreSQL to be ready
echo "[1/3] Waiting for PostgreSQL at ${DB_HOST}:${DB_PORT}..."
retries=0
until pg_isready -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -q 2>/dev/null; do
    retries=$((retries + 1))
    if [ $retries -ge $MAX_RETRIES ]; then
        echo "ERROR: PostgreSQL not ready after $MAX_RETRIES attempts. Exiting."
        exit 1
    fi
    echo "  Attempt $retries/$MAX_RETRIES - PostgreSQL not ready, waiting ${RETRY_INTERVAL}s..."
    sleep $RETRY_INTERVAL
done
echo "  PostgreSQL is ready!"

# Ensure pgvector extension exists
echo ""
echo "[1.5/3] Ensuring pgvector extension..."
PGPASSWORD="${POSTGRES_PASSWORD:-visionai_password}" psql \
    -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "${POSTGRES_DB:-visionai}" \
    -c "CREATE EXTENSION IF NOT EXISTS vector;" 2>/dev/null || \
    echo "  Note: pgvector extension may already exist or require superuser. Continuing..."

# Run Alembic migrations
echo ""
echo "[2/3] Running Alembic migrations..."
cd /app 2>/dev/null || cd "$(dirname "$0")/../backend"
alembic upgrade head
echo "  Migrations complete!"

# Seed initial data
echo ""
echo "[3/3] Seeding initial data..."
python -m scripts.seed_data 2>/dev/null || python ../scripts/seed_data.py
echo "  Seeding complete!"

echo ""
echo "=========================================="
echo "Database setup finished successfully!"
echo "=========================================="
