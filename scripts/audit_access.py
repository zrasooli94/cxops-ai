"""Render the SQL for a tenant-scoped, time-boxed, read-only audit role (1T).

This tool only PRINTS SQL. It never connects to a database, reads no
credential, and nothing it prints is applied automatically: creating the role is
an owner action that needs separate approval, and no generated script contains
a password (the owner sets it in the Neon console, or with ``\\password`` in
psql, so the secret never appears in SQL text, logs or this repository).

Design (see ``docs/runbooks/phase1t-audit-access.md``):

* Isolation comes from **tenant-pinned views**, not from the role. Each view
  filters on the tenant's ``organization_id``, resolved and validated once at
  apply time (the slug must match exactly one organization) and then baked in as
  a constant, so a later rename or a new organization cannot widen it.
* The role has ``USAGE`` on one schema and ``SELECT`` on its views only. It is
  granted nothing on ``public`` tables, so ``SELECT ... FROM public.tickets``
  is ``permission denied``.
* The views expose identifiers, statuses, counts, similarity scores and
  citation source ids. They omit message bodies, ticket subject/description,
  requester e-mail, questions, answers, model drafts, tokens and free-text
  errors.
* The role is NOLOGIN until the owner activates it, expires after at most
  seven days, has two connections, read-only default transactions and short
  statement timeouts.
* The schema and role carry a marker comment; revoke refuses to drop any object
  with the same name that does not carry it.

    python -m scripts.audit_access grant    --tenant rispu
    python -m scripts.audit_access activate --tenant rispu --days 7
    python -m scripts.audit_access revoke   --tenant rispu

Run each script with ``psql -v ON_ERROR_STOP=1``. A script has succeeded only if
it ends without an ERROR; revocation prints a final ``REVOCATION COMPLETE`` row
only when the role and schema are really gone.
"""

from __future__ import annotations

import argparse
import re
import sys

MAX_DAYS = 7
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class AuditAccessError(ValueError):
    pass


def identifiers(tenant_slug: str) -> tuple[str, str]:
    """(schema, role) for a validated tenant slug."""
    if not _SLUG.fullmatch(tenant_slug):
        raise AuditAccessError(
            "tenant slug must be lowercase letters, digits and hyphens (max 63)"
        )
    base = tenant_slug.replace("-", "_")
    return f"audit_{base}", f"{base}_audit_ro"


def marker(tenant_slug: str) -> str:
    """Comment stamped on the schema and role this tool creates."""
    return f"cxops-audit:phase1t:{tenant_slug}"


def _check_days(days: int) -> None:
    if not 1 <= days <= MAX_DAYS:
        raise AuditAccessError(f"days must be between 1 and {MAX_DAYS}")


# Each view: (name, SELECT with ``{tid}`` standing for the pinned tenant id).
_VIEWS: tuple[tuple[str, str], ...] = (
    (
        "sessions",
        """SELECT s.id, s.status, s.created_at, s.closed_at, s.expires_at,
       s.ticket_id, s.conversation_id,
       (s.assigned_to_subject IS NOT NULL) AS was_assigned
FROM public.public_chat_sessions s
WHERE s.organization_id = {tid}""",
    ),
    (
        "tickets",
        """SELECT k.id, k.status, k.source, k.created_at, k.resolved_at
FROM public.tickets k
WHERE k.organization_id = {tid}""",
    ),
    (
        "agent_runs",
        """SELECT r.id, r.run_id, r.ticket_id, r.action, r.status,
       r.requires_human_approval, r.authorization_source, r.workflow_path,
       r.created_at,
       jsonb_path_query_array(r.sources, '$[*].source_id') AS source_ids
FROM public.agent_runs r
WHERE r.organization_id = {tid}""",
    ),
    (
        "run_events",
        """SELECT e.id, e.agent_run_id, e.event_type, e.actor, e.created_at,
       CASE WHEN e.event_type = 'public_grounded_auto_reply'
            THEN e.event_data -> 'source_ids' END                  AS source_ids,
       CASE WHEN e.event_type = 'public_grounded_auto_reply'
            THEN e.event_data ->> 'eligibility_reason' END         AS eligibility_reason,
       CASE WHEN e.event_type = 'public_grounded_auto_reply'
            THEN e.event_data ->> 'rag_request_id' END             AS rag_request_id,
       CASE WHEN e.event_type = 'public_grounded_auto_reply'
            THEN e.event_data ->> 'retrieval_count' END            AS retrieval_count,
       CASE WHEN e.event_type = 'public_grounded_auto_reply'
            THEN e.event_data ->> 'best_similarity' END            AS best_similarity,
       CASE WHEN e.event_type = 'proposed'
            THEN e.event_data -> 'decision' ->> 'action' END       AS decision_action
FROM public.agent_action_events e
JOIN public.agent_runs r ON r.id = e.agent_run_id
WHERE r.organization_id = {tid}""",
    ),
    (
        "ai_requests",
        """SELECT l.request_id, l.feature, l.model, l.status, l.grounded,
       l.llm_called, l.retrieval_count, l.best_similarity, l.total_tokens,
       l.estimated_cost_usd, l.latency_ms, l.created_at,
       jsonb_path_query_array(l.sources, '$[*].document_id') AS document_ids
FROM public.ai_request_logs l
WHERE l.organization_id = {tid}""",
    ),
    (
        "reservations",
        """SELECT u.id, u.reservation_key, u.status, u.estimated_cost_usd,
       u.created_at, u.settled_at
FROM public.ai_usage_reservations u
WHERE u.organization_id = {tid}""",
    ),
    (
        "jobs",
        """SELECT j.id, j.job_type, j.status, j.attempts, j.created_at,
       j.completed_at
FROM public.integration_jobs j
WHERE j.organization_id = {tid}""",
    ),
)

