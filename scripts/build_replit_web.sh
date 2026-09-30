#!/usr/bin/env bash
# CXOps frontend BUILD phase for a Replit Reserved VM deployment.
#
# WHY THE BUILD IS A SEPARATE STEP
#
# Replit runs `[deployment] build` once, before the deployment starts, and then
# `[deployment] run` on every start of the resulting machine. This script owns
# the first half; scripts/start_replit_web.sh owns the second.
#
# Before this split, one script did both: it installed Python packages, built
# the frontend, and then started uvicorn and Next.js. Every restart of the machine
# therefore re-ran `npm ci` and a full `next build` -- tens of seconds to minutes
# -- before anything listened on the published port. Replit's health check
# gives a fresh deployment a short window to open that port, so a slow start is
# reported as `hostingpid1: an open port was not detected` even though the build
# eventually succeeded. The build is the correct home for that work precisely
# because it is the work that does not need to repeat on every restart.
#
# WHY THIS SCRIPT NEEDS NO SECRETS
#
# The variables required below are the ones `next build` inlines into the
# bundle. They are public configuration, not credentials:
#
#   * CXOPS_PUBLIC_SITE_URL       - the canonical origin, prerendered into
#                                  src/app/robots.ts and src/app/sitemap.ts
#   * NEXT_PUBLIC_NHOST_SUBDOMAIN - read at module scope in
#   * NEXT_PUBLIC_NHOST_REGION      src/lib/nhost/server.ts
#
# All three are `NEXT_PUBLIC_*`-style or origin values that are visible in the
# served HTML and the client bundle by construction, so baking them in leaks
# nothing. Nothing sensitive is required: DATABASE_URL, ENCRYPTION_KEYS and
# OPENAI_API_KEY are runtime concerns, checked by scripts/preflight_production_env.py,
# and a build step that demanded them would fail for a completely correct
# configuration.
#
# Note that Replit DOES expose deployment secrets to the build command -- the
# Publishing tool's "deployment secrets" list is documented as the place for the
# "environment variables or secrets your build command needs to run securely".
# The reason this script asks only for public values is that it does not NEED
# more, not that more is unavailable.
#
# This script installs nothing at RUN time, and the deployment image's Python
# packages are not something the platform promises to contain: Replit's own
# `packager.features.enabledForHosting` defaults to false, so a hosting install of
# requirements.txt is not a given. The two obvious shortcuts are both refused.
# `packager.features.enabledForHosting` is not enabled here because it is a
# platform setting this repository does not own and does not control per
# deployment, and because a runtime install is what this split exists to remove.
# A `.venv` is not created and persisted either: it is a build artifact that the
# run phase would have to trust, and its `bin/` layout is not importable from the
# system interpreter.
#
# So the Python dependencies are installed HERE, into a project-local directory,
# as plain importable packages. That is the one shape that needs no venv, no
# console scripts, and no PATH manipulation: the run phase puts the directory on
# PYTHONPATH and runs the system interpreter. `pip install --target` is exactly
# that shape.
set -euo pipefail

readonly REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

readonly FRONTEND_DIR="${FRONTEND_DIR:-frontend}"
readonly NEXT_DIR="$REPO_ROOT/$FRONTEND_DIR/.next"
readonly STANDALONE_DIR="$NEXT_DIR/standalone"
readonly PUBLIC_DIR="$REPO_ROOT/$FRONTEND_DIR/public"

# Where requirements.txt is installed, and where scripts/start_replit_web.sh
# looks for it. A dotted, repo-root name so it is unmistakably a build artifact
# and not source; it is a directory of installed packages, not a venv, and there
# is no bin/ to put on PATH.
readonly PY_DEPS_DIRNAME=".replit-python"
readonly PY_DEPS_DIR="$REPO_ROOT/$PY_DEPS_DIRNAME"

