"""Phase 1T - tenant-scoped read-only audit access (SQL is only rendered here).

Runs the rendered grant/revoke scripts on a disposable PostgreSQL and proves,
as the audit role, that: only the pinned tenant's rows are visible, base
tables are unreachable, sensitive columns do not exist in any view, nothing can
be written or created, tenant identity is validated and pinned at apply time,
and revocation removes everything.
"""

import json
import os
import secrets
import time
import uuid
from urllib.parse import urlsplit

import asyncpg
import pytest
import pytest_asyncio

from scripts import audit_access
from scripts.audit_access import (
    AuditAccessError,
    render_activate_sql,
    render_grant_sql,
    render_revoke_sql,
)

SENSITIVE_COLUMNS = {
    "body", "question", "answer", "subject", "description", "requester_email",
    "payload", "token_hash", "response_draft", "reason", "note", "event_data",
    "last_error", "error_message", "dedupe_key", "welcome_message",
    "public_widget_key_hash", "assigned_to_subject",
}


def _dsn() -> str:
    url = os.environ.get("DATABASE_URL", "")
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest_asyncio.fixture
async def conn():
    connection = await asyncpg.connect(_dsn())
    try:
        yield connection
    finally:
        await connection.close()


async def _seed_tenant(conn, slug: str, *, label: str) -> dict:
    org_id = await conn.fetchval(
        "INSERT INTO organizations (name, external_id) VALUES ($1, $2) RETURNING id",
        f"audit-{label}-{uuid.uuid4().hex[:8]}",
        slug,
    )
    conv_id = await conn.fetchval(
        "INSERT INTO conversations (organization_id, provider, channel, status) "
        "VALUES ($1,'public_chat','public_chat','open') RETURNING id",
        org_id,
    )
    ticket_id = await conn.fetchval(
        "INSERT INTO tickets (organization_id, subject, description, status, priority, source, "
        "requester_email) VALUES ($1,$2,$3,'new','medium','public-chat-widget',$4) RETURNING id",
        org_id,
        f"SECRET-SUBJECT-{label}",
        f"SECRET-BODY-{label}",
        f"{label}@example.invalid",
    )
    config_id = await conn.fetchval(
        "INSERT INTO public_chat_configurations (organization_id, public_widget_key_hash, "
        "display_name, welcome_message, enabled, allowed_origins, max_message_length, "
        "max_messages_per_minute, session_ttl_hours) VALUES ($1,$2,'W','hi',true,'[]'::jsonb,"
        "2000,20,24) RETURNING id",
        org_id,
        secrets.token_hex(32),
    )
    session_id = await conn.fetchval(
        "INSERT INTO public_chat_sessions (organization_id, configuration_id, conversation_id, "
        "ticket_id, token_hash, status, expires_at) VALUES ($1,$2,$3,$4,$5,'closed',now()) "
        "RETURNING id",
        org_id, config_id, conv_id, ticket_id, secrets.token_hex(32),
    )
    run_id = await conn.fetchval(
        "INSERT INTO agent_runs (run_id, organization_id, ticket_id, action, reason, "
        "requires_human_approval, status, sources, workflow_path, tool_plan, response_draft) "
        "VALUES ($1,$2,$3,'respond',$4,false,'pending_approval',$5::jsonb,$6::jsonb,'[]'::jsonb,$7) "
        "RETURNING id",
        uuid.uuid4().hex, org_id, ticket_id, f"SECRET-REASON-{label}",
        '[{"source_id": "S1", "document_id": 7, "content": "SECRET-CHUNK"}]',
        '["load_ticket", "retrieve_knowledge"]',
        f"SECRET-DRAFT-{label}",
    )
    rag_id = uuid.uuid4().hex
    await conn.execute(
        "INSERT INTO agent_action_events (agent_run_id, event_type, actor, note, event_data) "
        "VALUES ($1,'public_grounded_auto_reply','public_chat','SECRET-NOTE',$2::jsonb)",
        run_id,
        json.dumps(
            {
                "rag_request_id": rag_id,
                "retrieval_count": 2,
                "best_similarity": 0.7,
                "source_ids": ["S1", "S2"],
                "eligibility_reason": "eligible",
            }
        ),
    )
    await conn.execute(
        "INSERT INTO agent_action_events (agent_run_id, event_type, actor, event_data) "
        "VALUES ($1,'proposed','cxops-agent',$2::jsonb)",
        run_id,
        '{"decision": {"action": "respond", "response_draft": "SECRET-DRAFT-IN-EVENT"}}',
    )
    await conn.execute(
        "INSERT INTO ai_request_logs (organization_id, request_id, feature, model, status, "
        "question, answer, grounded, llm_called, retrieval_count, best_similarity, sources, "
        "latency_ms, error_message, input_tokens, output_tokens, total_tokens, "
        "estimated_cost_usd) VALUES ($1,$2,'public_chat','m','success',$3,$4,true,true,2,"
        "0.7,$5::jsonb,10,'SECRET-ERROR',1,1,2,0.0001)",
        org_id, rag_id, f"SECRET-QUESTION-{label}", f"SECRET-ANSWER-{label}",
        '[{"source_id": "S1", "document_id": 7, "content": "SECRET-CHUNK"}]',
    )
    await conn.execute(
        "INSERT INTO ai_usage_reservations (organization_id, reservation_key, period_start, "
        "status, estimated_cost_usd, expires_at, settled_at) VALUES ($1,$2,date_trunc('month', now()),"
        "'settled',0.01, now() + interval '1 day', now())",
        org_id, f"ai-turn:{org_id}:{session_id}:smoke-{label}",
    )
    await conn.execute(
        "INSERT INTO integration_jobs (dedupe_key, job_type, organization_id, payload, status, "
        "attempts, max_attempts, available_at) VALUES ($1,'agent.execute',$2,$3::jsonb,"
        "'completed',1,3,now())",
        f"SECRET-DEDUPE-{uuid.uuid4().hex}", org_id, '{"secret": "SECRET-PAYLOAD"}',
    )
    return {"org_id": org_id, "run_id": run_id, "session_id": session_id, "ticket_id": ticket_id,
            "rag_id": rag_id, "slug": slug}


