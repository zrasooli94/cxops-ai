# Replit system dependency layer (Phase 1P-Replit).
#
# Only the *system* packages live here: interpreters, a shell, and a client for
# the operational checks. Python and Node language dependencies are installed by
# the start scripts from requirements.txt and package-lock.json, so the versions
# that actually run are the ones pinned in the repository rather than whatever a
# base image happened to ship.
#
# Replit's nixpkgs is the source of these package names. If a future Replit
# upgrade renames one, the failure is loud at VM boot - the interpreter is
# missing and the start command cannot run - rather than a silent downgrade to
# an older Python quietly building a wheel for the wrong ABI.

{ pkgs }: {
  deps = [
    # Production Python.
    #
    # 3.12 matches pyproject.toml's requires-python (>=3.12,<3.13) and the
    # .python-version the local project uses. CI still runs 3.11, which is a
    # deliberate, documented difference: 3.12 is the floor the application
    # declares, and 3.11 CI proves the code does not *require* 3.12-only syntax.
    # Raising CI is deliberately not done in this milestone.
    pkgs.python312
    pkgs.python312Packages.pip

    # Frontend build and runtime. Node 22 matches the CI pin; an older default
    # must never silently build Next.js 16.
    pkgs.nodejs_22
    pkgs.nodejs_22Packages.npm

    # bash 5.3. start_replit_web.sh depends on `wait -n` (bash 4.3+) for its
    # failure-propagation guarantee, and on `set -E` for ERR inheritance.
    pkgs.bash

    # psql, for the documented pgvector verification step. The extension status
    # is also checked programmatically by scripts/validate_production_config.py,
    # which is the gate that matters; this is for an operator running the check
    # by hand during a migration.
    pkgs.postgresql_16

    # Manylinux wheels cover cryptography/asyncpg/psycopg, but a platform
    # without a matching wheel falls back to building from source and then needs
    # a compiler toolchain and OpenSSL headers. Including these costs image size
    # and removes a class of "works on my VM" build failure.
    pkgs.pkg-config
    pkgs.openssl

    # TLS roots. Without them, a container image reaches the database and the
    # model provider over https with no trust store, which fails as an
    # unhelpable certificate error.
    pkgs.cacert
  ];
}
