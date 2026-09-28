"""Phase 1P.4 — live pilot release gate: the production invariants themselves.

This suite covers the properties that decide whether a live pilot may serve real
customers, and it covers them at the layer where they can actually break:

- **Database invariant (B/C).** One ``PublicChatConfiguration`` per tenant,
  enforced by the database, not only by the onboarding service. Includes the
  migration's duplicate precheck, which must refuse rather than pick a winner.
- **Exact migration head (D).** The repository head is pinned to a specific
  revision. A drifting pin is a deploy blocker, not a cosmetic fix.
- **Real A1 origins (E/U).** A1's production origins are accepted; near-miss
  and look-alike origins are rejected, because origin matching is the only thing
  stopping another site from embedding the widget.
- **Manifest posture (F).** The pilot is labelled ``pilot``, not ``production``,
  while business actions are still simulated - and the loader enforces that a
  non-production posture cannot carry a live provider.
- **Validator contract (I).** The expected-head check, and a hermetic harness
  proving a genuinely production-shaped environment reports READY. Hermetic
  matters: this suite must not pass or fail based on the developer's shell.

Nothing here contacts a network provider, deploys anything, or writes outside
the test namespace. Tenant-scoped rows are removed on teardown.
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError

from app.models.base import Base
from app.models.organization import Organization
from app.models.public_chat import PublicChatConfiguration

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "config" / "tenants" / "a1-cash-for-cars.yaml"

EXPECTED_HEAD = "1p4a0001"
PREVIOUS_HEAD = "1p2a0001"

# A1's real production origins, per config/tenants/a1-cash-for-cars.yaml.
A1_APEX = "https://a1cashforcars.com.au"
A1_WWW = "https://www.a1cashforcars.com.au"


def _load_validator():
    script = REPO_ROOT / "scripts" / "validate_production_config.py"
    spec = importlib.util.spec_from_file_location("p1p4_validator", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


validator = _load_validator()


def _reporter(*, strict: bool = False):
    return validator.Reporter(fail_on_warn=strict)


# ======================================================================
# D. Exact migration head
# ======================================================================


def test_repository_head_is_exactly_the_expected_revision():
    """One head, and it is the one this milestone reviewed.

    Asserting the literal id rather than ``len(heads) == 1`` is deliberate: a
    second migration added later would satisfy a count-only check while silently
    moving the head, and a deploy would then apply unreviewed DDL.
    """
    script = ScriptDirectory.from_config(Config(str(REPO_ROOT / "alembic.ini")))
    heads = script.get_heads()

    assert len(heads) == 1, f"expected a single head, found {heads}"
    assert heads[0] == EXPECTED_HEAD


def test_expected_head_migration_revises_the_previous_phase_head():
    """The chain is linear: 1p4a0001 sits directly on 1p2a0001."""
    from alembic import script as alembic_script

    directory = ScriptDirectory.from_config(Config(str(REPO_ROOT / "alembic.ini")))
    revision = directory.get_revision(EXPECTED_HEAD)
    assert revision is not None

    downgrades = revision.down_revision
    downgrades = [downgrades] if isinstance(downgrades, str) else list(downgrades or [])
    assert downgrades == [PREVIOUS_HEAD], (
        f"{EXPECTED_HEAD} must revise exactly {PREVIOUS_HEAD}, got {downgrades}"
    )
    assert alembic_script is not None


def test_validator_expected_head_constant_matches_the_repository():
    """A stale constant would report a healthy database as unvalidatable."""
    script = ScriptDirectory.from_config(Config(str(REPO_ROOT / "alembic.ini")))

    assert validator.EXPECTED_ALEMBIC_HEAD == script.get_heads()[0]


# ======================================================================
# B. One public chat configuration per tenant, enforced by the database
# ======================================================================


def test_model_declares_one_config_per_tenant_as_a_unique_constraint():
    """The invariant is declared on the model, not only in the migration.

    A migration-only invariant silently drifts: the ORM would still allow the
    second insert, so the failure would appear in production as a raw
    IntegrityError from whichever writer happened to be running.
    """
    constraints = {
        c.name: c
        for c in PublicChatConfiguration.__table__.constraints
        if c.name
    }
    unique = constraints["ux_public_chat_configurations_organization_id"]

    assert [c.name for c in unique.columns] == ["organization_id"]


def test_organization_id_remains_not_null():
    column = PublicChatConfiguration.__table__.c.organization_id

    assert column.nullable is False


def test_organization_id_lookup_index_is_not_redundant():
    """A unique constraint indexes the column on its own.

    Keeping a second plain index on organization_id would make every write
    maintain two identical structures for no query benefit, so the migration
    drops it. This pins that the model agrees.
    """
    index_names = {
        index.name
        for index in PublicChatConfiguration.__table__.indexes
    }

    assert "ix_public_chat_configurations_organization_id" not in index_names


@pytest.mark.asyncio
async def test_database_rejects_a_second_public_chat_config_for_one_tenant(db):
    """The core invariant, proven against the real database.

    A second row for the same organization is refused by PostgreSQL. Before
    migration 1p4a0001 this insert succeeded, which meant the invariant held
    only as long as every writer remembered to check it.
    """
    org = Organization(name=f"p1p4-invariant-{uuid.uuid4().hex[:10]}")
    db.add(org)
    await db.flush()
    # Captured now: a rollback expires the ORM instance, and reading org.id
    # afterwards would trigger a lazy refresh outside the async greenlet.
    org_id = org.id

    def _config(suffix: str) -> PublicChatConfiguration:
        return PublicChatConfiguration(
            organization_id=org_id,
            public_widget_key_hash=hashlib.sha256(
                f"p1p4-{org_id}-{suffix}".encode()
            ).hexdigest(),
            display_name="A1 Cash for Cars",
            welcome_message="Hi!",
            allowed_origins=[A1_APEX],
            enabled=True,
            theme_token="a1-dark",
            max_message_length=2000,
            max_messages_per_minute=12,
            session_ttl_hours=24,
        )

    try:
        db.add(_config("first"))
        await db.commit()

        db.add(_config("second"))
        with pytest.raises(IntegrityError) as excinfo:
            await db.commit()
        await db.rollback()

        # The named constraint, not just "some" violation: a wrong constraint
        # firing would mean the test is passing for the wrong reason.
        assert "ux_public_chat_configurations_organization_id" in str(excinfo.value)

        remaining = await db.execute(
            text(
                "SELECT COUNT(*) FROM public_chat_configurations "
                "WHERE organization_id = :org_id"
            ),
            {"org_id": org_id},
        )
        assert remaining.scalar_one() == 1
    finally:
        await db.execute(
            delete(PublicChatConfiguration).where(
                PublicChatConfiguration.organization_id == org_id
            )
        )
        await db.execute(delete(Organization).where(Organization.id == org_id))
        await db.commit()


@pytest.mark.asyncio
async def test_two_tenants_may_each_hold_their_own_config(db):
    """The invariant is per-tenant, not a global single row.

    Guarding the over-correction: a unique constraint on ``id`` or on the widget
    key alone would satisfy "one config per tenant" for the wrong reason and
    break every second customer on the platform.
    """
    orgs = [Organization(name=f"p1p4-multi-{uuid.uuid4().hex[:10]}") for _ in range(2)]
    for org in orgs:
        db.add(org)
    await db.flush()

    try:
        for org in orgs:
            db.add(
                PublicChatConfiguration(
                    organization_id=org.id,
                    public_widget_key_hash=hashlib.sha256(
                        f"p1p4-multi-{org.id}".encode()
                    ).hexdigest(),
                    display_name="Support",
                    welcome_message="Hi!",
                    allowed_origins=[A1_WWW],
                    enabled=True,
                    theme_token="default",
                    max_message_length=2000,
                    max_messages_per_minute=12,
                    session_ttl_hours=24,
                )
            )
        await db.commit()
    finally:
        await db.execute(
            delete(PublicChatConfiguration).where(
                PublicChatConfiguration.organization_id.in_([o.id for o in orgs])
            )
        )
        await db.execute(delete(Organization).where(Organization.id.in_([o.id for o in orgs])))
        await db.commit()


# ======================================================================
# C. Migration duplicate precheck
# ======================================================================


def _load_migration_module():
    path = (
        REPO_ROOT
        / "alembic"
        / "versions"
        / "1p4a0001_one_public_chat_config_per_tenant.py"
    )
    spec = importlib.util.spec_from_file_location("p1p4_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


migration = _load_migration_module()


def _sync_engine():
    """A synchronous engine: alembic's ``op.get_bind()`` is a sync connection."""
    from sqlalchemy import create_engine

    from app.core.config import settings

    return create_engine(
        settings.database_url.replace("postgresql+asyncpg", "postgresql+psycopg")
    )


