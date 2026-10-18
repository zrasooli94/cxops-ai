#!/bin/sh
# Source-only Render Cron entrypoint. This file does not create a service or
# schedule. Provisioning it requires explicit operator approval.
set -eu

if ! python scripts/preflight_production_env.py; then
  echo "environment preflight failed; refusing to run cron worker" >&2
  exit 1
fi

exec python -m scripts.worker \
  --once \
  --max-jobs 25 \
  --max-seconds 240 \
  --exit-nonzero-on-unsuccessful \
  --finalize-expired-public-chat \
  --expiry-batch-size 50
