#!/bin/sh
# DEPRECATED - superseded by scripts/start_replit_web.sh (Phase 1P-Replit).
#
# Kept working and behaviourally unchanged so nothing that still references it
# breaks, but it is no longer the production entry point. It is kept for one
# reason beyond continuity: the comments below record why migrations must never
# live in a start command, which is the single most expensive mistake the
# previous topology carried.
#
# If you are deploying CXOps, use scripts/start_replit_web.sh. See
# config/replit/deployment-env.yaml for the environment contract and
# docs/runbooks/replit-production-deployment.md for the procedure.
#
# ---------------------------------------------------------------------------
# Start the CXOps API web service.
#
# This script deliberately does NOT run migrations. On a multi-instance
# platform every instance executes this file at boot, and concurrent
# `alembic upgrade head` calls race each other on DDL locks. Run migrations as a
# single, explicit, one-off step instead (scripts/run_migrations.sh) and let
# /ready keep the instance out of rotation until that step has succeeded.
set -eu

echo "Starting CXOps API on port ${PORT:-10000}..."
exec python -m uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-10000}" \
  --proxy-headers \
  --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-*}" \
  --timeout-keep-alive "${KEEP_ALIVE_SECONDS:-30}" \
  --no-server-header