log() { printf '%s replit-web-build %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
fail() { log "ERROR: $*" >&2; exit 1; }

# --- 1. Public build configuration --------------------------------------------
#
# Required, not defaulted. `next build` inlines these into the output, so a value
# supplied only at run time has no effect: robots.txt and sitemap.xml would
# advertise http://localhost:PORT as the canonical origin, and every staff
# session would fail in createNhostServerClient with NhostConfigurationError.
# Failing here names the cause; failing later looks like a broken frontend.
#
# Indirect expansion (`${!name:-}`) so the list of names is written once, and a
# variable added here is checked the same way as the rest. The `:-` is required
# under `set -u`: a bare `${!name}` would abort with an unbound-variable error
# that names the shell, not the setting.
for name in CXOPS_PUBLIC_SITE_URL NEXT_PUBLIC_NHOST_SUBDOMAIN NEXT_PUBLIC_NHOST_REGION; do
  if [ -z "${!name:-}" ]; then
    fail "$name is not set; next build inlines it, so it cannot be supplied at run time"
  fi
done

# A non-https canonical origin is a configuration fault, and it is a fault of the
# same shape the production preflight rejects for FRONTEND_BASE_URL: the value
# parses, the deployment starts, and the only symptom is a site telling every
# crawler and every tenant that its own origin is plaintext. The path and query
# are stripped before the message so a value with a token in it cannot reach the
# build log.
case "$CXOPS_PUBLIC_SITE_URL" in
  https://*) ;;
  *) fail "CXOPS_PUBLIC_SITE_URL must be https (got ${CXOPS_PUBLIC_SITE_URL%%\?*})" ;;
esac

# NODE_ENV is set per-process rather than in `.replit [env]`. A project-wide
# NODE_ENV=production also applied to the workspace's development run, and
# `next dev` under NODE_ENV=production fails to start; taking it out of [env]
# meant the build stopped inheriting it, so it is set explicitly here.
export NEXT_TELEMETRY_DISABLED=1
export NODE_ENV=production

# --- 2. Python backend dependencies --------------------------------------------
#
# Installed with the system interpreter and `--target`, so the result is a
# directory of plain packages rather than a virtual environment. Three
# properties of that choice are load-bearing, and each replaces something that
# was a failure mode before:
#
#   * no .venv, so the run phase does not have to trust a build artifact's
#     bin/ layout or a shebang baked with a path from build time. `python3 -m
#     uvicorn` with the directory on PYTHONPATH is import resolution and nothing
#     else.
#   * deterministic, because the target is removed first. A stale package from a
#     previous build is exactly the kind of thing that makes a fix appear not to
#     work, and it is invisible in the log.
#   * pip configuration is overridden explicitly, per command, for the same
#     reason the worker pins PIP_USER=false: Replit injects `PIP_USER=true` into
#     the environment, and a user install here would put packages somewhere this
#     process does not control. PIP_REQUIRE_VIRTUALENV=false is pinned for the
#     same class of reason: an ambient true would refuse an install that is
#     deliberately outside a venv.
#
# Console scripts are not installed onto PATH and are not needed: every entry
# point in this repository is `python -m <module>`. Their absence is not an
# error, so the warning pip prints about it is suppressed to keep the build log
# to real problems.
log "installing Python dependencies from requirements.txt into $PY_DEPS_DIRNAME/"
rm -rf "$PY_DEPS_DIR"
mkdir -p "$PY_DEPS_DIR"
env PIP_USER=false PIP_REQUIRE_VIRTUALENV=false \
  python3 -m pip install \
    --disable-pip-version-check \
    --no-input \
    --no-warn-script-location \
    --target "$PY_DEPS_DIR" \
    -r requirements.txt \
  || fail "pip install --target $PY_DEPS_DIRNAME/ failed; the resolver output above names the cause"

