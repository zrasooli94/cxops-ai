"""Render blueprint must stay in step with what the app actually requires.

`render.yaml` duplicates the web and worker environment by hand. That
duplication is necessary -- the worker imports `app.core.config` and crashes
rather than degrading -- but it is also a place where a new required setting
can be added to the app and silently omitted from the blueprint. The result is a
crash loop in production that no local test would have caught.

The checks here are structural: they do not need credentials, and they fail on
drift rather than on a missing secret, because a missing secret is an operator
action, not a code defect.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RENDER_YAML = REPO_ROOT / "render.yaml"
START_API = REPO_ROOT / "scripts" / "start_render_api.sh"
START_WORKER = REPO_ROOT / "scripts" / "start_render_worker.sh"
MIGRATE_SH = REPO_ROOT / "scripts" / "run_migrations.sh"

RENDER_SOURCE = RENDER_YAML.read_text(encoding="utf-8")


def _service_block(name: str) -> str:
    """Return the YAML block for one named service."""
    start = RENDER_SOURCE.index(f"name: {name}")
    end = RENDER_SOURCE.find("  - type:", start)
    return RENDER_SOURCE[start : end if end != -1 else len(RENDER_SOURCE)]


WEB_BLOCK = _service_block("cxops-api")
WORKER_BLOCK = _service_block("cxops-worker")


def _keys(block: str) -> set[str]:
    return set(re.findall(r"- key: ([A-Z0-9_]+)", block))


WEB_KEYS = _keys(WEB_BLOCK)
WORKER_KEYS = _keys(WORKER_BLOCK)


# ---------------------------------------------------------------------------
# Shared production posture
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key,value",
    [
        ("ENVIRONMENT", "production"),
        ("DEBUG", "false"),
        ("PYTHONPATH", "/app"),
        ("AUTH_MODE", "jwks"),
        ("AUTH_DEV_MODE", "false"),
    ],
)
@pytest.mark.parametrize("block_name", ["cxops-api", "cxops-worker"])
def test_every_service_declares_the_production_posture(
    key: str, value: str, block_name: str
) -> None:
    block = _service_block(block_name)
    # - key: X\n<indent>value: "value"
    pattern = rf"- key: {key}\s*\n\s*value: {json_quote(value)}"
    assert re.search(pattern, block), f"{block_name} must set {key}={value}"


def json_quote(value: str) -> str:
    return rf'"?{re.escape(value)}"?'


@pytest.mark.parametrize("block_name", ["cxops-api", "cxops-worker"])
def test_every_service_binds_the_database(block_name: str) -> None:
    """A service without DATABASE_URL cannot reach PostgreSQL at all."""
    block = _service_block(block_name)
    assert "fromDatabase:" in block, f"{block_name} has no database binding"
    assert "name: cxops-db" in block


@pytest.mark.parametrize(
    "key",
    [
        "BACKEND_PUBLIC_URL",
        "FRONTEND_BASE_URL",
        "AUTH_JWKS_URL",
        "ENCRYPTION_KEYS",
        "OPENAI_API_KEY",
    ],
)
@pytest.mark.parametrize("block_name", ["cxops-api", "cxops-worker"])
def test_required_settings_are_present_and_operator_supplied(
    key: str, block_name: str
) -> None:
    """Each of these must exist in the blueprint and have no default value.

    `sync: false` means "operator sets it in the dashboard". A literal default
    would ship a placeholder to production -- for a webhook secret or an
    encryption key that is a silent outage, and for a working default key it is
    worse.
    """
    block = _service_block(block_name)
    pattern = rf"- key: {key}\s*\n\s*sync: false"
    assert re.search(
        pattern, block
    ), f"{block_name}.{key} must be declared with 'sync: false' and no default"


# ---------------------------------------------------------------------------
# Parity between web and worker
# ---------------------------------------------------------------------------


def test_web_and_worker_agree_on_security_critical_settings() -> None:
    """The worker must never be weaker than the web service.

    These four are the settings whose absence makes `app.core.config` refuse to
    import, so a missing one is a crash loop rather than a degraded feature.
    """
    shared = {"ENCRYPTION_KEYS", "OPENAI_API_KEY", "AUTH_JWKS_URL", "AUTH_MODE"}
    missing_from_worker = shared - WORKER_KEYS
    assert not missing_from_worker, (
        f"worker is missing {sorted(missing_from_worker)}; it would crash on "
        "import in production"
    )
    assert shared <= WEB_KEYS, f"web is missing {sorted(shared - WEB_KEYS)}"


def test_worker_does_not_try_to_serve_http() -> None:
    """FORWARDED_ALLOW_IPS and a health check belong to the web service only."""
    assert "FORWARDED_ALLOW_IPS" not in WORKER_KEYS
    assert "FORWARDED_ALLOW_IPS" in WEB_KEYS
    assert "healthCheckPath" not in WORKER_BLOCK
    assert "healthCheckPath: /ready" in WEB_BLOCK


def test_zendesk_oauth_stays_off_the_worker() -> None:
    """Deliberate asymmetry: the worker consumes jobs, it does not run OAuth."""
    for key in ("ZENDESK_CLIENT_ID", "ZENDESK_CLIENT_SECRET", "ZENDESK_REDIRECT_URI"):
        assert key in WEB_KEYS, key
        assert key not in WORKER_KEYS, (
            f"{key} on the worker grants an OAuth surface with no HTTP client"
        )


# ---------------------------------------------------------------------------
# Deployment safety properties
# ---------------------------------------------------------------------------


def test_no_service_runs_migrations_at_boot() -> None:
    """The core reason migrations are a separate step.

    Every web replica executes its start command. A migration there races across
    replicas and produces DDL errors that read like application bugs.

    Comments are stripped first: both start scripts *name* run_migrations.sh to
    tell operators not to run it, and a naive substring check would flag that
    warning as the very defect it exists to prevent. Only executable lines
    count.
    """
    for name, block in (("cxops-api", WEB_BLOCK), ("cxops-worker", WORKER_BLOCK)):
        assert "run_migrations" not in block, (
            f"{name} runs migrations at boot; every replica would race on DDL"
        )
    for script in (START_API, START_WORKER):
        executed = _executable_lines(script.read_text(encoding="utf-8"))
        assert not any("run_migrations" in line for line in executed), (
            f"{script.name} invokes the migration step; that belongs in a "
            "one-off shell or CI job"
        )


def _executable_lines(text: str) -> list[str]:
    """Return lines that are not comments or blank, with comments stripped.

    A trailing `# comment` is also removed so a note after a harmless command
    cannot register as a reference to the migration step.
    """
    lines = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(stripped.split(" #", 1)[0])
    return lines


def test_startup_scripts_actually_start_the_services() -> None:
    """The complementary check: the scripts are not empty shells.

    A start script that silently did nothing would pass every 'does not run
    migrations' assertion above, so assert the real entry points are present.
    """
    api = _executable_lines(START_API.read_text(encoding="utf-8"))
    assert any("uvicorn" in line for line in api), "API script does not start uvicorn"
    assert any("--host" in line for line in api), "API script does not bind a host"
    worker = _executable_lines(START_WORKER.read_text(encoding="utf-8"))
    assert any("python" in line for line in worker), "worker script runs nothing"


def test_database_is_not_reachable_from_the_public_internet() -> None:
    """An empty ipAllowList means internal-only on Render."""
    assert "ipAllowList: []" in RENDER_SOURCE


def test_pgvector_requirement_is_recorded() -> None:
    """knowledge_chunks.embedding needs pgvector; the DB must have it."""
    assert "pgvector" in RENDER_SOURCE
    assert 'postgresMajorVersion: "16"' in RENDER_SOURCE


def test_no_secret_has_a_literal_default() -> None:
    """Catch a placeholder that would ship to production."""
    block_start = RENDER_SOURCE.index("envVars:")
    for match in re.finditer(
        r"- key: ([A-Z0-9_]+)\s*\n\s*value:\s*(\S+)", RENDER_SOURCE[block_start:]
    ):
        key, value = match.group(1), match.group(2)
        if key in {"ENVIRONMENT", "DEBUG", "PYTHONPATH", "AUTH_MODE", "AUTH_DEV_MODE",
                   "FORWARDED_ALLOW_IPS"}:
            continue
        assert "sync" not in key
        assert not value.startswith(("changeme", "placeholder", "todo", "xxx")), (
            f"{key} has a placeholder default"
        )


def test_autodeploy_is_disabled_so_migrations_can_run_first() -> None:
    """autoDeploy would deploy the new code before its migration has run."""
    assert RENDER_SOURCE.count("autoDeploy: false") >= 2
    assert "autoDeploy: true" not in RENDER_SOURCE


def test_migration_step_is_documented_as_separate() -> None:
    text = MIGRATE_SH.read_text(encoding="utf-8")
    assert "Never run this from a service start command" in text
    assert "DATABASE_URL must be set" in text


def test_frontend_build_time_origin_is_not_expected_in_the_api_blueprint() -> None:
    """robots.ts/sitemap.ts read CXOPS_PUBLIC_SITE_URL at BUILD time.

    The API blueprint has no frontend build, so the variable does not belong
    here. It belongs to whichever system builds the frontend; recording that
    distinction prevents someone "fixing" the API blueprint to satisfy a
    metadata variable it can never affect.
    """
    assert "CXOPS_PUBLIC_SITE_URL" not in RENDER_SOURCE
