#!/bin/sh
set -eu

echo "Running database migrations..."
alembic upgrade head

echo "Starting CXOps worker..."
python -m scripts.worker &

echo "Starting CXOps API..."
exec python -m uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-10000}"