# Not tenant data: the migration head, for "direct alembic_version" checks.
_REVISION_VIEW = "schema_revision"


def render_grant_sql(tenant_slug: str, *, days: int = MAX_DAYS) -> str:
    schema, role = identifiers(tenant_slug)
    _check_days(days)
    tag = marker(tenant_slug)
    view_statements = "\n".join(
        f"""  EXECUTE format($q$
CREATE VIEW "{schema}"."{name}" WITH (security_barrier = true) AS
{select}
$q$, tenant_id);"""
        for name, select in _VIEWS
    ).replace("{tid}", "%s")
    return f"""-- Phase 1T audit access for tenant "{tenant_slug}". Run once, as the database
-- owner, ONLY after separate owner approval. Contains no secret.
-- Run with: psql -v ON_ERROR_STOP=1 -f grant.sql
-- The role is created NOLOGIN; activation is a separate step ({days} day(s) maximum).
BEGIN;

DO $audit$
DECLARE
  tenant_id integer;
  matches   integer;
BEGIN
  SELECT count(*), min(id) INTO matches, tenant_id
  FROM public.organizations WHERE external_id = '{tenant_slug}';
  IF matches <> 1 THEN
    RAISE EXCEPTION 'tenant {tenant_slug} must match exactly one organization (found %)', matches;
  END IF;

  IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = '{schema}') THEN
    RAISE EXCEPTION 'schema {schema} already exists; revoke first';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
    RAISE EXCEPTION 'role {role} already exists; revoke first';
  END IF;

  EXECUTE 'CREATE SCHEMA "{schema}"';
  EXECUTE 'COMMENT ON SCHEMA "{schema}" IS ''{tag}''';

{view_statements}

  EXECUTE $q$
CREATE VIEW "{schema}"."{_REVISION_VIEW}" AS
SELECT version_num FROM public.alembic_version
$q$;
END
$audit$;

-- NOLOGIN until the owner activates it (and sets the password out of band).
CREATE ROLE "{role}" NOLOGIN NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION
  NOBYPASSRLS CONNECTION LIMIT 2;
COMMENT ON ROLE "{role}" IS '{tag}';
ALTER ROLE "{role}" SET default_transaction_read_only = on;
ALTER ROLE "{role}" SET statement_timeout = '10s';
ALTER ROLE "{role}" SET idle_in_transaction_session_timeout = '15s';
ALTER ROLE "{role}" SET search_path = "{schema}";

GRANT USAGE ON SCHEMA "{schema}" TO "{role}";
GRANT SELECT ON ALL TABLES IN SCHEMA "{schema}" TO "{role}";

COMMIT;

-- Activation, as two owner steps (the password never enters SQL):
--   1. psql> \\password "{role}"        (or set the password in the Neon console)
--   2. python -m scripts.audit_access activate --tenant {tenant_slug} --days {days}
"""


def render_activate_sql(tenant_slug: str, *, days: int = MAX_DAYS) -> str:
    """Enable login with a timestamp computed and quoted at activation time.

    ``VALID UNTIL`` takes a literal, not an expression, so the value is built
    server-side and applied with ``format(... %L)``. The password must already
    have been set out of band; this script never sees or contains it.
    """
    _schema, role = identifiers(tenant_slug)
    _check_days(days)
    return f"""-- Phase 1T: activate audit login for tenant "{tenant_slug}" for {days} day(s).
-- Run as the database owner AFTER setting the role's password out of band.
-- Run with: psql -v ON_ERROR_STOP=1 -f activate.sql
BEGIN;

DO $activate$
DECLARE
  role_oid oid;
  expires  timestamptz := now() + make_interval(days => {days});
BEGIN
  SELECT oid INTO role_oid FROM pg_roles WHERE rolname = '{role}';
  IF role_oid IS NULL THEN
    RAISE EXCEPTION 'role {role} does not exist; apply the grant script first';
  END IF;
  IF coalesce(shobj_description(role_oid, 'pg_authid'), '') <> '{marker(tenant_slug)}' THEN
    RAISE EXCEPTION 'role {role} was not created by the audit-access tool';
  END IF;
  EXECUTE format('ALTER ROLE %I LOGIN VALID UNTIL %L', '{role}', expires::text);
END
$activate$;

COMMIT;
SELECT rolname, rolcanlogin, rolvaliduntil FROM pg_roles WHERE rolname = '{role}';
"""