# The install's own exit code says the packages were downloaded and unpacked, not
# that they import. `--target` installs flat, so an import can still fail on a
# package that needs a `.pth` file processed at install time, and that failure
# would otherwise surface at run time as a module the run phase believes is
# present. So the packages this deployment actually starts with are imported here,
# with PYTHONPATH pointing at the target and nothing else -- a stray ambient
# PYTHONPATH must not be able to satisfy the check out of the image's own
# site-packages and hide a broken install.
#
# The list is the third-party packages the deployment cannot start without -- the
# API server, its ORM and driver, its settings loader, its logger -- and nothing
# else. `app` is deliberately absent: the project is not installed as a
# distribution, so it is imported from the repository root, and that is a fact
# about the checkout rather than about the install. `app.main` is absent for a
# second reason: it takes seconds to import, and the authoritative check that the
# application loads is the preflight at run time, in the environment that actually
# has the secrets. The run phase's own check covers `app` and the server, and it
# has to run either way.
missing_python_packages() {
  PYTHONPATH="$PY_DEPS_DIR" python3 - <<'PY' 2>/dev/null || true
import importlib

missing = []
for name in ("uvicorn", "fastapi", "sqlalchemy", "asyncpg", "pydantic_settings", "structlog"):
    try:
        importlib.import_module(name)
    except ImportError as exc:
        missing.append(exc.name or name)
print(" ".join(missing))
PY
}

missing="$(missing_python_packages)"
if [ -n "$missing" ]; then
  fail "requirements.txt installed but ${missing} will not import from $PY_DEPS_DIRNAME/; the run phase would start and then fail on a missing package"
fi
log "python dependencies installed and importable ($(python3 -V 2>&1), target $PY_DEPS_DIRNAME/)"

# --- 3. Install and build the frontend -----------------------------------------
#
# `npm ci` rather than `npm install`: it installs the lockfile exactly, and it
# fails outright when package.json and package-lock.json disagree -- which is a
# hard error at install time and therefore a build that never starts.
#
# --include=dev is explicit because devDependencies are required here, not
# optional: `next build` runs the TypeScript compiler and the PostCSS toolchain,
# and a production-only install produces a build that fails on a missing binary.
log "installing Node dependencies"
( cd "$FRONTEND_DIR" && npm ci --include=dev ) \
  || fail "npm ci failed in $FRONTEND_DIR; the lockfile and manifest disagree, or the registry was unreachable"

log "building the Next.js production bundle"
( cd "$FRONTEND_DIR" && npm run build ) \
  || fail "next build failed; the compiler output above names the cause"

# --- 4. Verify and stage the standalone artifact -------------------------------
#
# next.config.ts sets output: "standalone", which emits a self-contained server
# with its own traced node_modules subtree. That is what lets the run phase skip
# npm entirely: the artifact carries the server and the libraries it needs, not
# the build toolchain.
[ -f "$STANDALONE_DIR/server.js" ] \
  || fail "next build did not produce $STANDALONE_DIR/server.js; check that output is not set to something other than 'standalone'"

# Standalone emits only the server and its traced dependencies. It deliberately
# does NOT copy .next/static or public/, because a container image is expected
# to copy them itself. Running server.js without doing that copy is the classic
# result: HTML renders, every /_next/static/*.js 404s, and the app is a blank
# page -- so this is verified here, at build time, rather than discovered by a
# customer.
#
# The destinations are removed first because `cp -R src dst` nests when dst
# already exists, turning .next/static into .next/static/static on any build that
# did not start from a clean .next.
log "staging static assets into the standalone server"
rm -rf "$STANDALONE_DIR/.next/static"
cp -R "$NEXT_DIR/static" "$STANDALONE_DIR/.next/static" \
  || fail "could not stage .next/static into the standalone output"

if [ -d "$PUBLIC_DIR" ]; then
  rm -rf "$STANDALONE_DIR/public"
  cp -R "$PUBLIC_DIR" "$STANDALONE_DIR/public" \
    || fail "could not stage public/ into the standalone output"
  log "staged public/ ($(find "$STANDALONE_DIR/public" -type f | wc -l | tr -d ' ') file(s))"
fi

# The run phase looks for exactly this path, so a rename on one side is a
# startup failure on the other. Asserting it here names the build as the cause.
[ -f "$STANDALONE_DIR/server.js" ] \
  || fail "the standalone server is missing after staging; the artifact is unusable"

log "build complete: ${STANDALONE_DIR#"$REPO_ROOT"/}/server.js"
log "build complete: python packages in $PY_DEPS_DIRNAME/, run phase prepends it to PYTHONPATH"
log "run phase: scripts/start_replit_web.sh"
