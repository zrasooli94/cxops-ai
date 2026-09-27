#!/bin/sh
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