async def _cleanup(conn, slug: str, org_ids: list[int]) -> None:
    await conn.execute("ROLLBACK")  # a test may have left an aborted transaction
    await conn.execute(render_revoke_sql(slug))
    for table in (
        "agent_action_events",
    ):
        await conn.execute(
            f"DELETE FROM {table} WHERE agent_run_id IN "
            "(SELECT id FROM agent_runs WHERE organization_id = ANY($1))",
            org_ids,
        )
    for table in (
        "integration_jobs", "ai_usage_reservations", "ai_request_logs", "agent_runs",
        "public_chat_sessions", "public_chat_configurations", "conversations", "tickets",
    ):
        await conn.execute(f"DELETE FROM {table} WHERE organization_id = ANY($1)", org_ids)
    await conn.execute("DELETE FROM organizations WHERE id = ANY($1)", org_ids)


@pytest_asyncio.fixture
async def two_tenants(conn):
    suffix = uuid.uuid4().hex[:8]
    slug = f"audit-a-{suffix}"
    a = await _seed_tenant(conn, slug, label="A")
    b = await _seed_tenant(conn, f"audit-b-{suffix}", label="B")
    yield slug, a, b
    await _cleanup(conn, slug, [a["org_id"], b["org_id"]])


async def _as_role(conn, role: str, sql: str, *args):
    """Run ``sql`` as the audit role inside a rolled-back transaction."""
    async with conn.transaction():
        await conn.execute(f'SET LOCAL ROLE "{role}"')
        try:
            return await conn.fetch(sql, *args)
        finally:
            pass


async def _denied(conn, role: str, sql: str) -> bool:
    try:
        async with conn.transaction():
            await conn.execute(f'SET LOCAL ROLE "{role}"')
            await conn.execute(sql)
    except asyncpg.InsufficientPrivilegeError:
        return True
    return False


