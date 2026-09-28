#!/bin/sh
# Run database migrations as a single explicit step.
#
# Never run this from a service start command. Every replica of the web service
# would race on the same DDL, which produces "relation already exists" or
# "duplicate key value violates unique constraint" errors that look like
# application bugs.
#
# Usage (from a one-off shell or a CI deploy job, with DATABASE_URL set):
#   scripts/run_migrations.sh
#
# The advisory lock is held by the same process that runs Alembic, so a second
# migrator waits instead of interleaving DDL with this one. If the lock cannot
# be taken within MIGRATION_LOCK_WAIT_SECONDS the script fails loudly rather
# than blocking a deploy job forever.
#
# The locking logic lives in scripts/migration_lock.py so it can be tested
# without running a real migration.
#
# Verify afterwards with scripts/validate_production_config.py, which compares
# `alembic current` to `alembic heads` without mutating anything.
set -eu

if [ -z "${DATABASE_URL:-}" ]; then
  echo "error: DATABASE_URL must be set" >&2
  exit 2
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(dirname "$SCRIPT_DIR")
cd "$REPO_ROOT"

echo "Acquiring advisory migration lock and applying migrations..."
MIGRATION_LOCK_WAIT_SECONDS="${MIGRATION_LOCK_WAIT_SECONDS:-300}" \
  "${PYTHON:-python}" "$SCRIPT_DIR/migration_lock.py"
