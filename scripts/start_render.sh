#!/bin/sh
# Compatibility shim. Delegates to scripts/start_render_api.sh.
#
# An existing Render service still points its start command at this path, so the
# file has to keep working. It contains no startup logic of its own: the API
# entry point lives in start_render_api.sh, and duplicating it here would create
# a second place for the preflight-before-uvicorn ordering and the
# no-migrations-at-boot rule to be maintained.
#
# It does not run migrations and does not start the worker. Both were wrong here
# on any platform: migrations in a start command race across replicas, and a
# worker inside every web replica multiplies job consumers. The worker is its own
# service -- scripts/start_render_worker.sh.
set -eu

exec "$(dirname -- "$0")/start_render_api.sh"