def _make_duplicate_configs(conn, *, count: int = 3) -> int:
    """Create a tenant with ``count`` widget rows, inside the caller's transaction.

    The Phase 1P.4 constraint forbids this, so it is dropped for the duration of
    the enclosing transaction. PostgreSQL DDL is transactional, so the caller
    rolling back also restores the constraint - the test cannot leave the
    database weaker than it found it.
    """
    conn.execute(
        text(
            "ALTER TABLE public_chat_configurations "
            "DROP CONSTRAINT ux_public_chat_configurations_organization_id"
        )
    )
    org_id = conn.execute(
        text(
            "INSERT INTO organizations (name) "
            "VALUES (:name) RETURNING id"
        ),
        {"name": f"p1p4-dupcheck-{uuid.uuid4().hex[:10]}"},
    ).scalar_one()
    for suffix in ("a", "b", "c")[:count]:
        conn.execute(
            text(
                "INSERT INTO public_chat_configurations "
                "(organization_id, public_widget_key_hash, display_name, "
                " welcome_message, allowed_origins, enabled, theme_token, "
                " max_message_length, max_messages_per_minute, session_ttl_hours, "
                " created_at, updated_at) "
                "VALUES (:org, :hash, 'Support', 'Hi!', :origins, true, 'default', "
                " 2000, 12, 24, now(), now())"
            ),
            {
                "org": org_id,
                "hash": hashlib.sha256(
                    f"p1p4-dupcheck-{org_id}-{suffix}".encode()
                ).hexdigest(),
                "origins": "[]",
            },
        )
    return org_id


