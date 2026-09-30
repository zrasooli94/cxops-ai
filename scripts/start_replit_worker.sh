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

# The production database, mapped before anything else runs.
#
# CXOps production runs on an external Neon PostgreSQL, migrated to Alembic head
# 1p4a0001 with pgvector installed, and the worker claims jobs from the same
# database the web deployment serves. The application reads DATABASE_URL, so that
# is what gets exported, from EXTERNAL_DATABASE_URL -- an app-owned deployment
# secret -- unconditionally.
#
# Unconditional for the reason given at length in start_replit_web.sh: Replit
# already publishes a platform-managed DATABASE_URL for this project, and which of
# the two same-named variables wins depends on injection order. The worker must
# land on the same database as the web deployment, so it copies over the top of
# whatever the platform provided rather than deferring to it.
#
# Before the venv work on purpose: a missing secret is a configuration error, and
# the venv build and pip install that follow are minutes. Reporting it after them
# means every redeploy pays that cost to learn something an operator could have
# been told immediately.
#
# Neither value is logged. A Neon URL carries the password.
if [ -z "${EXTERNAL_DATABASE_URL:-}" ]; then
  fail "EXTERNAL_DATABASE_URL is not set; it is this project's deployment secret for the production PostgreSQL (Neon, Alembic head 1p4a0001) and it is required. The platform-managed DATABASE_URL is deliberately not used."
fi
export DATABASE_URL="$EXTERNAL_DATABASE_URL"
log "database binding: DATABASE_URL set from EXTERNAL_DATABASE_URL (value not logged)"

# Dependencies first, preflight second. The preflight imports
# app.core.encryption, so it needs the project's own dependencies; on a fresh VM,
# running it against the system interpreter reports a missing module rather than
# the actual configuration problem.
#
# This deployment is the only one that installs anything at run time. The web
# deployment does not: its build phase (scripts/build_replit_web.sh) pip-installs
# requirements.txt into .replit-python/, and scripts/start_replit_web.sh imports
# from there via PYTHONPATH, creating no venv and running no pip. The worker has
# no build phase of its own -- its [deployment] build is unset, since a
# deployment build command is not inherited from another deployment in the same
# project -- and it imports app.core.encryption, which needs the real
# dependencies, so it creates and populates .venv here. That asymmetry is
# deliberate, and it is why a change to how one of these two launchers installs
# cannot be assumed to apply to the other.
#
# Every pip invocation goes through venv_pip so the Reserved VM image's own pip
# configuration cannot defeat the install: that environment can inject
# user-install behavior, and a user install inside a venv is an error pip
# refuses to perform ("Can not perform a '--user' install"). An environment
# variable is the one lever that beats every pip config file, which is why the
# override is here rather than in a pip.conf.
#
# Scoped with `env` so the override applies to these commands only, and the
# exec'd worker does not inherit a mutated pip environment.
venv_pip() {
  env \
    PIP_USER=false \
    PIP_REQUIRE_VIRTUALENV=1 \
    "$REPO_ROOT/.venv/bin/python" -m pip "$@"
}

if [ ! -x .venv/bin/python ]; then
  log "creating virtualenv"
  "$PYTHON_BIN" -m venv .venv
  venv_pip install --quiet --upgrade pip \
    || fail "could not upgrade pip inside .venv; the Reserved VM image or its pip configuration is unusable"
fi

if ! .venv/bin/python -c "import app" 2>/dev/null; then
  log "installing Python dependencies"
  venv_pip install --quiet -r requirements.txt \
    || fail "could not install requirements.txt into .venv; the pip output above names the cause"
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
