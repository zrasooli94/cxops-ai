#!/bin/sh
# Start the CXOps background worker on Render.
#
# This is the production entry point for the worker. It is a SEPARATE service
# from the API on purpose.
#
# One worker per service, not one per replica: the worker claims durable
# integration jobs, so running it inside every API instance multiplies job
# consumers by the replica count. Job claiming is row-locked and therefore safe
# under concurrency, but one consumer per service is both cheaper and far easier
# to reason about during an incident.
#
# The worker must NOT publish an HTTP port. It serves no traffic, and a health
# check against a worker is a sign the service is misconfigured. Its Prometheus
# listener on WORKER_METRICS_PORT (default 9101) is for local/in-cluster use
# only; set it to 0 if the platform forbids extra listeners, at the cost of the
# metrics that distinguish "idle" from "dead".
#
# It does NOT run migrations -- same reason as the API: every replica runs this
# file, so a migration here races itself. Run scripts/run_migrations.sh once per
# deploy instead.
#
# The same production preflight runs first. The worker imports app.core.config,
# whose production validation is process-wide, so a missing auth or encryption
# variable is a crash loop rather than a degraded feature. Checking it here means
# the reason is legible in the service log instead of buried in a traceback.
set -eu

if ! python scripts/preflight_production_env.py; then
  echo "environment preflight failed; refusing to start" >&2
  exit 1
fi

echo "Starting CXOps worker..."
exec python -m scripts.worker