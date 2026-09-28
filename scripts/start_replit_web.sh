#!/usr/bin/env bash
# CXOps web/API process for a Replit Reserved VM deployment.
#
# WHY ONE VM RUNS BOTH SERVERS
#
# Replit publishes exactly one port for a deployment, behind its own TLS
# terminator. The public entry point therefore has to be a single server. That
# server is the Next.js standalone server, because every browser request in this
# application is already same-origin:
#
#   * the public widget calls the same-origin BFF at /api/public/chat/[...path]
#   * the staff Control Center reads the backend through a "use server" module
#     (src/lib/tenant/backend-client.ts) and /api/backend/[...path]
#
# So FastAPI never needs to be internet-reachable, and this script deliberately
# binds it to loopback on an internal port. That is a security improvement over
# the previous topology, not a compromise: the backend is not directly exposed,
# the widget key is never sent cross-origin, and the CSP stays connect-src
# 'self'. Frontend and backend sharing a host is exactly why the BFF boundary
# must be preserved rather than removed.
#
# A future direct API consumer for a third party would need a published port and
# a second VM. Nothing in this repository needs one today.
#
# WHAT THIS SCRIPT DOES NOT DO
#
# It does not run migrations. On a restart-every-deploy platform every replica
# re-runs the start command, so migrations at boot race each other on DDL. They
# are a single explicit step: scripts/run_migrations.sh. /ready keeps a process
# out of rotation until that step has succeeded, which is the correct place for
# the coupling.
#
# WHY THIS IS NOT `cmd1 & cmd2`
#
# The obvious two-line version silently swallows a dead child: if uvicorn dies
# the shell keeps running Next.js, the platform still sees the port open, and the
# deployment looks healthy while every backend call 503s through the BFF. This
# supervisor instead:
#
#   * fails fast, before serving anything, on an invalid environment
#   * treats "either child exited" as a deployment failure (wait -n)
#   * forwards SIGTERM/SIGINT to the process group and waits for teardown
#   * reaps the other child on exit so nothing is orphaned
#
# It uses bash and its own trap/wait logic rather than adding supervisord or
# s6 to the image: the invariant set is small, and a dependency that itself
# crashes is a worse failure mode than twenty lines of reviewable shell.
set -Eeuo pipefail

readonly REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Replit injects PORT for the published port. The internal API port is separate
# and never published; it only has to be reachable from this machine.
readonly PUBLIC_PORT="${PORT:-8080}"
readonly INTERNAL_API_PORT="${INTERNAL_API_PORT:-8000}"
readonly PYTHON_BIN="${PYTHON:-python3}"
readonly FRONTEND_DIR="${FRONTEND_DIR:-frontend}"

log() { printf '%s replit-web %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
fail() { log "ERROR: $*" >&2; exit 1; }

# --- 1. Dependencies -------------------------------------------------------------
#
# Replit's nix layer provides the interpreters; the language dependencies are
# installed here so a fresh Reserved VM is reproducible from the repo alone.
#
# This runs BEFORE the preflight on purpose. The preflight imports
# app.core.encryption, so it needs the project's own dependencies -- on a fresh
# VM, running it against the system interpreter fails with ModuleNotFoundError
# and reports a broken environment rather than an invalid one.
if [ ! -x .venv/bin/python ]; then
  log "creating virtualenv"
  "$PYTHON_BIN" -m venv .venv
  .venv/bin/python -m pip install --quiet --upgrade pip
fi

if [ ! -x .venv/bin/uvicorn ]; then
  log "installing Python dependencies"
  .venv/bin/python -m pip install --quiet -r requirements.txt
fi

cd "$FRONTEND_DIR"
if [ ! -d node_modules ]; then
  log "installing Node dependencies"
  # Node 22 ships npm 10, which honours --include=dev by default; the explicit
  # flag documents that devDependencies are required, because `next build` needs
  # the TypeScript and PostCSS toolchain.
  npm ci --include=dev
fi
cd "$REPO_ROOT"

# --- 2. Fail fast on configuration ------------------------------------------------
#
# A crash loop is the desired outcome for a misconfigured production process: it
# is visible and bounded, and it cannot serve a customer. This has bitten before
# - a service with ENVIRONMENT=production and a non-https FRONTEND_BASE_URL
# restarted forever while the previously healthy instance kept answering.
#
# Before either server starts, so that a process which starts and *then* discovers
# it has no encryption key has not already accepted traffic.
if ! .venv/bin/python scripts/preflight_production_env.py; then
  fail "environment preflight failed; refusing to start"
fi

# CXOPS_PUBLIC_SITE_URL is inlined into the build by next/robots/sitemap, so it
# has to be present *now*, not merely at runtime, or the deployed site advertises
# localhost as its canonical origin to every crawler.
if [ -z "${CXOPS_PUBLIC_SITE_URL:-}" ]; then
  fail "CXOPS_PUBLIC_SITE_URL is not set; it is inlined at build time and cannot be defaulted"
fi
case "$CXOPS_PUBLIC_SITE_URL" in
  https://*) ;;
  *) fail "CXOPS_PUBLIC_SITE_URL must be https (got ${CXOPS_PUBLIC_SITE_URL%%\?*})" ;;