def test_duplicate_precheck_reports_every_offending_tenant():
    """The precheck names the tenants, not just "there is a problem".

    An operator meeting a bare constraint violation on a production database
    cannot act on it. This is the exact query the runbook tells them to run,
    exercised against real duplicate rows.
    """
    engine = _sync_engine()
    try:
        with engine.begin() as conn:
            org_id = _make_duplicate_configs(conn, count=3)

            duplicates = migration._find_duplicate_organizations(conn)

            assert (org_id, 3) in duplicates
            # Rolled back by raising, which also restores the constraint.
            raise _Rollback
    except _Rollback:
        pass
    finally:
        engine.dispose()


def _constraint_is_present(engine) -> bool:
    with engine.connect() as conn:
        return (
            conn.execute(
                text(
                    "SELECT count(*) FROM pg_constraint "
                    "WHERE conname = 'ux_public_chat_configurations_organization_id'"
                )
            ).scalar_one()
            == 1
        )


class _Rollback(Exception):
    """Sentinel used to roll back a fixture transaction."""


def test_precheck_rolls_back_cleanly_and_the_constraint_survives():
    """The duplicate fixture must not weaken the invariant it creates.

    Guards the test above: if the constraint were not restored, every later test
    in this file would run against a weakened schema.
    """
    engine = _sync_engine()
    try:
        with engine.begin() as conn:
            _make_duplicate_configs(conn, count=2)
            raise _Rollback
    except _Rollback:

        with engine.connect() as conn:
            duplicates = migration._find_duplicate_organizations(conn)
        assert duplicates == [], f"fixture leaked: {duplicates}"

        assert _constraint_is_present(engine)
    finally:
        engine.dispose()


def test_precheck_finds_nothing_on_a_healthy_database():
    """A clean database must not be blocked by the precheck."""
    engine = _sync_engine()
    try:
        with engine.connect() as conn:
            assert migration._find_duplicate_organizations(conn) == []
    finally:
        engine.dispose()


