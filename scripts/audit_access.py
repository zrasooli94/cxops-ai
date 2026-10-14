"""Render the SQL for a tenant-scoped, time-boxed, read-only audit role (1T).

This tool only PRINTS SQL. It never connects to a database, reads no
credential, and nothing it prints is applied automatically: creating the role is
an owner action that needs separate approval, and the generated script contains
no password (the owner enables login and sets the password in the Neon console,
or with ``\\password`` in psql, so the secret never appears in SQL text, logs
or this repository).

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
* The role is NOLOGIN until the owner enables it, has ``VALID UNTIL`` at most
  seven days out, two connections, read-only default transactions and short
  statement timeouts.

    python -m scripts.audit_access grant --tenant rispu --days 7
    python -m scripts.audit_access revoke --tenant rispu
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


# Each view: (name, select-list + FROM/JOIN, tenant predicate on the base alias).
# ``{tid}`` is substituted by ``format('%s', tenant_id)`` inside the DO block.
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


def render_grant_sql(tenant_slug: str, *, days: int = MAX_DAYS) -> str:
    schema, role = identifiers(tenant_slug)
    if not 1 <= days <= MAX_DAYS:
        raise AuditAccessError(f"days must be between 1 and {MAX_DAYS}")
    view_statements = "\n".join(
        f"""  EXECUTE format($q$
CREATE VIEW "{schema}"."{name}" WITH (security_barrier = true) AS
{body}
$q$, tenant_id);"""
        for name, body in _VIEWS
        if "{tid}" in body
    ).replace("{tid}", "%s")
    return f"""-- Phase 1T audit access for tenant "{tenant_slug}". Run once, as the database
-- owner, ONLY after separate owner approval. Contains no secret.
-- Expires {days} day(s) after it is applied. Revoke with the matching revoke script.
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

{view_statements}

  EXECUTE $q$
CREATE VIEW "{schema}"."schema_revision" AS
SELECT version_num FROM public.alembic_version
$q$;
END
$audit$;

-- NOLOGIN until the owner enables it (and sets the password out of band).
CREATE ROLE "{role}" NOLOGIN NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION
  NOBYPASSRLS CONNECTION LIMIT 2;
ALTER ROLE "{role}" SET default_transaction_read_only = on;
ALTER ROLE "{role}" SET statement_timeout = '10s';
ALTER ROLE "{role}" SET idle_in_transaction_session_timeout = '15s';
ALTER ROLE "{role}" SET search_path = "{schema}";

GRANT USAGE ON SCHEMA "{schema}" TO "{role}";
GRANT SELECT ON ALL TABLES IN SCHEMA "{schema}" TO "{role}";

COMMIT;

-- Activation (separate step, owner, interactive; the password never enters SQL):
--   psql> \\password "{role}"
--   ALTER ROLE "{role}" LOGIN VALID UNTIL (now() + interval '{days} days');
"""


def render_revoke_sql(tenant_slug: str) -> str:
    schema, role = identifiers(tenant_slug)
    return f"""-- Phase 1T audit access revocation for tenant "{tenant_slug}". Idempotent.
BEGIN;

DO $revoke$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
    EXECUTE 'ALTER ROLE "{role}" NOLOGIN';
    BEGIN
      PERFORM pg_terminate_backend(pid)
      FROM pg_stat_activity
      WHERE usename = '{role}' AND pid <> pg_backend_pid();
    EXCEPTION WHEN insufficient_privilege THEN
      NULL;  -- sessions then end at their own timeouts / next disconnect
    END;
    EXECUTE 'DROP OWNED BY "{role}"';
  END IF;
END
$revoke$;

DROP SCHEMA IF EXISTS "{schema}" CASCADE;
DROP ROLE IF EXISTS "{role}";

COMMIT;
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    grant = sub.add_parser("grant", help="print the grant script")
    grant.add_argument("--tenant", required=True)
    grant.add_argument("--days", type=int, default=MAX_DAYS)
    revoke = sub.add_parser("revoke", help="print the revoke script")
    revoke.add_argument("--tenant", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "grant":
            sys.stdout.write(render_grant_sql(args.tenant, days=args.days))
        else:
            sys.stdout.write(render_revoke_sql(args.tenant))
    except AuditAccessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
