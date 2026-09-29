#!/usr/bin/env bash
# CXOps development launcher for the Replit workspace.
#
# This is what the workspace Run button starts (.replit `run = "bash
# scripts/start_replit_dev.sh"`). It is deliberately NOT the production start
# path -- see scripts/start_replit_web.sh for that. The difference is the whole
# point of having two files:
#
#   development (here)      production (start_replit_web.sh)
#   ------------------------ --------------------------------------
#   no environment preflight  fail-fast production preflight
#   no secrets required       https public URLs, JWKS, ENCRYPTION_KEYS
#   no migrations, ever       no migrations, ever
#   next dev (on-demand)      next production build
#   uses system Python 3.12   requires a provisioned .venv
#
# The separation exists because a developer cannot iterate if previewing a
# change requires first provisioning production credentials, and — worse — a
# shared launcher would tempt someone into adding a secret requirement or a
# migration to a path that also runs in production.
#
# WHAT THIS DOES NOT DO, DELIBERATELY
#
# No preflight. A development preview must work with none of DATABASE_URL,
# ENCRYPTION_KEYS, OPENAI_API_KEY, or AUTH_* set, because none of them exist in
# a fresh workspace.
#
# No migrations. Not even "just to make the preview work". A migration runs
# against whatever DATABASE_URL happens to be present, and in a workspace that
# may be a shared or an operator's database. Migrations are one explicit step
# (scripts/run_migrations.sh), run by a human, never by a start command.
#
# PYTHON: SYSTEM INTERPRETER, NOT .venv
#
# This script uses the system Python 3.12 that the `python-3.12` runtime module
# in .replit provides, when there is no .venv. It does not create one. Two
# reasons: creating a venv and installing requirements.txt on every workspace
# cold start is slow enough to be skipped, and -- the real reason -- the system
# interpreter is the one whose version the module list actually pins. A
# launcher that silently built a different Python would make the preview a poor
# predictor of the deployment.
#
# If a .venv does exist, it is preferred, so a developer who has prepared one
# gets the same environment the production scripts use.
set -Eeuo pipefail

readonly REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Replit's Preview expects the app on port 5000 and injects PORT when it does
# not want the default. 3000 was wrong: a fresh workspace served nothing, and the
# failure looks like a build failure rather than a wrong port. The explicit
# injection still wins, so a tunnel or a custom dev port overrides as before.
#
# The API stays on loopback: in development as in production, the browser only
# ever talks to the frontend's same-origin BFF, so exposing the API directly
# would teach a preview habit that the deployment does not support.
readonly PUBLIC_PORT="${PORT:-5000}"
readonly INTERNAL_API_PORT="${INTERNAL_API_PORT:-8000}"
readonly FRONTEND_DIR="${FRONTEND_DIR:-frontend}"

log() { printf '%s replit-dev %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
fail() { log "ERROR: $*" >&2; exit 1; }

# --- 1. OpenAI key fallback, development only -------------------------------------
#
# Three modules construct an OpenAI-backed client at import time
# (app/services/embedding_service.py, rag_service.py, agent_workflow_service.py).
# The OpenAI SDK refuses to construct a client with no key, so a fresh workspace
# with no OPENAI_API_KEY dies during import of app.main -- before uvicorn is
# listening, so there is no server and no error the developer can act on beyond a
# stack trace in the log.
#
# So a development preview injects a synthetic, non-working key. It is NOT a
# credential: it is a fixed literal with no entropy, and calling OpenAI with it
# fails with an authentication error. That is the point -- AI features are
# visibly unavailable rather than silently misbehaving, and everything that does
# not need the model (health, auth, tenant config, the UI) works.
#
# The literal is assembled from pieces so this file contains no secret-shaped
# token for a scanner to flag. Assembling it changes nothing about the value.
#
# Two guards make this safe to have in a repository:
#
#   * it is a hard failure under ENVIRONMENT=production, so a production start
#     can never inherit the placeholder. Production still demands a real key
#     through the preflight gate, which is unchanged.
#   * it only runs when the variable is ABSENT from the environment. A key
#     already exported in the shell is never touched.
#
# Note the deliberate limit of that second guard: the shell does not read .env,
# so a developer whose key lives only in .env -- and is not exported -- will get
# the placeholder here and auth errors from the AI features. Export the key, or
# run uvicorn directly, when working locally against a real key. The alternative
# was to parse .env in shell to detect the key, which duplicates the
# configuration loader and fails silently when the file format changes; the
# explicit export is the more honest failure.
if [ "${ENVIRONMENT:-development}" = "production" ]; then
  if [ -z "${OPENAI_API_KEY:-}" ]; then
    fail "ENVIRONMENT=production with no OPENAI_API_KEY; the development placeholder is refused in production on purpose"
  fi
else
  if [ -z "${OPENAI_API_KEY:-}" ]; then
    OPENAI_API_KEY="$(printf 'dev-%s-%s' "not-a-real-key" "placeholder")"
    export OPENAI_API_KEY
    log "WARNING: no OPENAI_API_KEY set; using a synthetic non-working placeholder."
    log "WARNING: AI features (RAG, embeddings, agent workflow) will fail with an auth error. Everything else works."
  fi
fi

# --- 2. Python -------------------------------------------------------------------
#
# Prefer a prepared .venv; fall back to the system interpreter. `command -v`
# rather than a hardcoded path because the module could place python3.12 on PATH
# under any of several names, and failing to find it should say so plainly.
if [ -x .venv/bin/python ]; then
  PYTHON_BIN=".venv/bin/python"
else
  PYTHON_BIN=""
  for candidate in python3.12 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      PYTHON_BIN="$(command -v "$candidate")"
      break
    fi
  done
  [ -n "$PYTHON_BIN" ] || fail "no Python interpreter found; expected the python-3.12 runtime module"
  log "no .venv; using system interpreter ${PYTHON_BIN} ($("$PYTHON_BIN" -c 'import platform;print(platform.python_version())'))"
fi
log "python: ${PYTHON_BIN}"

# --- 3. Node dependencies --------------------------------------------------------
#
# Installed only when absent. A preview that reinstalls on every start is a slow
# preview, and the lockfile guarantees the same versions when it does.
cd "$FRONTEND_DIR"
if [ ! -d node_modules ]; then
  log "installing Node dependencies (first run only)"
  # Node 22 ships npm 10, which includes devDependencies by default. They are
  # required: `next dev` and the TypeScript/PostCSS toolchain are dev tools.
  npm ci --include=dev
fi
cd "$REPO_ROOT"

# --- 4. Supervise ----------------------------------------------------------------
#
# Same shape as the production supervisor, for the same reason: a bare
# `cmd1 & cmd2` hides a dead child. If uvicorn dies, the workspace keeps serving
# the frontend and every backend call fails through the BFF, which looks like an
# application bug rather than a dead process.
#
# Children are signalled by PID, not process group. Both are single processes
# with no child tree here, so PID signalling is exact and needs no dependency
# such as setsid — whose output would make `$!` the wrapper rather than the
# program, which is a bug this design already had to avoid once.
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

  # Bounded grace. Kept under 10s because the platform SIGKILLs at roughly that
  # mark, and a launcher that waits longer is killed mid-shutdown — which
  # truncates the log exactly when the shutdown is what is being investigated.
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

  # SIGKILL whatever survived, then reap. Reaping matters as much as killing: an
  # unreaped child keeps the shell alive, and the workspace appears to hang
  # after a stop instead of exiting.
  for pid in "$api_pid" "$web_pid"; do
    if [ -n "$pid" ]; then
      kill -KILL "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
    fi
  done
  log "children stopped"
}

