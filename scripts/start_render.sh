#!/bin/sh
# DEPRECATED: single-process start for Render. Use the split services instead.
#
#   web    -> scripts/start_render_api.sh
#   worker -> scripts/start_render_worker.sh
#
# Why this script is no longer the entry point:
#
# 1. It ran `alembic upgrade head` at boot. On a horizontally scaled service
#    every replica runs its start command, so the replicas raced each other on
#    DDL. Migrations are now a single explicit step: scripts/run_migrations.sh.
#
# 2. It started the background worker in the same process as the API. Every
#    replica therefore ran its own job consumer, multiplying consumers by the
#    replica count. The worker is now its own service.
#
# This wrapper keeps the old command working for a single-instance deployment
# while making both problems impossible to reintroduce silently.
set -eu

echo "warning: scripts/start_render.sh is deprecated." >&2
echo "warning: migrations are NOT run here. Run scripts/run_migrations.sh once." >&2
echo "warning: no worker is started here. Use scripts/start_render_worker.sh." >&2

exec /app/scripts/start_render_api.sh
