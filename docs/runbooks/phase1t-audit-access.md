# Phase 1T - seven-day, tenant-scoped, read-only audit access

**Nothing in this repository applies this.** Every step is an owner action and
needs separate approval. The generated SQL contains no password.

## What it grants
* Schema `audit_<tenant>` with eight views pinned to that tenant's
  organization id: `sessions`, `tickets`, `agent_runs`, `run_events`,
  `ai_requests`, `reservations`, `jobs`, `schema_revision`.
* Role `<tenant>_audit_ro`: `USAGE` on that schema and `SELECT` on its views,
  nothing else. No privilege on any `public` table.
* Not exposed: message bodies, ticket subject/description/e-mail, questions,
  answers, model drafts and reasons, tokens/hashes, job payloads, free-text
  errors. Exposed: ids, statuses, timestamps, counts, similarity, citation
  source ids, `eligibility_reason`, reservation keys and settlement.

## Procedure (owner)
1. Review: `python -m scripts.audit_access grant --tenant rispu --days 7`
   prints the exact SQL; `revoke --tenant rispu` prints the exact undo.
2. Apply the grant script once with the owner role (for example
   `psql "$OWNER_URL" -v ON_ERROR_STOP=1 -f grant.sql`). It aborts and applies
   nothing if the slug does not match exactly one organization or the
   role/schema already exists.
3. Activate (interactive, secret never in SQL): `\password "rispu_audit_ro"`
   then `ALTER ROLE "rispu_audit_ro" LOGIN VALID UNTIL (now() + interval '7 days');`
   (or set the password in the Neon console). Use the **direct** endpoint.
4. Verify as the audit role (expected results):
   * `SELECT count(*) FROM audit_rispu.sessions;` -> only RISPU test sessions
   * `SELECT 1 FROM public.tickets LIMIT 1;` -> `permission denied`
   * `SELECT version_num FROM audit_rispu.schema_revision;` -> `1q0a0001`
5. Audit queries (read-only):
```sql
SELECT id, status, created_at, closed_at FROM audit_rispu.sessions ORDER BY id DESC LIMIT 10;
SELECT id, run_id, action, status, authorization_source, source_ids FROM audit_rispu.agent_runs ORDER BY id DESC LIMIT 10;
SELECT agent_run_id, event_type, eligibility_reason, source_ids, rag_request_id
  FROM audit_rispu.run_events WHERE event_type = 'public_grounded_auto_reply' ORDER BY id DESC;
SELECT request_id, status, grounded, retrieval_count, best_similarity, latency_ms
  FROM audit_rispu.ai_requests WHERE feature = 'public_chat' ORDER BY created_at DESC LIMIT 10;
SELECT reservation_key, status, settled_at FROM audit_rispu.reservations ORDER BY id DESC LIMIT 10;
SELECT job_type, status, count(*) FROM audit_rispu.jobs GROUP BY 1, 2;
```
6. Revoke at the end of the audit window (or earlier): apply the `revoke`
   script. It sets NOLOGIN, terminates the role's sessions, drops owned
   objects, the schema and the role; it is idempotent. The role also expires on
   its own at `VALID UNTIL`, and the views can be dropped independently with
   `DROP SCHEMA audit_rispu CASCADE`.

## Limits
* The coordinator intent label is not persisted in any table; classification
  can only be inferred (a `public_grounded_auto_reply` event exists only for
  `information`). Render logs (`public_chat_reply_sent.coordinator_intent`)
  carry it.
* `pg_catalog` still lists object names to any role; no row data is exposed.
* An already-open audit connection survives `VALID UNTIL`; the revoke script
  terminates it.