@pytest.mark.asyncio
async def test_audit_role_sees_only_the_pinned_tenant_and_nothing_sensitive(conn, two_tenants):
    slug, a, b = two_tenants
    schema, role = audit_access.identifiers(slug)
    await conn.execute(render_grant_sql(slug, days=7))

    # --- identity and role hardening ---
    row = await conn.fetchrow(
        "SELECT rolcanlogin, rolconnlimit, rolvaliduntil, rolbypassrls, rolsuper, rolconfig "
        "FROM pg_roles WHERE rolname = $1", role,
    )
    assert row["rolcanlogin"] is False and row["rolconnlimit"] == 2
    assert not row["rolbypassrls"] and not row["rolsuper"]
    config = " ".join(row["rolconfig"])
    assert "default_transaction_read_only=on" in config
    assert "statement_timeout=10s" in config

    # --- only the pinned tenant's rows are visible, in every view ---
    sessions = await _as_role(conn, role, f'SELECT id FROM "{schema}".sessions')
    assert [r["id"] for r in sessions] == [a["session_id"]]
    for view, own_key, other_key in (
        ("tickets", a["ticket_id"], b["ticket_id"]),
        ("agent_runs", a["run_id"], b["run_id"]),
    ):
        rows = await _as_role(conn, role, f'SELECT id FROM "{schema}"."{view}"')
        ids = {r["id"] for r in rows}
        assert own_key in ids and other_key not in ids
    rag = await _as_role(conn, role, f'SELECT request_id FROM "{schema}".ai_requests')
    assert {r["request_id"] for r in rag} == {a["rag_id"]}
    res = await _as_role(conn, role, f'SELECT reservation_key, status FROM "{schema}".reservations')
    assert len(res) == 1 and res[0]["status"] == "settled" and f":{a['org_id']}:" in res[0]["reservation_key"]
    jobs = await _as_role(conn, role, f'SELECT job_type FROM "{schema}".jobs')
    assert [r["job_type"] for r in jobs] == ["agent.execute"]
    rev = await _as_role(conn, role, f'SELECT version_num FROM "{schema}".schema_revision')
    assert len(rev) == 1

    # --- the audit evidence the owner needs is present and bounded ---
    ev = await _as_role(
        conn, role,
        f"SELECT * FROM \"{schema}\".run_events WHERE event_type = 'public_grounded_auto_reply'",
    )
    assert len(ev) == 1
    assert ev[0]["eligibility_reason"] == "eligible" and ev[0]["rag_request_id"] == a["rag_id"]
    assert ev[0]["source_ids"] is not None
    proposed = await _as_role(
        conn, role, f"SELECT decision_action FROM \"{schema}\".run_events WHERE event_type='proposed'"
    )
    assert proposed[0]["decision_action"] == "respond"

    # --- no view carries a sensitive column, and no secret value appears ---
    cols = await conn.fetch(
        "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = $1",
        schema,
    )
    assert cols
    assert {c["column_name"] for c in cols}.isdisjoint(SENSITIVE_COLUMNS)
    blob = ""
    for view in ("sessions", "tickets", "agent_runs", "run_events", "ai_requests",
                 "reservations", "jobs"):
        for r in await _as_role(conn, role, f'SELECT * FROM "{schema}"."{view}"'):
            blob += repr(dict(r))
    assert "SECRET" not in blob and "example.invalid" not in blob


@pytest.mark.asyncio
async def test_audit_role_cannot_reach_base_tables_write_or_create(conn, two_tenants):
    slug, _a, _b = two_tenants
    schema, role = audit_access.identifiers(slug)
    await conn.execute(render_grant_sql(slug))

    for table in ("tickets", "organizations", "agent_runs", "agent_action_events",
                  "ai_request_logs", "ai_usage_reservations", "integration_jobs",
                  "public_chat_sessions", "conversation_messages", "knowledge_chunks",
                  "alembic_version"):
        assert await _denied(conn, role, f"SELECT 1 FROM public.{table} LIMIT 1"), table

    assert await _denied(conn, role, f'INSERT INTO "{schema}".tickets (id) VALUES (1)')
    assert await _denied(conn, role, f'UPDATE "{schema}".tickets SET status = \'x\'')
    assert await _denied(conn, role, f'DELETE FROM "{schema}".tickets')
    assert await _denied(conn, role, f'CREATE TABLE "{schema}".t (id int)')
    assert await _denied(conn, role, "CREATE TABLE public.audit_probe (id int)")
    assert await _denied(conn, role, f'CREATE VIEW "{schema}".v AS SELECT 1')