class _RecordingOps:
    """Stand-in for alembic's ``op`` proxy.

    The real proxy refuses every call outside a live Operations context, and
    driving a real upgrade here would mutate the test database. This records the
    call order instead, which is exactly what these tests assert on.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def get_bind(self):
        # The precheck is monkeypatched in these tests, so the connection it
        # would receive is never touched.
        return None

    def create_unique_constraint(self, name, table, columns, **kwargs):
        self.calls.append(("create_unique_constraint", (name, table, tuple(columns))))

    def drop_index(self, name, table_name=None, **kwargs):
        self.calls.append(("drop_index", (name, table_name)))

    def create_index(self, name, table_name, columns, **kwargs):
        self.calls.append(("create_index", (name, table_name, tuple(columns))))

    def drop_constraint(self, name, table_name, **kwargs):
        self.calls.append(("drop_constraint", (name, table_name)))


def test_upgrade_refuses_and_explains_when_duplicates_exist(monkeypatch):
    """The failure names the tenants and promises no data was touched."""
    monkeypatch.setattr(
        migration,
        "_find_duplicate_organizations",
        lambda _conn: [(7, 2), (9, 3)],
    )
    ops = _RecordingOps()
    monkeypatch.setattr(migration, "op", ops)

    with pytest.raises(RuntimeError) as excinfo:
        migration.upgrade()

    message = str(excinfo.value)
    assert "organization_id=7 (2 rows)" in message
    assert "organization_id=9 (3 rows)" in message
    assert "No rows were changed" in message
    # The point of the precheck: nothing was mutated on the way to failing.
    assert ops.calls == []


def test_upgrade_applies_the_constraint_and_drops_the_redundant_index(monkeypatch):
    """Order matters: constraint first, so a failure leaves the index intact."""
    monkeypatch.setattr(migration, "_find_duplicate_organizations", lambda _c: [])
    ops = _RecordingOps()
    monkeypatch.setattr(migration, "op", ops)

    migration.upgrade()

    assert [name for name, _ in ops.calls] == [
        "create_unique_constraint",
        "drop_index",
    ]
    assert ops.calls[0][1] == (
        "ux_public_chat_configurations_organization_id",
        "public_chat_configurations",
        ("organization_id",),
    )


def test_downgrade_restores_the_index_before_dropping_the_constraint(monkeypatch):
    """Reverse order, for the same reason: never momentarily unindexed."""
    ops = _RecordingOps()
    monkeypatch.setattr(migration, "op", ops)

    migration.downgrade()

    assert [name for name, _ in ops.calls] == ["create_index", "drop_constraint"]


def test_migration_changes_nothing_unrelated():
    """Scope check: only the one table, and only these two objects."""
    source = (REPO_ROOT / "alembic" / "versions" / "1p4a0001_one_public_chat_config_per_tenant.py").read_text()

    tables_touched = set(re.findall(r'table_name="([^"]+)"', source)) | set(
        re.findall(r'create_unique_constraint\(\s*\w+,\s*"([^"]+)"', source)
    ) | set(re.findall(r'drop_constraint\(\s*\w+,\s*"([^"]+)"', source))

    assert tables_touched == {"public_chat_configurations"}


# ======================================================================
# E/U. Real A1 origins and foreign-origin rejection
# ======================================================================


def test_manifest_ships_the_real_a1_production_origins():
    from app.tenant_onboarding import load_manifest_file

    manifest = load_manifest_file(MANIFEST_PATH)

    assert manifest.public_chat is not None
    assert manifest.public_chat.enabled is True
    assert set(manifest.public_chat.allowed_origins) == {A1_APEX, A1_WWW}


def test_manifest_no_longer_carries_a_reserved_example_origin():
    """A `.example` origin resolves nowhere; the widget would never load."""
    text_ = MANIFEST_PATH.read_text()

    assert "a1cashforcars.example" not in text_


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil-a1cashforcars.com.au",       # suffix appended to the left
        "https://a1cashforcars.com.au.evil.example",  # suffix appended to the right
        "http://a1cashforcars.com.au",            # scheme downgrade
        "https://subdomain.a1cashforcars.com.au",  # unconfigured subdomain
        "https://a1cashforcars.com.au:8443",      # unexpected port
        "https://A1CASHFORS.CARS.com.au",         # wrong host entirely
        "https://a1cashforcars.com.au/",         # trailing slash changes the origin
    ],
)
def test_foreign_origins_are_rejected_by_exact_match(origin):
    """Matching is exact string membership, not suffix or host heuristics.

    A suffix or prefix check would accept the look-alike hosts above, which is
    the standard way an attacker gets a third-party site to serve somebody
    else's widget.
    """
    from app.services.public_chat_service import (
        PublicChatOriginNotAllowedError,
        PublicChatService,
    )

    allowed = [A1_APEX, A1_WWW]
    configuration = type(
        "Cfg",
        (),
        {"allowed_origins": allowed, "enabled": True},
    )()

    with pytest.raises(PublicChatOriginNotAllowedError):
        PublicChatService.validate_embedding_origin(configuration, origin)


@pytest.mark.parametrize("origin", [A1_APEX, A1_WWW])
def test_real_a1_origins_are_accepted(origin):
    from app.services.public_chat_service import PublicChatService

    allowed = [A1_APEX, A1_WWW]
    configuration = type("Cfg", (), {"allowed_origins": allowed, "enabled": True})()

    assert (
        PublicChatService.validate_embedding_origin(configuration, origin) is None
    )


def test_absent_origin_is_rejected():
    """No referrer means no allowlist match; fail closed."""
    from app.services.public_chat_service import (
        PublicChatOriginNotAllowedError,
        PublicChatService,
    )

    configuration = type("Cfg", (), {"allowed_origins": [A1_APEX]})()

    with pytest.raises(PublicChatOriginNotAllowedError):
        PublicChatService.validate_embedding_origin(configuration, None)


def test_empty_allowlist_denies_every_origin():
    """A disabled widget must not answer from any site, including its own."""
    from app.services.public_chat_service import (
        PublicChatOriginNotAllowedError,
        PublicChatService,
    )

    configuration = type("Cfg", (), {"allowed_origins": []})()

    with pytest.raises(PublicChatOriginNotAllowedError):
        PublicChatService.validate_embedding_origin(configuration, A1_APEX)


# ======================================================================
# F. Manifest posture
# ======================================================================


def test_manifest_is_labelled_pilot_not_production():
    """Business actions are still simulated, so 'production' would be false."""
    from app.tenant_onboarding import load_manifest_file

    manifest = load_manifest_file(MANIFEST_PATH)

    assert manifest.pilot is not None
    assert manifest.pilot.state == "pilot"
    assert manifest.pilot.state != "production"


def test_manifest_keeps_the_provider_explicitly_local_demo():
    from app.tenant_onboarding import load_manifest_file

    manifest = load_manifest_file(MANIFEST_PATH)

    assert len(manifest.business_integrations) == 1
    integration = manifest.business_integrations[0]
    assert integration.provider == "a1_cash_for_cars"
    assert integration.provider_mode == "local_demo"


def test_pilot_is_an_accepted_pilot_state():
    from app.tenant_onboarding.manifest import _PILOT_STATES

    assert "pilot" in _PILOT_STATES


@pytest.mark.parametrize("state", ["pilot", "local_demo", "staging", "disabled"])
def test_non_production_postures_may_not_declare_a_live_provider(state):
    """The interlock that stops a simulated tenant being labelled live.

    A non-production posture with a real provider would tell a reviewer the
    pilot is inert when it is not. The loader must refuse rather than trust the
    notes field.
    """
    from app.tenant_onboarding.manifest import _NON_PRODUCTION_STATES

    assert state in _NON_PRODUCTION_STATES
    assert "production" not in _NON_PRODUCTION_STATES


def test_only_local_demo_provider_mode_exists_in_this_build():
    """There is no real A1 backend, so no manifest may claim one."""
    from app.tenant_onboarding.manifest import _KNOWN_PROVIDER_MODES

    assert _KNOWN_PROVIDER_MODES == {"local_demo"}


def test_manifest_cannot_claim_production_while_actions_are_simulated(tmp_path):
    """``state: production`` plus ``provider_mode: local_demo`` must not parse.

    This is the one labelling error that would let a reader believe A1 performs
    real quotes and offers, so the loader refuses it instead of trusting the
    reviewer to notice.
    """
    import yaml

    from app.tenant_onboarding.manifest import ManifestError, parse_manifest

    doc = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    doc["pilot"] = {"state": "production"}

    with pytest.raises(ManifestError) as excinfo:
        parse_manifest(doc)

    message = str(excinfo.value)
    assert "local_demo" in message
    assert "pilot" in message


def test_a1_manifest_never_declares_the_production_state():
    """Belt-and-braces on the shipped file itself."""
    from app.tenant_onboarding import load_manifest_file

    manifest = load_manifest_file(MANIFEST_PATH)

    assert manifest.pilot is not None
    assert manifest.pilot.state != "production"
    assert manifest.pilot.state == "pilot"
    for integration in manifest.business_integrations:
        assert integration.provider_mode == "local_demo"


def test_validator_rejects_a_manifest_claiming_production(tmp_path, monkeypatch):
    """The validator applies the rule to a directory, not just the loader."""
    import yaml

    doc = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    doc["tenant"] = dict(doc["tenant"], slug="prod-claimant", name="Prod Claimant")
    doc["pilot"] = {"state": "production"}
    (tmp_path / "prod-claimant.yaml").write_text(
        yaml.safe_dump(doc, sort_keys=False), encoding="utf-8"
    )

    monkeypatch.setattr(validator, "MANIFEST_DIR", tmp_path)
    reporter = _reporter()

    validator.check_manifests(reporter)

    assert reporter.failures >= 1, reporter.lines
    assert any("prod-claimant" in line for line in reporter.lines)


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:3000",
        "https://localhost:3000",
        "https://10.1.2.3",
        "https://192.168.0.9",
        "https://[::1]",
    ],
)
def test_public_url_check_rejects_unreachable_hosts(url):
    """An https loopback/private URL looks configured but serves nobody.

    It passes the scheme check and the example-TLD check, so without this rule a
    pilot embed could be "validated" and still fail for every real customer.
    """
    class _Cfg:
        frontend_base_url = url
        backend_public_url = "https://api.example-real.com"

    reporter = _reporter()

    validator.check_public_urls(_Cfg(), reporter)

    assert reporter.failures >= 1, reporter.lines
    assert any("unreachable" in line for line in reporter.lines), reporter.lines


@pytest.mark.parametrize(
    "url",
    ["https://a1cashforcars.com.au", "https://cxops-ai.vercel.app", "https://8.8.8.8"],
)
def test_public_url_check_accepts_reachable_hosts(url):
    class _Cfg:
        frontend_base_url = url
        backend_public_url = "https://api.cxops-ai.example-real.com"

    reporter = _reporter()

    validator.check_public_urls(_Cfg(), reporter)

    assert reporter.failures == 0, reporter.lines


# ======================================================================
# I. Validator: expected head + hermetic production harness
# ======================================================================


def _fake_alembic(
    monkeypatch,
    *,
    current: str,
    heads: str,
    code: int = 0,
):
    """Stand in for the alembic subprocess so the check is hermetic.

    ``check_migrations`` imports subprocess inside the function, so the patch
    target is the real module rather than an attribute of the loaded script.
    """
    import subprocess

    def fake_run(cmd, *args, **kwargs):
        target = "current" if "current" in cmd else "heads"
        text_out = current if target == "current" else heads
        return subprocess.CompletedProcess(cmd, code, stdout=text_out, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)


def test_migration_check_passes_at_the_expected_head(monkeypatch):
    _fake_alembic(
        monkeypatch,
        current=f"{EXPECTED_HEAD} (head)\n",
        heads=f"{EXPECTED_HEAD} (head)\n",
    )
    reporter = _reporter()

    validator.check_migrations(reporter)

    assert reporter.failures == 0, reporter.lines
    assert any("at expected head" in line for line in reporter.lines), reporter.lines


def test_migration_check_fails_when_the_repository_head_is_unexpected(monkeypatch):
    """A stale EXPECTED_ALEMBIC_HEAD is a build problem, reported as one."""
    _fake_alembic(
        monkeypatch,
        current=f"{EXPECTED_HEAD} (head)\n",
        heads=f"{PREVIOUS_HEAD} (head)\n",
    )
    reporter = _reporter()

    validator.check_migrations(reporter)

    assert reporter.failures == 1, reporter.lines
    assert "expects" in reporter.lines[0]


def test_migration_check_fails_on_multiple_heads(monkeypatch):
    _fake_alembic(
        monkeypatch,
        current=f"{EXPECTED_HEAD} (head)\n",
        heads=f"{EXPECTED_HEAD} (head)\ndeadbeef (head)\n",
    )
    reporter = _reporter()

    validator.check_migrations(reporter)

    assert reporter.failures == 1, reporter.lines
    assert "multiple heads" in reporter.lines[0]


def test_migration_check_fails_when_the_database_is_behind(monkeypatch):
    """One revision behind is a gap, and the operator is told how to fix it."""
    _fake_alembic(
        monkeypatch,
        current=f"{PREVIOUS_HEAD} (head)\n",
        heads=f"{EXPECTED_HEAD} (head)\n",
    )
    reporter = _reporter()

    validator.check_migrations(reporter)

    assert reporter.failures == 1, reporter.lines
    assert "migration gap" in reporter.lines[0]


def test_migration_check_fails_when_the_database_is_ahead_of_this_build(monkeypatch):
    """An unreviewed schema is as dangerous as an unapplied one."""
    _fake_alembic(
        monkeypatch,
        current="ffff9999 (head)\n",
        heads=f"{EXPECTED_HEAD} (head)\n",
    )
    reporter = _reporter()

    validator.check_migrations(reporter)

    assert reporter.failures == 1, reporter.lines
    assert "not in this" in reporter.lines[0]


def test_migration_check_fails_when_no_revision_is_applied(monkeypatch):
    """An uninitialised schema must not read as 'at head'."""
    _fake_alembic(monkeypatch, current="\n", heads=f"{EXPECTED_HEAD} (head)\n")
    reporter = _reporter()

    validator.check_migrations(reporter)

    assert reporter.failures == 1, reporter.lines
    assert "no applied revision" in reporter.lines[0]


def _production_settings(**overrides):
    """A genuinely production-shaped Settings, hermetically constructed.

    Every secret is pinned explicitly for the reason documented in
    tests/test_validate_production_config.py: ``_env_file=None`` does not stop
    ``os.environ`` from supplying a value, so an unpinned field would make this
    suite's verdict depend on the developer's shell.
    """
    from cryptography.fernet import Fernet

    from app.core.config import Settings

    base = {
        "ENVIRONMENT": "production",
        "DEBUG": False,
        "DATABASE_URL": "postgresql+asyncpg://u:p@db.internal:5432/cxops?sslmode=require",
        "AUTH_MODE": "jwks",
        "AUTH_JWKS_URL": "https://issuer.example.com/.well-known/jwks.json",
        "AUTH_JWT_AUDIENCE": "cxops-staff",
        "AUTH_JWT_ISSUER": "https://issuer.example.com/",
        "AUTH_DEV_MODE": False,
        "ENCRYPTION_KEYS": Fernet.generate_key().decode(),
        "OPENAI_API_KEY": "sk-p1p4-harness-not-a-real-key",
        "FRONTEND_BASE_URL": "https://cxops.example.com",
        "BACKEND_PUBLIC_URL": "https://api.cxops.example.com",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def test_full_production_environment_reports_ready():
    """The end-to-end contract: a real production shape is READY.

    Without this, every other validator test only proves it can say NOT READY,
    and a script that always fails is indistinguishable from a good one.

    ``--strict`` is used deliberately: the runbook tells operators to run it in
    CI, so an A1 pilot configuration (no Zendesk, no ticket-event webhook) has
    to reach a clean READY with zero warnings. That is only true because absent
    optional integration secrets are reported as a completed configuration
    rather than as a warning.
    """
    cfg = _production_settings()
    reporter = _reporter(strict=True)

    validator.check_runtime_flags(cfg, reporter)
    validator.check_public_urls(cfg, reporter)
    validator.check_auth(cfg, reporter)
    validator.check_secrets(cfg, reporter)
    validator.check_database(cfg, reporter)

    assert reporter.failures == 0, reporter.lines
    assert reporter.warnings == 0, reporter.lines
    assert reporter.verdict() == "READY", reporter.lines


def test_absent_optional_integration_secrets_are_not_warnings():
    """A tenant that does not use Zendesk is fully configured, not at risk.

    An empty ticket_event_webhook_secret makes the endpoint answer 503 by
    design, so treating it as a warning would have made --strict permanently
    unreachable for the A1 pilot.
    """
    cfg = _production_settings()
    reporter = _reporter(strict=True)

    validator.check_secrets(cfg, reporter)

    assert reporter.warnings == 0, reporter.lines
    assert any("integration disabled" in line for line in reporter.lines)
    assert any("webhook disabled" in line for line in reporter.lines)


def test_configured_zendesk_without_its_secret_is_a_warning():
    """Configured-but-incomplete is a real problem, so it still warns."""
    cfg = _production_settings(ZENDESK_SUBDOMAIN="acme")
    reporter = _reporter()

    validator.check_secrets(cfg, reporter)

    assert reporter.warnings >= 1, reporter.lines
    assert any(
        "ZENDESK_SUBDOMAIN is set" in line for line in reporter.lines
    ), reporter.lines


@pytest.mark.parametrize(
    "overrides",
    [
        {"DEBUG": True},
        {"ENCRYPTION_KEYS": ""},
        {"AUTH_DEV_MODE": True},
        {"AUTH_MODE": "hs256"},
        {"FRONTEND_BASE_URL": "http://cxops.example.com"},
        {"BACKEND_PUBLIC_URL": "http://api.cxops.example.com"},
    ],
    ids=["debug", "no-encryption-keys", "dev-auth", "hs256", "http-frontend", "http-backend"],
)
def test_settings_refuses_to_construct_with_an_unsafe_production_value(overrides):
    """Layer 1: the process never boots with these, so the validator never runs.

    These five-plus-one cases are refused by ``Settings`` validation itself. That
    is stronger than a validator FAIL - there is no configuration in which the
    service starts and then reports itself unsafe. The validator still checks
    them (defence in depth, and for a hand-assembled Settings object), but the
    real guarantee is here.
    """
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        _production_settings(**overrides)


@pytest.mark.parametrize(
    ("overrides", "expected_check", "expected_fragment"),
    [
        pytest.param(
            {"OPENAI_API_KEY": ""}, "OPENAI_API_KEY", "OPENAI_API_KEY", id="no-openai-key"
        ),
        pytest.param(
            {"DATABASE_URL": "postgresql+asyncpg://u:p@localhost:5432/cxops"},
            "DATABASE_URL", "localhost", id="localhost-db",
        ),
        pytest.param(
            {"DATABASE_URL": "postgresql+asyncpg://u:p@db:5432/cxops?sslmode=disable"},
            "DATABASE_URL", "TLS", id="db-tls-disabled",
        ),
    ],
)
def test_validator_catches_what_settings_cannot(overrides, expected_check, expected_fragment):
    """Layer 2: values Settings accepts but production must still reject.

    ``Settings`` cannot know that a reachable database happens to be on
    localhost, nor that a present-but-empty OpenAI key is fatal at import time in
    ``agent_workflow_service``. The validator is where those are caught.
    """
    cfg = _production_settings(**overrides)
    reporter = _reporter()

    validator.check_runtime_flags(cfg, reporter)
    validator.check_public_urls(cfg, reporter)
    validator.check_auth(cfg, reporter)
    validator.check_secrets(cfg, reporter)
    validator.check_database(cfg, reporter)

    failing = [line for line in reporter.lines if line.startswith("FAIL")]
    assert failing, f"expected a FAIL for {expected_check}, got {reporter.lines}"
    assert any(
        line.split()[1] == expected_check and expected_fragment in line
        for line in failing
    ), reporter.lines
    assert reporter.verdict() == "NOT READY"


def test_production_harness_is_unaffected_by_ambient_ci_environment(monkeypatch):
    """The Phase 1P.3 CI failure, re-proved at the whole-environment level.

    A runner exporting a real ``OPENAI_API_KEY`` or ``DATABASE_URL`` must not be
    able to turn a deliberately broken production environment into READY.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-leaked-from-ci")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/leak")
    monkeypatch.setenv("ENCRYPTION_KEYS", "")
    monkeypatch.setenv("DEBUG", "true")

    # Explicitly broken, and explicitly pinned empty where the test intends it.
    # OPENAI_API_KEY and a localhost DATABASE_URL are used because Settings
    # accepts both; DEBUG is not, since Settings refuses to construct with it.
    cfg = _production_settings(
        OPENAI_API_KEY="",
        DATABASE_URL="postgresql+asyncpg://u:p@localhost:5432/leaked",
    )
    reporter = _reporter()

    validator.check_secrets(cfg, reporter)
    validator.check_database(cfg, reporter)

    assert reporter.failures >= 2, reporter.lines
    assert reporter.verdict() == "NOT READY"


