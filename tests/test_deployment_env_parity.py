"""The deployment env contract must stay in step with what the app requires.

`config/replit/deployment-env.yaml` states, per process, exactly which
environment variables a production deployment needs. That duplication is
necessary -- the worker imports `app.core.config` and crashes rather than
degrading -- but it is also a place where a new required setting can be added
to the app and silently omitted from the contract. The result is a crash loop
in production that no local test would have caught.

The checks here are structural: they do not need credentials, and they fail on
drift rather than on a missing secret, because a missing secret is an operator
action, not a code defect.

This file replaced `test_render_env_parity.py`, which asserted the same
properties against `render.yaml`. The blueprint it checked is now archived in
`docs/archive/`, and the invariants are unchanged -- only the source of truth
moved from a vendor-specific manifest to a vendor-neutral contract.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT = REPO_ROOT / "config" / "replit" / "deployment-env.yaml"
MIGRATE_SH = REPO_ROOT / "scripts" / "run_migrations.sh"
START_WEB = REPO_ROOT / "scripts" / "start_replit_web.sh"
START_WORKER = REPO_ROOT / "scripts" / "start_replit_worker.sh"
START_API = REPO_ROOT / "scripts" / "start_render_api.sh"
NEXT_CONFIG = REPO_ROOT / "frontend" / "next.config.ts"

WEB = "cxops-web"
WORKER = "cxops-worker"

DOCUMENT: dict[str, Any] = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
PROCESSES: dict[str, Any] = DOCUMENT["processes"]
WEB_KEYS = {entry["name"] for entry in PROCESSES[WEB]["required"]}
WORKER_KEYS = {entry["name"] for entry in PROCESSES[WORKER]["required"]}
WEB_REQUIRED = {
    entry["name"]: entry
    for entry in PROCESSES[WEB]["required"]
    if not entry.get("optional")
}
WORKER_REQUIRED = {
    entry["name"]: entry
    for entry in PROCESSES[WORKER]["required"]
    if not entry.get("optional")
}


def _tuning(process: str) -> dict[str, str]:
    return {
        entry["name"]: str(entry.get("default", ""))
        for entry in PROCESSES[process].get("optional_tuning", [])
    }


# ---------------------------------------------------------------------------
# Shared production posture
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("ENVIRONMENT", "production"),
        ("DEBUG", "false"),
        ("AUTH_MODE", "jwks"),
        ("AUTH_DEV_MODE", "false"),
    ],
)
@pytest.mark.parametrize("process", [WEB, WORKER])
def test_every_process_declares_the_production_posture(
    key: str, value: str, process: str
) -> None:
    """Production is only recognisable if every process says so.

    A worker left on the development default still claims jobs and still writes
    to the database, so it fails quietly rather than refusing to start.
    """
    entries = {e["name"]: e for e in PROCESSES[process]["required"]}
    assert key in entries, f"{process} does not declare {key}"
    assert str(entries[key].get("value")) == value, f"{process}.{key} != {value}"


@pytest.mark.parametrize("process", [WEB, WORKER])
def test_every_process_can_import_the_app(process: str) -> None:
    """PYTHONPATH is what makes `app` importable; without it the entry point dies."""
    entries = {e["name"]: e for e in PROCESSES[process]["required"]}
    assert str(entries["PYTHONPATH"]["value"]) == ".", (
        f"{process}.PYTHONPATH must be the repository root, not an absolute "
        "path that only existed on a previous platform's image layout"
    )


@pytest.mark.parametrize("process", [WEB, WORKER])
def test_every_process_binds_the_database(process: str) -> None:
    """A process without DATABASE_URL cannot reach PostgreSQL at all."""
    entries = {e["name"]: e for e in PROCESSES[process]["required"]}
    assert "DATABASE_URL" in entries, f"{process} has no database binding"
    assert entries["DATABASE_URL"].get("secret") is True, (
        "DATABASE_URL carries a password; it must be flagged secret so nobody "
        "is tempted to inline it"
    )
    assert "value" not in entries["DATABASE_URL"], (
        "DATABASE_URL must have no default: a fallback URL is how a deploy ends "
        "up writing to the wrong database"
    )


# ---------------------------------------------------------------------------
# Parity between web and worker
# ---------------------------------------------------------------------------


def test_web_and_worker_agree_on_security_critical_settings() -> None:
    """The worker must never be weaker than the web deployment.

    These are the settings whose absence makes `app.core.config` refuse to
    import, so a missing one is a crash loop rather than a degraded feature.
    """
    shared = {
        "ENCRYPTION_KEYS",
        "OPENAI_API_KEY",
        "AUTH_JWKS_URL",
        "AUTH_MODE",
        "FRONTEND_BASE_URL",
        "BACKEND_PUBLIC_URL",
    }
    missing_from_worker = shared - set(WORKER_REQUIRED)
    assert not missing_from_worker, (
        f"worker is missing {sorted(missing_from_worker)}; it would crash on "
        "import in production"
    )
    assert shared <= set(WEB_REQUIRED), f"web is missing {sorted(shared - WEB_REQUIRED)}"


def test_worker_does_not_try_to_serve_http() -> None:
    """FORWARDED_ALLOW_IPS is a web-deployment concern only.

    The worker publishes nothing. A worker that "trusted forward headers" would
    be a process with no HTTP surface trusting header values from nowhere.
    """
    assert "FORWARDED_ALLOW_IPS" not in WORKER_KEYS
    assert "FORWARDED_ALLOW_IPS" in _tuning(WEB)
    assert "INTERNAL_API_PORT" not in _tuning(WORKER)
    assert "INTERNAL_API_PORT" in _tuning(WEB)


def test_forwarded_allow_ips_default_is_not_a_wildcard() -> None:
    """Loopback-only API + trusted-everyone proxy headers is a spoofing hole.

    On this topology the API is bound to 127.0.0.1 and reached only by the
    frontend on the same machine. If the API also trusts X-Forwarded-Proto from
    any source, then anything that can open that socket can dictate the scheme
    the app believes it is served over -- which is exactly the input that
    decides whether a generated URL is http or https.
    """
    assert _tuning(WEB).get("FORWARDED_ALLOW_IPS") != "*", (
        "FORWARDED_ALLOW_IPS must default to a specific value, not '*'"
    )


def test_zendesk_oauth_stays_off_the_worker() -> None:
    """Deliberate asymmetry: the worker consumes jobs, it does not run OAuth."""
    for key in ("ZENDESK_CLIENT_ID", "ZENDESK_CLIENT_SECRET", "ZENDESK_REDIRECT_URI"):
        assert key in WEB_KEYS, key
        assert key not in WORKER_KEYS, (
            f"{key} on the worker grants an OAuth surface with no HTTP client"
        )


# ---------------------------------------------------------------------------
# Required-but-operator-supplied settings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["BACKEND_PUBLIC_URL", "AUTH_JWKS_URL", "ENCRYPTION_KEYS"])
@pytest.mark.parametrize("process", [WEB, WORKER])
def test_required_settings_have_no_literal_default(key: str, process: str) -> None:
    """Each of these must be declared and left for the operator to supply.

    A literal default would ship a placeholder to production -- for a webhook
    secret that is a silent outage, and for a working default encryption key it
    is far worse, because stored credentials would look protected while being
    decryptable by anyone holding the source.
    """
    entries = {e["name"]: e for e in PROCESSES[process]["required"]}
    assert key in entries, f"{process} does not declare {key}"
    assert entries[key].get("secret") is True, f"{process}.{key} must be marked secret"
    assert "value" not in entries[key], f"{process}.{key} must have no default value"


def test_disabled_integration_is_a_complete_configuration() -> None:
    """A missing optional secret must be documented, not merely tolerated.

    The ticket webhook is the one case where absent means 'the endpoint answers
    503' rather than 'the process crashes'. That is only a safe default if the
    contract says so, or an operator will eventually add the endpoint without
    its secret and get unsigned writes.
    """
    entry = {
        e["name"]: e
        for e in PROCESSES[WEB]["required"]
    }["TICKET_EVENT_WEBHOOK_SECRET"]
    assert entry.get("optional") is True
    assert "503" in entry["reason"], (
        "the disabled-webhook behaviour must be stated in the contract"
    )


def test_every_required_entry_explains_itself() -> None:
    """A required variable with no stated reason is the one nobody sets.

    This is the documentation that travels with the contract, so the runbook and
    the machine-readable file cannot drift into disagreeing about what matters.
    """
    for process in (WEB, WORKER):
        for entry in PROCESSES[process]["required"]:
            assert entry.get("reason"), f"{process}.{entry['name']} has no reason"


# ---------------------------------------------------------------------------
# Build-time vs runtime separation
# ---------------------------------------------------------------------------


def test_frontend_build_time_origin_is_required_by_web_only() -> None:
    """robots.ts/sitemap.ts read CXOPS_PUBLIC_SITE_URL at BUILD time.

    A runtime-only value produces a site that advertises http://localhost:3000
    to every crawler, and it fails silently: the site works, the metadata does
    not. So the web deployment must carry it, flagged as build-time, and the
    worker must not -- the worker runs no frontend build and therefore cannot
    be the place a metadata variable lives.
    """
    entry = {e["name"]: e for e in PROCESSES[WEB]["required"]}["CXOPS_PUBLIC_SITE_URL"]
    assert entry.get("build_time") is True
    assert "build" in entry["reason"].lower()
    assert "CXOPS_PUBLIC_SITE_URL" not in WORKER_KEYS


def test_internal_backend_url_is_runtime_only_and_may_be_loopback() -> None:
    """BACKEND_API_URL is a loopback address on this topology.

    It is consumed by the server-side BFF, never by the browser, so it is the
    one URL in the contract that is legitimately not https -- but it must be
    marked runtime-only so nobody expects to find it in a build artifact.
    """
    entry = {e["name"]: e for e in PROCESSES[WEB]["required"]}["BACKEND_API_URL"]
    assert entry.get("runtime_only") is True
    assert "not required to be https" in entry["reason"]


# ---------------------------------------------------------------------------
# Deployment safety properties
# ---------------------------------------------------------------------------


def test_no_process_runs_migrations_at_boot() -> None:
    """The core reason migrations are a separate step.

    Every restart re-runs the start command, so a migration there races itself
    and produces DDL errors that read like application bugs. This is the one
    inherited rule that applies identically to every platform, which is why it
    is asserted against the contract *and* against the scripts.
    """
    assert "run_migrations" not in str(PROCESSES[WEB]["required"])
    assert "run_migrations" not in str(PROCESSES[WORKER]["required"])
    for script in (START_WEB, START_WORKER, START_API):
        executed = _executable_lines(script.read_text(encoding="utf-8"))
        assert not any("run_migrations" in line for line in executed), (
            f"{script.name} invokes the migration step; that belongs in a "
            "one-off shell, not a boot"
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
    web = _executable_lines(START_WEB.read_text(encoding="utf-8"))
    assert any("uvicorn" in line for line in web), "web script does not start uvicorn"
    assert any("server.js" in line for line in web), "web script does not start Next.js"
    worker = _executable_lines(START_WORKER.read_text(encoding="utf-8"))
    assert any("python" in line for line in worker), "worker script runs nothing"
    assert any("scripts.worker" in line for line in worker), (
        "worker script does not exec the worker module"
    )


def test_api_is_bound_to_loopback_and_only_next_publishes() -> None:
    """The reason there is exactly one published port.

    FastAPI on 0.0.0.0 would make the API directly reachable and would split
    the origin, which then forces CORS onto a same-origin-by-design BFF. Binding
    it to loopback and letting only Next.js listen on the platform port keeps
    one origin and removes a whole class of cross-origin mistake.
    """
    web = START_WEB.read_text(encoding="utf-8")
    assert "127.0.0.1" in web, "the API must bind loopback"
    assert "--host 0.0.0.0" not in web, (
        "binding the API to 0.0.0.0 publishes it on the platform port and "
        "breaks the single-origin design"
    )


# ---------------------------------------------------------------------------
# Database requirements
# ---------------------------------------------------------------------------


def test_pgvector_requirement_is_recorded_with_a_recovery_step() -> None:
    """knowledge_chunks.embedding needs pgvector; the DB must have it.

    The requirement is not just "install pgvector" but "the database owner
    enables it", because the application's own role frequently cannot create an
    extension on a managed database. A contract that omits that step produces a
    deploy that reports success and an app that fails its first RAG query.
    """
    database = DOCUMENT["database"]
    extension = next(
        e for e in database["required_extensions"] if e["name"] == "vector"
    )
    assert "CREATE EXTENSION" in extension["manual_step"]
    assert "VECTOR(1536)" in extension["required_by"]
    assert database["minimum_major_version"] >= 12


def test_expected_migration_head_is_recorded() -> None:
    """A deploy is verifiable only if the expected head is written down."""
    assert DOCUMENT["database"]["migrations"]["expected_head"]


def test_migration_step_is_documented_as_separate() -> None:
    text = MIGRATE_SH.read_text(encoding="utf-8")
    assert "Never run this from a service start command" in text
    assert "DATABASE_URL must be set" in text


# ---------------------------------------------------------------------------
# The previous platform is archived, not authoritative
# ---------------------------------------------------------------------------


def test_render_blueprint_is_not_at_the_repository_root() -> None:
    """A blueprint at the root is applied by tooling; archived is read by humans.

    Leaving `render.yaml` in place would keep a second, divergent statement of
    the environment contract, and either platform's tooling would keep accepting
    a manifest that describes a topology nobody runs.
    """
    assert not (REPO_ROOT / "render.yaml").exists()
    assert (REPO_ROOT / "docs" / "archive" / "render.yaml").exists()


def test_archived_blueprint_is_marked_deprecated() -> None:
    archived = (REPO_ROOT / "docs" / "archive" / "render.yaml").read_text(
        encoding="utf-8"
    )
    head = archived.splitlines()[:5]
    assert any("DEPRECATED" in line for line in head), (
        "the archived blueprint must announce itself in its first lines"
    )


def test_no_test_or_runbook_treats_the_blueprint_as_current() -> None:
    """References may survive as history, but not as instructions."""
    for path in [
        REPO_ROOT / "docs" / "runbooks" / "replit-production-deployment.md",
        REPO_ROOT / "docs" / "runbooks" / "production-environment-contract.md",
    ]:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if "render.yaml" not in line:
                continue
            assert "docs/archive" in line or "archived" in line.lower(), (
                f"{path.name} references render.yaml as if it were current: {line.strip()}"
            )


# ---------------------------------------------------------------------------
# Widget framing
# ---------------------------------------------------------------------------


def _strip_js_comments(text: str) -> str:
    """Drop JS comments so prose about a rule cannot satisfy or break the rule.

    The reason this matters: the comment above WIDGET_FRAME_ANCESTORS has to
    *name* the wildcard it warns against, so a naive substring search for
    "replit.app" would fail on the very documentation that explains the
    invariant. Only real code is searched.

    The negative lookbehind on the line-comment pass is load-bearing, not
    decoration: every allowed origin in this file is an https:// URL, and a
    plain `//` strip would silently rewrite `"https://a1.example"` to
    `"https:` and make the policy look empty.
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return "\n".join(re.sub(r"(?<!:)//.*$", "", line) for line in text.splitlines())