@pytest.mark.asyncio
async def test_tenant_identity_is_validated_and_pinned_at_apply_time(conn, two_tenants):
    slug, a, b = two_tenants
    schema, role = audit_access.identifiers(slug)

    missing = f"missing-{uuid.uuid4().hex[:8]}"
    with pytest.raises(asyncpg.PostgresError, match="must match exactly one organization"):
        await conn.execute(render_grant_sql(missing))
    await conn.execute("ROLLBACK")  # what the script's trailing COMMIT does on failure
    missing_schema, missing_role = audit_access.identifiers(missing)
    assert await conn.fetchval(
        "SELECT 1 FROM pg_roles WHERE rolname = $1", missing_role
    ) is None  # a failed validation applies nothing
    assert await conn.fetchval(
        "SELECT 1 FROM pg_namespace WHERE nspname = $1", missing_schema
    ) is None

    await conn.execute(render_grant_sql(slug))
    with pytest.raises(asyncpg.PostgresError, match="already exists"):
        await conn.execute(render_grant_sql(slug))
    await conn.execute("ROLLBACK")

    # Re-point the slug at another organization: the views stay on the original.
    await conn.execute("UPDATE organizations SET external_id = $2 WHERE id = $1",
                       a["org_id"], f"moved-{uuid.uuid4().hex[:8]}")
    await conn.execute("UPDATE organizations SET external_id = $2 WHERE id = $1",
                       b["org_id"], slug)
    rows = await _as_role(conn, role, f'SELECT id FROM "{schema}".sessions')
    assert [r["id"] for r in rows] == [a["session_id"]]
    # restore so cleanup (which revokes by slug) is unaffected
    await conn.execute("UPDATE organizations SET external_id = $2 WHERE id = $1",
                       b["org_id"], f"restored-{uuid.uuid4().hex[:8]}")


@pytest.mark.asyncio
async def test_revoke_removes_the_role_and_schema_and_is_idempotent(conn, two_tenants):
    slug, _a, _b = two_tenants
    schema, role = audit_access.identifiers(slug)
    await conn.execute(render_grant_sql(slug))
    assert await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", role) == 1

    await conn.execute(render_revoke_sql(slug))
    assert await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", role) is None
    assert await conn.fetchval("SELECT 1 FROM pg_namespace WHERE nspname = $1", schema) is None
    await conn.execute(render_revoke_sql(slug))  # second run is a no-op


def test_renderer_validates_inputs_and_never_embeds_a_secret():
    for bad in ("", "Rispu", "ri spu", "rispu;DROP", "a" * 80, "-rispu", "rispu\"x"):
        with pytest.raises(AuditAccessError):
            audit_access.identifiers(bad)
    for days in (0, 8, -1):
        with pytest.raises(AuditAccessError):
            render_grant_sql("rispu", days=days)
    sql = render_grant_sql("rispu", days=7)
    assert "interval '7 days'" not in sql  # the old, invalid activation expression
    code = "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    )
    upper = code.upper()
    assert "PASSWORD" not in upper  # no secret ever appears in executable SQL
    assert "NOLOGIN" in sql and "CONNECTION LIMIT 2" in sql
    assert "default_transaction_read_only = on" in sql
    assert "GRANT SELECT ON ALL TABLES IN SCHEMA" in sql
    assert "ON public." not in sql  # no grant on any base table
    assert "SUPERUSER" not in upper.replace("NOSUPERUSER", "")


# ---------------------------------------------------------------------------
# Real password authentication as the audit role (not SET ROLE)
# ---------------------------------------------------------------------------


async def _set_password(conn, role: str) -> str:
    """Give the role an ephemeral random password without it entering SQL text
    that a failure could print: it travels as a bound parameter and is applied
    with ``format(%L)`` server side."""
    password = secrets.token_urlsafe(24)
    await conn.execute("SELECT set_config('audit.test_pw', $1, false)", password)
    await conn.execute(
        f"DO $$ BEGIN EXECUTE format('ALTER ROLE %I PASSWORD %L', '{role}', "
        "current_setting('audit.test_pw')); END $$"
    )
    await conn.execute("SELECT set_config('audit.test_pw', '', false)")
    return password


async def _login(role: str, password: str) -> asyncpg.Connection:
    parts = urlsplit(_dsn())
    return await asyncpg.connect(
        host=parts.hostname,
        port=parts.port,
        user=role,
        password=password,
        database=parts.path.lstrip("/"),
        timeout=10,
    )


async def _provision(conn, slug: str, *, days: int = 7) -> tuple[str, str, str]:
    schema, role = audit_access.identifiers(slug)
    await conn.execute(render_grant_sql(slug, days=days))
    password = await _set_password(conn, role)
    await conn.execute(render_activate_sql(slug, days=days))
    return schema, role, password


