"""The Render launchers must stay the production entry points they are wired to.

`scripts/start_render.sh` is still the start command on the existing Render API
service, so a change to these three scripts changes what production actually
runs. The properties asserted here are the ones whose loss is silent:

* a launcher that starts the server *before* validating the environment accepts
  traffic it should never have served,
* a migration reintroduced into a start command races every replica on DDL, and
* logging a connection string writes a live database credential into retained,
  searchable output.

The scripts are read as text rather than executed. Booting uvicorn or a job
consumer is not something a unit test may do, and the properties being asserted
are statements about the file's own ordering and contents, which a dry run would
not prove anyway.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
START_API = REPO_ROOT / "scripts" / "start_render_api.sh"
START_WORKER = REPO_ROOT / "scripts" / "start_render_worker.sh"
START_SHIM = REPO_ROOT / "scripts" / "start_render.sh"
PREFLIGHT = REPO_ROOT / "scripts" / "preflight_production_env.py"
NEXT_CONFIG = REPO_ROOT / "frontend" / "next.config.ts"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _code_lines(text: str) -> list[str]:
    """Lines that a shell actually executes: no comments, no blank lines.

    Comments are stripped because nearly every property below is documented in
    prose *at length* right next to the code, and a warning that names the thing
    it prevents must not register as a commit of it.
    """
    lines = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(stripped.split(" #", 1)[0])
    return lines


def _line_index(code: list[str], needle: str) -> int:
    for index, line in enumerate(code):
        if needle in line:
            return index
    raise AssertionError(f"no executable line containing {needle!r}")


# ---------------------------------------------------------------------------
# The preflight runs before the process, not after
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("script", "entry_point"),
    [
        (START_API, "uvicorn"),
        (START_WORKER, "scripts.worker"),
    ],
    ids=["api", "worker"],
)
def test_preflight_runs_before_the_process_starts(script: Path, entry_point: str) -> None:
    """The gate has to be first, or it gates nothing.

    uvicorn binds and serves before the first request is handled, so a
    preflight placed after it would have let the process accept traffic and only
    then discover a missing encryption key. The worker is the same argument with
    a job consumer: it can claim and act on a job before validating anything.
    """
    code = _code_lines(_source(script))
    preflight = _line_index(code, str(PREFLIGHT.name))
    process = _line_index(code, entry_point)
    assert preflight < process, (
        f"{script.name} starts {entry_point} before running the preflight"
    )


@pytest.mark.parametrize("script", [START_API, START_WORKER], ids=["api", "worker"])
def test_a_failed_preflight_stops_startup(script: Path) -> None:
    """`if ! preflight` alone is not enough; the failure must be fatal.

    Under `set -eu` a bare `preflight` would also abort, so both forms are
    accepted here. What is rejected is running it and continuing regardless --
    the shape that turns a failed gate into a log line.
    """
    code = _code_lines(_source(script))
    branch = _line_index(code, PREFLIGHT.name)
    # The branch is `if ! <preflight>; then`, so the condition really is negated.
    assert code[branch].startswith("if ! "), (
        f"{script.name} runs the preflight without branching on its exit status: "
        f"{code[branch]!r}"
    )
    # ...and something inside the branch terminates the script before any
    # exec of the long-lived process.
    body = []
    for line in code[branch + 1 :]:
        if line == "fi":
            break
        body.append(line)
    assert any(re.search(r"\bexit\s+[1-9]", line) for line in body), (
        f"{script.name} does not exit non-zero when the preflight fails"
    )
    assert not any("exit 0" in line for line in body), (
        f"{script.name} treats a preflight failure as success"
    )


def test_preflight_is_the_shared_production_gate() -> None:
    """Both launchers must call the same script, not two similar ones.

    Duplicated checks are the failure mode this repository has already paid for
    once: a policy that was tightened in the advisory validator and left alone in
    the startup gate.
    """
    for script in (START_API, START_WORKER):
        assert PREFLIGHT.name in _source(script), f"{script.name} runs a different gate"


# ---------------------------------------------------------------------------
# Migrations are never a boot step
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "script", [START_API, START_WORKER, START_SHIM], ids=["api", "worker", "shim"]
)
def test_no_render_launcher_runs_migrations(script: Path) -> None:
    """Every replica re-runs its start command, so a migration here races itself.

    Concurrent `alembic upgrade head` calls contend on DDL locks and fail in ways
    that read like application bugs. Migration is a single explicit step,
    scripts/run_migrations.sh, run once per deploy before any code needing a new
    schema.
    """
    code = _code_lines(_source(script))
    assert not any("run_migrations" in line for line in code), (
        f"{script.name} invokes the migration step; that belongs in a one-off shell"
    )
    assert not any(re.search(r"alembic\s+upgrade", line) for line in code), (
        f"{script.name} runs alembic directly"
    )


def test_the_shim_does_not_start_a_worker() -> None:
    """A worker in every web replica multiplies job consumers.

    Job claiming is row-locked and so remains correct, but one consumer per
    replica is harder to reason about during an incident and costs more. The
    worker is its own service.
    """
    code = _code_lines(_source(START_SHIM))
    assert not any("scripts.worker" in line for line in code)
    assert not any("start_render_worker" in line for line in code), (
        "the compatibility shim starts a worker; the worker is a separate service"
    )


# ---------------------------------------------------------------------------
# The shim delegates, and delegates only
# ---------------------------------------------------------------------------


def test_the_shim_delegates_to_the_api_launcher() -> None:
    """The existing Render service points at this path, so it must still work."""
    code = _code_lines(_source(START_SHIM))
    assert any("start_render_api.sh" in line for line in code), (
        "the shim does not reference the API launcher"
    )


def test_the_shim_carries_no_api_startup_logic() -> None:
    """One copy of the API start command, not two that can disagree.

    Everything that matters about how the API starts -- the preflight ordering,
    the forwarded-header flags, the keep-alive setting -- lives in
    start_render_api.sh. A shim that also spelled out the uvicorn invocation would
    be a second thing to keep in step with the first.
    """
    code = "\n".join(_code_lines(_source(START_SHIM)))
    for owned_by_the_api_launcher in (
        "uvicorn",
        "--proxy-headers",
        "--forwarded-allow-ips",
        "--timeout-keep-alive",
        "preflight_production_env.py",
    ):
        assert owned_by_the_api_launcher not in code, (
            f"the shim duplicates API startup logic: {owned_by_the_api_launcher}"
        )


def test_the_shim_execs_rather_than_forking_a_layer() -> None:
    """exec keeps the signal path direct, which is the point of the shim."""
    assert any(
        line.startswith("exec") for line in _code_lines(_source(START_SHIM))
    ), "the shim should exec the API launcher so SIGTERM reaches uvicorn directly"


# ---------------------------------------------------------------------------
# API specifics
# ---------------------------------------------------------------------------


def test_api_binds_the_platform_port_on_all_interfaces() -> None:
    """Render routes to the container, so it cannot bind loopback."""
    code = "\n".join(_code_lines(_source(START_API)))
    assert "--host 0.0.0.0" in code
    assert '"${PORT:-10000}"' in code, "the platform PORT must not be hardcoded"


def test_api_trusts_forwarded_headers_for_tls_termination() -> None:
    """Render terminates TLS and forwards; without this the app believes http.

    Behind that termination an app that ignores forwarded headers builds http://
    URLs and marks cookies insecure, which breaks redirects and staff sign-in
    while the service looks healthy.
    """
    code = "\n".join(_code_lines(_source(START_API)))
    assert "--proxy-headers" in code, "uvicorn must trust Render's proxy"
    assert "--forwarded-allow-ips" in code, (
        "the trusted proxy range must be configurable; the Render default is '*'"
    )


def test_api_keeps_alive_configuration() -> None:
    """Idle keep-alive is what lets a proxy hold a reusable connection."""
    assert "--timeout-keep-alive" in _source(START_API)


def test_api_starts_the_expected_application() -> None:
    code = "\n".join(_code_lines(_source(START_API)))
    assert "app.main:app" in code
    assert any(line.startswith("exec") for line in _code_lines(_source(START_API)))


def test_render_launchers_are_not_marked_deprecated() -> None:
    """These are the production entry points; a deprecation banner misleads.

    The Replit launchers remain in the tree as compatibility files, which is
    what previously carried the "use the other one instead" banner here. That
    guidance was correct for the previous topology and is now wrong.
    """
    for script in (START_API, START_WORKER, START_SHIM):
        head = "\n".join(_source(script).splitlines()[:12])
        assert "DEPRECATED" not in head, f"{script.name} still announces itself deprecated"
        assert "superseded" not in head.lower(), f"{script.name} still defers to another platform"


# ---------------------------------------------------------------------------
# Worker specifics
# ---------------------------------------------------------------------------


def test_worker_requires_no_http_port() -> None:
    """It serves no traffic, so a port requirement or health check is wrong."""
    text = _source(START_WORKER)
    assert "--port" not in text
    assert "PORT" not in text.replace("WORKER_METRICS_PORT", ""), (
        "the worker must not depend on a platform port"
    )


def test_worker_execs_the_job_consumer() -> None:
    code = _code_lines(_source(START_WORKER))
    assert "python -m scripts.worker" in "\n".join(code)
    assert any(line.startswith("exec") for line in code), (
        "the worker should be exec'd so SIGTERM reaches it directly"
    )


# ---------------------------------------------------------------------------
# Secrets are never logged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "script", [START_API, START_WORKER, START_SHIM], ids=["api", "worker", "shim"]
)
def test_no_render_launcher_logs_a_secret_value(script: Path) -> None:
    """A connection string carries the database password.

    Output is retained, searchable, and shipped wherever the platform collects
    it, so printing one writes a live credential somewhere it cannot be revoked
    from. A logger may name a variable; it may not expand one.
    """
    for line in _source(script).splitlines():
        stripped = line.strip()
        if not stripped.startswith(("echo", "printf", "log")):
            continue
        for secret in (
            "DATABASE_URL",
            "ENCRYPTION_KEYS",
            "OPENAI_API_KEY",
            "AUTH_JWKS_URL",
            "AUTH_JWT_ISSUER",
        ):
            assert not re.search(rf'\$\{{?{secret}', stripped), (
                f"{script.name} expands {secret} into its output: {stripped!r}"
            )


def test_no_render_launcher_dumps_its_environment() -> None:
    """`env`/`set`/`printenv` in a launcher would emit every secret at once."""
    for script in (START_API, START_WORKER, START_SHIM):
        code = "\n".join(_code_lines(_source(script)))
        for dangerous in ("printenv", "set -x", "env |"):
            assert dangerous not in code, f"{script.name} prints its environment via {dangerous!r}"


# ---------------------------------------------------------------------------
# Frontend routing
# ---------------------------------------------------------------------------


def test_backend_api_url_stays_runtime_configurable() -> None:
    """It must be read at request time from the environment.

    Next.js server code reads process.env per request, so one built image can be
    pointed at a different API without a rebuild. Baking the value in at build
    time would couple the frontend release to the API's address.
    """
    source = _source(NEXT_CONFIG)
    assert "process.env.BACKEND_API_URL" in source, (
        "BACKEND_API_URL must remain a runtime environment read"
    )


def test_next_config_does_not_claim_the_backend_is_loopback_only() -> None:
    """The API is a separate Render service on the public https origin.

    A comment asserting loopback-only would be read as a security property
    someone could rely on, and it would justify dropping BACKEND_API_URL as
    "unnecessary on this topology".
    """
    source = _source(NEXT_CONFIG)
    assert "not required\n * to be https" not in source
    for stale in ("never leaves the machine", "same VM", "shared origin as the API"):
        assert stale not in source, f"next.config.ts still claims {stale!r}"


def test_next_config_still_proxies_the_backend_probes() -> None:
    """The frontend origin must keep re-exporting /health, /ready, /version.

    Unchanged behaviour, asserted so a topology edit cannot quietly drop it:
    these are the probes an operator and the readiness script use.
    """
    source = _source(NEXT_CONFIG)
    for probe in ("/health", "/ready", "/version"):
        assert probe in source, f"the backend probe {probe} is no longer proxied"
    assert "rewrites" in source


def test_next_config_security_headers_are_unchanged() -> None:
    """Header and framing behaviour must not shift in this task."""
    source = _source(NEXT_CONFIG)
    for header in (
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
        "Permissions-Policy",
        "Content-Security-Policy",
        "frame-ancestors",
        "a1cashforcars.com.au",
    ):
        assert header in source, f"{header} disappeared from next.config.ts"