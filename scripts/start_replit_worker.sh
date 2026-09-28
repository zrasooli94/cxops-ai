#!/usr/bin/env bash
# CXOps worker process for a Replit deployment.
#
# The worker is a SEPARATE deployment from the web/API process, on purpose.
# It claims durable integration jobs; if it ran inside the web deployment then
# every web replica would be a job consumer, multiplying consumers by the
# replica count. Job claiming is row-locked and therefore safe under
# concurrency, but one consumer per deployment is both cheaper and far easier to
# reason about during an incident.
#
# This script:
#
#   * fails fast on an invalid environment, before claiming any job
#   * reuses the existing entry point, python -m scripts.worker
#   * runs no migrations - see the note in start_replit_web.sh
#   * execs, so the platform's signal reaches the worker directly with no
#     supervisor layer in between
#
# The Prometheus metrics listener in scripts/worker.py binds 9101. On a worker
# deployment no port is published, so it is not reachable from the internet; it
# is left enabled deliberately, because "the worker is alive" is the first
# question during an incident. Set WORKER_METRICS_PORT=0 to disable it.
set -Eeuo pipefail

readonly REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

readonly PYTHON_BIN="${PYTHON:-python3}"

log() { printf '%s replit-worker %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
fail() { log "ERROR: $*" >&2; exit 1; }

# Dependencies first, preflight second. The preflight imports
# app.core.encryption, so it needs the project's own dependencies; on a fresh VM,
# running it against the system interpreter reports a missing module rather than
# the actual configuration problem.
if [ ! -x .venv/bin/python ]; then
  log "creating virtualenv"
  "$PYTHON_BIN" -m venv .venv
  .venv/bin/python -m pip install --quiet --upgrade pip
fi

if ! .venv/bin/python -c "import app" 2>/dev/null; then
  log "installing Python dependencies"
  .venv/bin/python -m pip install --quiet -r requirements.txt
fi

if ! .venv/bin/python scripts/preflight_production_env.py; then
  fail "environment preflight failed; refusing to start"
fi

if [ "${WORKER_METRICS_PORT:-}" = "0" ]; then
  log "WORKER_METRICS_PORT=0; metrics listener disabled"
fi

log "starting worker (python -m scripts.worker)"

# exec: replaces this shell so the platform's SIGTERM reaches the worker
# directly. Any wrapper process would be a signal-handling bug waiting to
# happen - this exact class of bug is why start_render.sh had to be deprecated.
exec .venv/bin/python -m scripts.worker