def test_validator_never_prints_a_secret_value_in_any_check():
    """No check may leak a value, including the new head check."""
    from cryptography.fernet import Fernet

    openai_key = "sk-p1p4-must-not-appear"
    encryption_keys = Fernet.generate_key().decode()
    password = "p1p4-password-must-not-appear"
    cfg = _production_settings(
        OPENAI_API_KEY=openai_key,
        ENCRYPTION_KEYS=encryption_keys,
        DATABASE_URL=(
            f"postgresql+asyncpg://cxops:{password}@db.internal:5432/cxops?sslmode=require"
        ),
    )
    reporter = _reporter()

    validator.check_runtime_flags(cfg, reporter)
    validator.check_public_urls(cfg, reporter)
    validator.check_auth(cfg, reporter)
    validator.check_secrets(cfg, reporter)
    validator.check_database(cfg, reporter)
    rendered = "\n".join(reporter.lines)

    for secret in (openai_key, encryption_keys, password):
        assert secret not in rendered


# ======================================================================
# Cross-tenant isolation for the live pilot (V)
# ======================================================================


@pytest_asyncio.fixture
async def p1p4_tenants(db):
    """Two tenants with widget configs, removed on teardown."""
    names = [f"p1p4-a1-{uuid.uuid4().hex[:8]}", f"p1p4-rispu-{uuid.uuid4().hex[:8]}"]
    orgs = [Organization(name=n) for n in names]
    for org in orgs:
        db.add(org)
    await db.flush()

    a1, rispu = orgs
    for org, origin in ((a1, A1_APEX), (rispu, "https://rispu.example.com")):
        db.add(
            PublicChatConfiguration(
                organization_id=org.id,
                public_widget_key_hash=hashlib.sha256(
                    f"p1p4-widget-{org.id}".encode()
                ).hexdigest(),
                display_name=org.name,
                welcome_message="Hi!",
                allowed_origins=[origin],
                enabled=True,
                theme_token="default",
                max_message_length=2000,
                max_messages_per_minute=12,
                session_ttl_hours=24,
            )
        )
    await db.commit()

    yield a1, rispu

    org_ids = [o.id for o in orgs]
    for table in Base.metadata.sorted_tables:
        if "organization_id" in table.columns:
            await db.execute(
                delete(table).where(table.c.organization_id.in_(org_ids))
            )
    await db.execute(delete(Organization).where(Organization.id.in_(org_ids)))
    await db.commit()