esac

# BACKEND_API_URL is read server-side by the BFF and the Control Center server
# modules. On this VM it points at the loopback API below. It is deliberately
# not validated as https: it never leaves the machine.
if [ -z "${BACKEND_API_URL:-}" ]; then
  export BACKEND_API_URL="http://127.0.0.1:${INTERNAL_API_PORT}"
  log "BACKEND_API_URL unset; defaulting to the loopback API on port ${INTERNAL_API_PORT}"
fi

# --- 3. Build the frontend --------------------------------------------------------
#
# Built on every deploy, not at image build time, because the canonical origin is
# a deployment environment variable. Replit injects Secrets as process
# environment, so a build that happens at VM start sees them.
export NEXT_TELEMETRY_DISABLED=1
log "building frontend with CXOPS_PUBLIC_SITE_URL already in the environment"
( cd "$FRONTEND_DIR" && npm run build )
[ -f "$FRONTEND_DIR/.next/standalone/server.js" ] \
  || fail "frontend build did not produce .next/standalone/server.js"

# Next's standalone output emits only the server and its traced dependencies. It
# deliberately does NOT copy .next/static or public/, because a Docker image can
# copy them itself. Running server.js straight from the build directory without
# doing that copy is the classic result: HTML renders, every /_next/static/*.js
# 404s, and the app is a blank page.
#
# The next.config.ts rewrites proxy /health, /ready, and /version to the loopback
# API through BACKEND_API_URL, so those stay reachable on the published port
# without publishing the API itself.
cp -R "$FRONTEND_DIR/.next/static" "$FRONTEND_DIR/.next/standalone/.next/static"
if [ -d "$FRONTEND_DIR/public" ]; then
  cp -R "$FRONTEND_DIR/public" "$FRONTEND_DIR/.next/standalone/public"
fi
log "frontend build complete"

# --- 4. Supervise ----------------------------------------------------------------
#
# Two children, one supervisor. `wait -n` returns as soon as *either* exits,
# which is the property that turns a crashed backend into a failed deployment
# instead of a silently degraded one.
#
# Children are signalled by PID, not by process group. A process-group kill needs
# the child's PID to also be its PGID, which is only true if something like
# `setsid` forked and stayed as the parent - and in that case `$!` is the
# supervisor, not the program, so the group id is wrong. Both uvicorn (no
# --reload, no --workers) and the Next.js standalone server are single
# processes with no child tree, so PID signalling is exact and needs no
# dependency.
api_pid=""
web_pid=""
shutting_down=0
signal_received=0