# A platform-initiated stop is a normal outcome, not a failure: exit 0.
on_signal() {
  signal_received=1
  log "received SIG$1; shutting down"
  terminate
  exit 0
}

# A child dying on its own is a failed preview. Exiting non-zero is what makes
# the workspace show a stopped run instead of a live-looking one.
on_child_exit() {
  local status=$1
  [ "$status" -eq 0 ] && status=1
  log "child exited with status ${status}; treating the preview as failed"
  terminate
  exit "$status"
}

trap 'on_signal TERM' TERM
trap 'on_signal INT' INT
trap 'terminate' EXIT

# --- 5. Start --------------------------------------------------------------------

# FastAPI on loopback. --reload because this is development and the whole point
# is a short edit-to-preview cycle. No --proxy-headers here: there is no trusted
# proxy in development, and trusting a forwarded scheme nobody controls is a bad
# habit to carry into the deployment.
"$PYTHON_BIN" -m uvicorn app.main:app \
  --host 127.0.0.1 \
  --port "$INTERNAL_API_PORT" \
  --reload \
  --no-server-header &
api_pid=$!

# Next.js on the published port, so the Replit preview URL serves the app.
# `next dev` is used rather than a production build: on-demand compilation is
# faster to iterate, and the deployment is the thing that needs a real build.
#
# BACKEND_API_URL points the server-side BFF at the loopback API above, the same
# relationship the production topology has. It is read at request time, so this
# is the one value that can be right here and different in production without a
# rebuild.
# `next dev` is invoked through node directly, not via `npm run dev`. npm is a
# wrapper: `npm run dev &` makes $! the npm process, and npm spawns next as a
# GRANDCHILD. Signalling $! kills npm, leaves the real next process running, and
# the workspace holds the published port after a stop — an orphan that looks like
# a preview that will not restart. Verified by probe, not assumed.
#
# This is the same reason the production launcher starts
# .next/standalone/server.js through node instead of npm run start.
BACKEND_API_URL="http://127.0.0.1:${INTERNAL_API_PORT}" \
PORT="$PUBLIC_PORT" HOSTNAME=0.0.0.0 \
  node "$FRONTEND_DIR/node_modules/next/dist/bin/next" dev "$FRONTEND_DIR" \
  -H 0.0.0.0 -p "$PUBLIC_PORT" &
web_pid=$!

log "api pid=${api_pid} on 127.0.0.1:${INTERNAL_API_PORT}"
log "frontend pid=${web_pid} on 0.0.0.0:${PUBLIC_PORT}"
log "preview port ${PUBLIC_PORT}; BFF target http://127.0.0.1:${INTERNAL_API_PORT}"
log "no preflight, no secrets, no migrations — development preview"

# `wait -n` exists only in bash 4.3+. Requiring it would make this launcher's
# ability to start depend on which bash the platform put on PATH, and that
# failure mode is a crash loop. Use the exact-status path when available and a
# portable poll otherwise; both exit non-zero on an unexpected child exit.
if ((BASH_VERSINFO[0] > 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] >= 3))); then
  while true; do
    set +e
    wait -n
    status=$?
    set -e
    [ "$signal_received" -eq 1 ] && break
    on_child_exit "$status"
  done
else
  # Portable equivalent. bash reaps finished children on its own between
  # commands, so `kill -0` failing means a real exit rather than a zombie. A
  # status of 127 means bash discarded it, which is still a failed preview, not
  # a success — reporting otherwise is the exact bug this loop exists to catch.
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