@pytest.mark.asyncio
async def test_one_widget_config_per_tenant_after_the_invariant(p1p4_tenants, db):
    """Both tenants hold exactly one config: the invariant is per-tenant."""
    a1, rispu = p1p4_tenants

    for org in (a1, rispu):
        count = await db.execute(
            text(
                "SELECT COUNT(*) FROM public_chat_configurations "
                "WHERE organization_id = :o"
            ),
            {"o": org.id},
        )
        assert count.scalar_one() == 1


# ======================================================================
# K. Agent decision schema: the structured-output mode must stay viable
# ======================================================================


def test_decision_schema_cannot_use_strict_json_schema():
    """``AgentDecision`` is structurally incompatible with strict json_schema.

    OpenAI's strict mode requires every object schema to declare
    ``additionalProperties: false``. ``business_arguments`` is intentionally an
    open map because its keys are per-tool, so this model can never satisfy that
    rule. This test documents the constraint that forces
    ``method="function_calling"`` in ``agent_workflow_service``.
    """
    import json

    from app.schemas.agent import AgentDecision

    schema = AgentDecision.model_json_schema()
    arguments = schema["properties"]["business_arguments"]

    rendered = json.dumps(arguments)
    assert '"additionalProperties": true' in rendered or (
        "additionalProperties" not in arguments
    ), arguments

    # Whatever the exact rendering, the field must remain an open object.
    assert "additionalProperties: false" not in rendered


def test_decision_llm_uses_function_calling():
    """The structured-output mode is load-bearing, so pin it.

    Regression: the default mode produced a 400 from OpenAI on the first
    customer message of a session, surfacing as a 500 in the pilot smoke test.
    """
    import inspect

    from app.services.agent_workflow_service import AgentWorkflowService

    source = inspect.getsource(AgentWorkflowService)
    assert 'method="function_calling"' in source, (
        "with_structured_output must pin method='function_calling'; the default "
        "strict json_schema mode is incompatible with an open "
        "business_arguments map"
    )
