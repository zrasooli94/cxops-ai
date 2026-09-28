#!/bin/sh
# DEPRECATED - superseded by scripts/start_replit_worker.sh (Phase 1P-Replit).
#
# Kept working and behaviourally unchanged. The reasoning below still holds on
# any platform, which is why the Replit worker is a separate deployment too and
# not a process inside the web deployment.
#
# If you are deploying CXOps, use scripts/start_replit_worker.sh, which adds the
# fail-fast production preflight and an explicit metrics-port decision.
#
# ---------------------------------------------------------------------------
# Start the CXOps background worker.
#
# This is a SEPARATE service from the API on purpose. The worker claims durable
# integration jobs, so running one inside every web instance multiplies job
# consumers by the replica count. Job claiming is row-locked and therefore safe
# under concurrency, but one consumer per deployment is both cheaper and far
# easier to reason about during an incident.
set -eu

echo "Starting CXOps worker..."
exec python -m scripts.worker