@pytest.mark.asyncio
async def test_activation_sql_is_valid_and_sets_a_computed_expiry(conn, two_tenants):
    slug, _a, _b = two_tenants
    _schema, role = audit_access.identifiers(slug)
    await conn.execute(render_grant_sql(slug))
    before = await conn.fetchrow(
        "SELECT rolcanlogin, rolvaliduntil FROM pg_roles WHERE rolname = $1", role
    )
    assert before["rolcanlogin"] is False and before["rolvaliduntil"] is None

    await conn.execute(render_activate_sql(slug, days=3))
    after = await conn.fetchrow(
        "SELECT rolcanlogin, rolvaliduntil, (rolvaliduntil - now()) AS remaining "
        "FROM pg_roles WHERE rolname = $1", role,
    )
    assert after["rolcanlogin"] is True
    days = after["remaining"].total_seconds() / 86400
    assert 2.99 < days <= 3.0

    # the printed script must not itself contain an unquoted expression
    assert "(now() + interval" not in render_activate_sql(slug)
    assert "make_interval(days =>" in render_activate_sql(slug)
    assert "%L" in render_activate_sql(slug)


@pytest.mark.asyncio
async def test_activation_refuses_a_missing_role_or_one_the_tool_did_not_create(conn, two_tenants):
    slug, _a, _b = two_tenants
    _schema, role = audit_access.identifiers(slug)

    with pytest.raises(asyncpg.PostgresError, match="does not exist"):
        await conn.execute(render_activate_sql(slug))
    await conn.execute("ROLLBACK")

    await conn.execute(f'CREATE ROLE "{role}" NOLOGIN')  # same name, no marker
    try:
        with pytest.raises(asyncpg.PostgresError, match="not created by the audit-access tool"):
            await conn.execute(render_activate_sql(slug))
        await conn.execute("ROLLBACK")
        assert (
            await conn.fetchval("SELECT rolcanlogin FROM pg_roles WHERE rolname = $1", role)
            is False
        )
    finally:
        await conn.execute(f'DROP ROLE IF EXISTS "{role}"')


@pytest.mark.asyncio
async def test_connecting_as_the_audit_role_enforces_isolation_and_read_only(conn, two_tenants):
    slug, a, b = two_tenants
    schema, role, password = await _provision(conn, slug)

    audit = await _login(role, password)
    try:
        assert await audit.fetchval("SELECT current_user") == role
        assert await audit.fetchval("SHOW default_transaction_read_only") == "on"
        assert await audit.fetchval("SHOW statement_timeout") == "10s"
        assert await audit.fetchval("SELECT current_setting('search_path')") == schema

        ids = {r["id"] for r in await audit.fetch("SELECT id FROM sessions")}
        assert ids == {a["session_id"]}
        runs = {r["id"] for r in await audit.fetch("SELECT id FROM agent_runs")}
        assert a["run_id"] in runs and b["run_id"] not in runs
        assert (await audit.fetchval("SELECT count(*) FROM tickets")) == 1
        assert (await audit.fetchval("SELECT version_num FROM schema_revision")) is not None

        for table in ("tickets", "organizations", "agent_runs", "ai_request_logs",
                      "alembic_version", "integration_jobs"):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await audit.fetch(f"SELECT * FROM public.{table}")
        denied = (asyncpg.InsufficientPrivilegeError, asyncpg.ReadOnlySQLTransactionError)
        with pytest.raises(denied):
            await audit.execute("INSERT INTO tickets (id) VALUES (1)")
        with pytest.raises(denied):
            await audit.execute("CREATE TABLE public.audit_probe (id int)")
        # even after turning the read-only default off, privileges still deny writes
        await audit.execute("SET default_transaction_read_only = off")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await audit.execute("DELETE FROM tickets")
    finally:
        await audit.close()