def test_frame_ancestors_has_no_wildcard() -> None:
    """A `*.replit.app` wildcard would let any Replit user frame a tenant widget.

    Every Replit deployment gets a sibling subdomain of one shared parent, so a
    wildcard there is not a narrow exception -- it is every tenant on the
    platform, including an account created this afternoon. The widget carries a
    tenant's public key in its URL, so a hostile parent could read it and
    repoint the victim at a lookalike origin. Only exact custom domains belong
    in this list.
    """
    code = _strip_js_comments(NEXT_CONFIG.read_text(encoding="utf-8"))
    assert "replit.app" not in code, (
        "next.config.ts references a Replit host in code; frame-ancestors must "
        "be exact tenant domains only"
    )
    assert "*." not in code, "a wildcard appears in the frame/security policy"


def test_tenant_manifests_stay_the_source_of_truth_for_origins() -> None:
    """The hardcoded allowlist and the manifests must not silently disagree.

    The origins exist in two places by design -- the manifest is the app's copy
    and next.config.ts is the browser's -- so the risk is drift, not
    duplication. An origin that exists in one but not the other produces a
    widget that is either unusable or unexpectedly framable, and neither shows
    up in a test suite that reads only one file.
    """
    code = _strip_js_comments(NEXT_CONFIG.read_text(encoding="utf-8"))
    manifest = yaml.safe_load(
        (REPO_ROOT / "config" / "tenants" / "a1-cash-for-cars.yaml").read_text(
            encoding="utf-8"
        )
    )
    origins = manifest["public_chat"]["allowed_origins"]
    assert origins, "A1 declares no origins, so this test proves nothing"
    for origin in origins:
        assert f'"{origin}"' in code, (
            f"A1 origin {origin} is in the manifest but not in the frame policy"
        )
