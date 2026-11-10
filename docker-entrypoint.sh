#!/bin/sh
set -eu

database_path="${SCHEDULER_DATABASE_PATH:-/app/instance/catalog.db}"
export SCHEDULER_DATABASE_PATH="$database_path"
mkdir -p "$(dirname "$database_path")"

if [ "${SYNC_CATALOG_ON_START:-1}" = "1" ]; then
    PYTHONPATH=/app/backend python /app/backend/bootstrap_catalog.py \
        --database "$database_path" \
        --refresh
else
    PYTHONPATH=/app/backend python /app/backend/bootstrap_catalog.py \
        --database "$database_path"
fi

exec gunicorn \
    --chdir /app/backend \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers "${WEB_CONCURRENCY:-2}" \
    --threads "${WEB_THREADS:-4}" \
    --timeout "${WEB_TIMEOUT:-120}" \
    --no-control-socket \
    --access-logfile - \
    --error-logfile - \
    --capture-output \
    api:app