terminate() {
  [ "$shutting_down" -eq 1 ] && return 0
  shutting_down=1
  log "stopping children"

  for pid in "$api_pid" "$web_pid"; do
    if [ -n "$pid" ]; then
      kill -TERM "$pid" 2>/dev/null || true
    fi
  done

  # Bounded grace period. A process that ignores SIGTERM must not wedge a deploy.
  #
  # Kept below 10s deliberately: deployment platforms send SIGTERM and then
  # SIGKILL after their own, shorter grace window, and a script that waits longer
  # than they do is SIGKILLed mid-shutdown. That truncates the log exactly when a
  # stop is the thing being investigated, and it turns a clean restart into an
  # unexplained one. Being killed first is worse than killing the child here.
  local waited=0
  local alive
  while [ "$waited" -lt 8 ]; do
    alive=0
    for pid in "$api_pid" "$web_pid"; do
      if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        alive=1
      fi
    done
    [ "$alive" -eq 0 ] && break
    sleep 1
    waited=$((waited + 1))
  done

  for pid in "$api_pid" "$web_pid"; do
    if [ -n "$pid" ]; then
      kill -KILL "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
    fi
  done
  log "children stopped"
}

# A platform-initiated stop is a normal outcome, not a failure, so it exits 0.
# A child dying on its own is a failure and exits non-zero.
on_signal() {
  signal_received=1
  log "received SIG$1; shutting down"
  terminate
  exit 0
}

on_child_exit() {
  local status=$1
  log "child exited with status ${status}; treating the deployment as failed"
  terminate
  exit "$status"
}

trap 'on_signal TERM' TERM
trap 'on_signal INT' INT
trap 'terminate' EXIT

.venv/bin/python -m uvicorn app.main:app \
  --host 127.0.0.1 \
  --port "$INTERNAL_API_PORT" \
  --proxy-headers \
  --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}" \
  --timeout-keep-alive "${KEEP_ALIVE_SECONDS:-30}" \
  --no-server-header &
api_pid=$!

PORT="$PUBLIC_PORT" HOSTNAME=0.0.0.0 NODE_ENV=production \
  node "$FRONTEND_DIR/.next/standalone/server.js" &
web_pid=$!

log "api pid=${api_pid} on 127.0.0.1:${INTERNAL_API_PORT}"
log "frontend pid=${web_pid} on 0.0.0.0:${PUBLIC_PORT}"
log "public origin ${CXOPS_PUBLIC_SITE_URL}"

# `wait -n` is the precise way to notice the *first* child to exit, but it only
# exists in bash 4.3+. Requiring it would make this script's ability to start at
# all depend on which bash the platform happens to put on PATH -- an assumption
# that fails as a crash loop, discovered during a deploy. So the exact-status
# path is used when available and a portable poll otherwise; both paths exit
# non-zero on an unexpected child exit, which is the property that matters.
if ((BASH_VERSINFO[0] > 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] >= 3))); then
  readonly HAS_WAIT_N=1
else
  readonly HAS_WAIT_N=0
  log "bash ${BASH_VERSION} has no 'wait -n'; using the portable poll path"
fi

# Reap zombies and notice the first child to exit.
if [ "$HAS_WAIT_N" -eq 1 ]; then
  while true; do
    set +e
    wait -n
    status=$?
    set -e
    # A signal that landed during wait is a clean stop, already handled above.
    [ "$signal_received" -eq 1 ] && break
    on_child_exit "$status"
  done
else
  # Portable equivalent. `kill -0` fails once bash has reaped the child, which
  # bash does on its own between commands, so this observes a real exit rather
  # than a zombie. If the individual `wait` can no longer recover the status --
  # because bash discarded it -- the exit is still reported as a failure, since
  # a child that vanished on its own is a failed deployment either way. Reporting
  # that as success is precisely the failure mode this supervisor exists to catch.
  while true; do
    [ "$signal_received" -eq 1 ] && break
    for pid in "$api_pid" "$web_pid"; do
      if ! kill -0 "$pid" 2>/dev/null; then
        set +e
        wait "$pid" 2>/dev/null
        status=$?
        set -e
        [ "$status" -eq 127 ] && status=1
        on_child_exit "$status"
      fi
    done
    sleep 1
  done
fi
