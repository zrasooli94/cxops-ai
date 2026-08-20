#!/bin/sh

set -eu

echo "Running database migrations..."
alembic upgrade head

echo "Starting CXOps worker..."
python -m scripts.worker &
WORKER_PID=$!

echo "Starting CXOps API..."
python -m uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-10000}" &
API_PID=$!

cleanup() {
  kill "$WORKER_PID" "$API_PID" 2>/dev/null || true
}

trap cleanup INT TERM EXIT

wait "$API_PID"