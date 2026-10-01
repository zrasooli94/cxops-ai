#!/bin/sh
# Start the CXOps API web service on Render.
#
# This is the production entry point for the API. The production topology is:
#
#   frontend (Vercel)  -> Next.js, public widget, same-origin BFF
#   api     (Render)   -> this script; FastAPI/uvicorn
#   worker  (Render)   -> scripts/start_render_worker.sh, a separate service
#   auth    (Nhost)    -> staff JWKS issuer
#   db      (Neon)     -> PostgreSQL + pgvector, already at Alembic head 1p4a0001
#
# Render terminates TLS and forwards over its own proxy, so uvicorn is told to
# trust forwarded headers below. Without --proxy-headers the app builds http://
# URLs and marks cookies insecure behind that termination.
#
# This script deliberately does NOT run migrations. On a scaled service every
# instance executes this file at boot, so concurrent `alembic upgrade head` calls
# race each other on DDL locks -- the single most expensive mistake the previous
# single-process topology carried. Migrations are a single explicit step:
# scripts/run_migrations.sh, run once per deploy before any code that needs a new
# schema. /ready keeps the instance out of rotation until that step succeeded.
#
# The preflight runs first, so a process that starts and *then* discovers a
# missing encryption key, an http public URL, or a plaintext-capable DATABASE_URL
# has not already accepted traffic. It also imports the project's third-party
# dependencies, so running it proves the image has what the code expects.
set -eu

if ! python scripts/preflight_production_env.py; then
  echo "environment preflight failed; refusing to start" >&2
  exit 1
fi

# PORT is assigned by the platform, never defaulted silently.
echo "Starting CXOps API on port ${PORT:-10000}..."
exec python -m uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-10000}" \
  --proxy-headers \
  --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-*}" \
  --timeout-keep-alive "${KEEP_ALIVE_SECONDS:-30}" \
  --no-server-header