#!/usr/bin/env bash
# CXOps web/API process for a Replit Reserved VM deployment.
#
# THIS IS THE RUN PHASE. THE FRONTEND IS ALREADY BUILT AND PYTHON IS ALREADY
# INSTALLED.
#
# `[deployment] build` runs scripts/build_replit_web.sh once, before the machine
# starts, and this script is `[deployment] run`. It installs nothing and builds
# nothing: `npm ci`, `next build` and a `pip install` are minutes of work that
# used to happen on every restart, before anything listened on the published
# port, which is how a healthy deployment gets reported as `an open port was not
# detected`. The two artifacts that work produced are checked here, and a missing
# one fails in a second with a message naming the build phase.
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
readonly STANDALONE_SERVER="$REPO_ROOT/$FRONTEND_DIR/.next/standalone/server.js"

# requirements.txt is installed by the build phase into this directory, and this
# is where the run phase expects it. The name is written once here and once in the
# build script; a rename on one side is a startup failure on the other, and the
# message below says which phase to look at rather than leaving an ImportError to
# be interpreted.
readonly PY_DEPS_DIRNAME=".replit-python"
readonly PY_DEPS_DIR="$REPO_ROOT/$PY_DEPS_DIRNAME"

log() { printf '%s replit-web %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
fail() { log "ERROR: $*" >&2; exit 1; }

# --- 1. The prebuilt frontend ---------------------------------------------------
#
# Checked first, before anything is imported or validated, because it is the one
# failure this script can name exactly and completely. A missing artifact means
# the build phase did not run or did not finish, and the fix is to republish --
# not to change any environment variable. The message says so, because the
# previous behaviour (build at start) made "start it again" look like the remedy.
[ -f "$STANDALONE_SERVER" ] \
  || fail "no prebuilt frontend at $STANDALONE_SERVER; the [deployment] build phase did not complete -- run scripts/build_replit_web.sh and republish, do not expect this script to build it"

# --- 2. The packaged Python dependencies -----------------------------------------
#
# The build phase installed requirements.txt with the system interpreter and
# `pip install --target`, so the packages are plain files in a directory rather
# than a virtual environment. Importing them is therefore PYTHONPATH and nothing
# else: the directory is prepended ahead of the repository root so an installed
# package wins over anything of the same name in the image, and the root follows
# because the project itself is imported from it (`app`, `scripts`).
#
# It is exported once, here, rather than prefixed onto each command: uvicorn, the
# preflight and the import check below are three separate interpreters, and a
# PYTHONPATH that has to be repeated in three places is one that will eventually
# be missing from the one place it mattered.
#
# The ambient value is appended, not prepended, so it is the least trusted of the
# three entries. `.replit [env]` does set PYTHONPATH="." -- the repository root,
# which this script has already added explicitly -- so appending is a no-op for
# the value that is actually configured here, and a guarantee against a future
# ambient value outranking either entry.
export PYTHONPATH="$PY_DEPS_DIR:$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

# The directory has to exist. A missing one means the build phase did not run or
# did not finish, and the fix is to republish -- not to install anything here,
# which is the slow path this split removed. Checking the directory itself, before
# any interpreter starts, is what makes that a one-line diagnosis: without it, the
# first import below reports a missing package and reads as a broken build rather
# than a build that never happened.
#
# Note that this is the same assumption the standalone-frontend check above makes:
# that the build phase's filesystem is the deployment's filesystem, so what it
# wrote is still there. If that does not hold on the platform, it does not hold for
# either artifact -- which is why both checks name the same remedy instead of
# offering a per-artifact workaround.
[ -d "$PY_DEPS_DIR" ] \
  || fail "no Python packages at $PY_DEPS_DIR; the [deployment] build phase did not complete -- run scripts/build_replit_web.sh and republish, do not expect this script to install them"

# The build already verified these import, with PYTHONPATH pointing at the target
# and nothing else. This repeats the check because the two environments are
# different -- the build's interpreter and this one are both "python3" from the
# same image, but the run phase is where an unusable install would otherwise
# surface -- and because it is cheap: importing these four takes well under a
# second. `app` proves the repository-root half of the PYTHONPATH. Importing
# `app.main` is NOT done here: it takes seconds, and the preflight below is the
# authoritative check that the application loads.
#
# The names are reported, so a missing package is a one-line fix rather than a
# ModuleNotFoundError traceback from inside uvicorn's import chain.
missing_python_packages() {
  "$PYTHON_BIN" - <<'PY' 2>/dev/null || true
import importlib

missing = []
for name in ("uvicorn", "fastapi", "sqlalchemy", "app"):
    try:
        importlib.import_module(name)
    except ImportError as exc:
        missing.append(exc.name or name)
print(" ".join(missing))
PY
}

missing="$(missing_python_packages)"
if [ -n "$missing" ]; then
  fail "the packaged Python dependencies in $PY_DEPS_DIRNAME/ are missing ${missing}; the build phase installed them, so this is a stale or partial build -- republish"
fi
log "python dependencies present ($("$PYTHON_BIN" -V 2>&1), from $PY_DEPS_DIRNAME/)"

# --- 3. Fail fast on configuration ----------------------------------------------
#
# A crash loop is the desired outcome for a misconfigured production process: it
# is visible and bounded, and it cannot serve a customer. This has bitten before
# - a service with ENVIRONMENT=production and a non-https FRONTEND_BASE_URL
# restarted forever while the previously healthy instance kept answering.
#
# Before either server starts, so that a process which starts and *then* discovers
# it has no encryption key has not already accepted traffic. The preflight also
# imports the project's third-party dependencies, so running it is also the
# authoritative check that the image's packages are the versions the code
# expects.
if ! "$PYTHON_BIN" scripts/preflight_production_env.py; then
  fail "environment preflight failed; refusing to start"
fi

# CXOPS_PUBLIC_SITE_URL is inlined into the bundle by the build phase
# (next/robots/sitemap), so it has to be present *now*, not merely at runtime, or
# the deployed site advertises localhost as its canonical origin to every
# crawler. The build phase requires it too; repeating the check here means a
# deployment cannot start with a public origin that differs from the one the
# bundle was built with, which would be a silent split between the two.
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

# The system interpreter, and the packaged dependencies from the build phase via
# the exported PYTHONPATH. There is no venv to activate and no interpreter other
# than the image's to choose: the packages are importable files, so the only
# requirement is that the interpreter can see them.
"$PYTHON_BIN" -m uvicorn app.main:app \
  --host 127.0.0.1 \
  --port "$INTERNAL_API_PORT" \
  --proxy-headers \
  --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}" \
  --timeout-keep-alive "${KEEP_ALIVE_SECONDS:-30}" \
  --no-server-header &
api_pid=$!

# NODE_ENV is set on the server process rather than exported project-wide, for
# the reason given in scripts/build_replit_web.sh: `next dev` under
# NODE_ENV=production does not start, and the workspace Run command needs it off.
PORT="$PUBLIC_PORT" HOSTNAME=0.0.0.0 NODE_ENV=production \
  node "$STANDALONE_SERVER" &
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