def render_revoke_sql(tenant_slug: str) -> str:
    schema, role = identifiers(tenant_slug)
    tag = marker(tenant_slug)
    names = [name for name, _ in _VIEWS] + [_REVISION_VIEW]
    view_array = ", ".join(f"'{name}'" for name in names)
    return f"""-- Phase 1T audit access revocation for tenant "{tenant_slug}".
-- Idempotent. Run with: psql -v ON_ERROR_STOP=1 -f revoke.sql
-- It has succeeded ONLY if it ends with the REVOCATION COMPLETE row and no ERROR.

-- Step 1 (committed on its own): refuse to touch anything this tool did not
-- create, then lock the role out so a later failure can never leave it usable.
BEGIN;
DO $lock$
DECLARE
  role_oid oid;
BEGIN
  SELECT oid INTO role_oid FROM pg_roles WHERE rolname = '{role}';
  IF role_oid IS NOT NULL
     AND coalesce(shobj_description(role_oid, 'pg_authid'), '') <> '{tag}' THEN
    RAISE EXCEPTION 'role {role} exists but was not created by the audit-access tool; refusing to modify it';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = '{schema}')
     AND coalesce((SELECT obj_description(oid, 'pg_namespace')
                   FROM pg_namespace WHERE nspname = '{schema}'), '') <> '{tag}' THEN
    RAISE EXCEPTION 'schema {schema} exists but was not created by the audit-access tool; refusing to modify it';
  END IF;
  IF role_oid IS NOT NULL THEN
    EXECUTE 'ALTER ROLE "{role}" NOLOGIN';
  END IF;
END
$lock$;
COMMIT;

-- Step 2: end every live session of the role and prove none remain. A missing
-- privilege to terminate sessions is an error here, never ignored.
DO $terminate$
DECLARE
  remaining integer;
  attempts  integer := 0;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
    RETURN;
  END IF;
  PERFORM pg_terminate_backend(pid)
  FROM pg_stat_activity
  WHERE usename = '{role}' AND pid <> pg_backend_pid();
  LOOP
    PERFORM pg_stat_clear_snapshot();
    SELECT count(*) INTO remaining
    FROM pg_stat_activity
    WHERE usename = '{role}' AND pid <> pg_backend_pid();
    EXIT WHEN remaining = 0;
    attempts := attempts + 1;
    IF attempts > 50 THEN
      RAISE EXCEPTION '% session(s) of {role} are still active; revocation is INCOMPLETE', remaining;
    END IF;
    PERFORM pg_sleep(0.1);
  END LOOP;
END
$terminate$;

-- Step 3 (one transaction): remove exactly our views, then the schema with the
-- restricted form of DROP (it fails, and rolls everything back, if anything
-- unexpected lives in it), then the role.
BEGIN;
DO $drop$
DECLARE
  view_name text;
BEGIN
  IF to_regnamespace('"{schema}"') IS NOT NULL THEN
    FOREACH view_name IN ARRAY ARRAY[{view_array}] LOOP
      EXECUTE format('DROP VIEW IF EXISTS %I.%I', '{schema}', view_name);
    END LOOP;
    EXECUTE 'DROP SCHEMA "{schema}"';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
    EXECUTE 'DROP OWNED BY "{role}"';
    EXECUTE 'DROP ROLE "{role}"';
  END IF;
END
$drop$;
COMMIT;

-- Step 4: produces a row only if everything above really succeeded.
SELECT 'REVOCATION COMPLETE: {role} and {schema} are gone' AS result
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
  AND to_regnamespace('"{schema}"') IS NULL;
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("grant", "activate"):
        command = sub.add_parser(name, help=f"print the {name} script")
        command.add_argument("--tenant", required=True)
        command.add_argument("--days", type=int, default=MAX_DAYS)
    revoke = sub.add_parser("revoke", help="print the revoke script")
    revoke.add_argument("--tenant", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "grant":
            sys.stdout.write(render_grant_sql(args.tenant, days=args.days))
        elif args.command == "activate":
            sys.stdout.write(render_activate_sql(args.tenant, days=args.days))
        else:
            sys.stdout.write(render_revoke_sql(args.tenant))
    except AuditAccessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