@pytest.mark.asyncio
async def test_statement_timeout_and_connection_limit_apply_to_real_logins(conn, two_tenants):
    slug, _a, _b = two_tenants
    _schema, role, password = await _provision(conn, slug)

    first = await _login(role, password)
    second = await _login(role, password)
    try:
        with pytest.raises(asyncpg.TooManyConnectionsError):
            await _login(role, password)

        started = time.perf_counter()
        with pytest.raises(asyncpg.QueryCanceledError):
            await first.fetch("SELECT pg_sleep(12)")
        assert 9.0 < time.perf_counter() - started < 12.0
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_expired_credentials_cannot_open_new_connections(conn, two_tenants):
    slug, _a, _b = two_tenants
    _schema, role, password = await _provision(conn, slug)

    live = await _login(role, password)  # opened while valid
    try:
        await conn.execute(f"ALTER ROLE \"{role}\" VALID UNTIL '2000-01-01 00:00:00+00'")
        with pytest.raises(asyncpg.InvalidPasswordError):
            await _login(role, password)
        # a session opened before expiry is not ended by expiry alone ...
        assert await live.fetchval("SELECT 1") == 1
    finally:
        await live.close()


@pytest.mark.asyncio
async def test_revocation_terminates_live_sessions_and_removes_everything(conn, two_tenants):
    slug, _a, _b = two_tenants
    schema, role, password = await _provision(conn, slug)

    live = await _login(role, password)
    assert await live.fetchval("SELECT 1") == 1

    await conn.execute(render_revoke_sql(slug))

    with pytest.raises((asyncpg.ConnectionDoesNotExistError, asyncpg.AdminShutdownError,
                        asyncpg.InterfaceError)):
        await live.fetchval("SELECT 1")
    assert await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", role) is None
    assert await conn.fetchval("SELECT 1 FROM pg_namespace WHERE nspname = $1", schema) is None
    with pytest.raises((asyncpg.InvalidPasswordError, asyncpg.InvalidAuthorizationSpecificationError)):
        await _login(role, password)
    await conn.execute(render_revoke_sql(slug))  # idempotent


@pytest.mark.asyncio
async def test_revoke_refuses_to_touch_objects_it_did_not_create(conn, two_tenants):
    slug, _a, _b = two_tenants
    schema, role = audit_access.identifiers(slug)
    await conn.execute(f'CREATE ROLE "{role}" NOLOGIN')
    await conn.execute(f'CREATE SCHEMA "{schema}"')
    try:
        with pytest.raises(asyncpg.PostgresError, match="was not created by the audit-access tool"):
            await conn.execute(render_revoke_sql(slug))
        await conn.execute("ROLLBACK")
        assert await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", role) == 1
        assert await conn.fetchval("SELECT 1 FROM pg_namespace WHERE nspname = $1", schema) == 1
        # and a grant for a name collision aborts rather than adopting them
        with pytest.raises(asyncpg.PostgresError, match="already exists"):
            await conn.execute(render_grant_sql(slug))
        await conn.execute("ROLLBACK")
    finally:
        await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}"')
        await conn.execute(f'DROP ROLE IF EXISTS "{role}"')


@pytest.mark.asyncio
async def test_revoke_fails_visibly_and_keeps_the_role_locked_when_the_schema_is_not_clean(
    conn, two_tenants
):
    slug, _a, _b = two_tenants
    schema, role, _password = await _provision(conn, slug)
    await conn.execute(f'CREATE TABLE "{schema}".unexpected_extra (id int)')

    with pytest.raises(asyncpg.PostgresError, match="depend|cannot drop"):
        await conn.execute(render_revoke_sql(slug))
    await conn.execute("ROLLBACK")

    # incomplete revocation is not reported as success, and the role is locked out
    assert await conn.fetchval("SELECT rolcanlogin FROM pg_roles WHERE rolname = $1", role) is False
    assert await conn.fetchval("SELECT 1 FROM pg_namespace WHERE nspname = $1", schema) == 1
    # unrelated objects were untouched
    assert await conn.fetchval("SELECT count(*) FROM public.organizations") >= 2

    await conn.execute(f'DROP TABLE "{schema}".unexpected_extra')
    await conn.execute(render_revoke_sql(slug))
    assert await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", role) is None


def test_revoke_script_has_no_cascade_and_ends_with_an_explicit_success_row():
    sql = render_revoke_sql("rispu")
    assert "CASCADE" not in sql.upper()
    assert "DROP VIEW IF EXISTS" in sql and 'DROP SCHEMA "audit_rispu"' in sql
    assert sql.rstrip().endswith("IS NULL;")
    assert "REVOCATION COMPLETE" in sql
    for forbidden in ("PASSWORD", "DROP TABLE", "TRUNCATE", "DELETE FROM"):
        assert forbidden not in sql.upper()
