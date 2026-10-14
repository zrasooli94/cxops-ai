# Phase 1T - seven-day, tenant-scoped, read-only audit access

**Nothing in this repository applies this.** Every step is an owner action and
needs separate approval. No generated script contains a password. Every script
is run with `psql -v ON_ERROR_STOP=1`; a script has succeeded only if it ends
without an `ERROR`.

## What it creates
* Schema `audit_<tenant>` with eight views pinned to that tenant's organization
  id: `sessions`, `tickets`, `agent_runs`, `run_events`, `ai_requests`,
  `reservations`, `jobs`, `schema_revision`.
* Role `<tenant>_audit_ro`: `NOLOGIN` until activated, `USAGE` on that schema,
  `SELECT` on its views, nothing else; no privilege on any `public` table;
  `CONNECTION LIMIT 2`, `default_transaction_read_only = on`,
  `statement_timeout = 10s`, `idle_in_transaction_session_timeout = 15s`.
* A marker comment (`cxops-audit:phase1t:<tenant>`) on the schema and role.
* Not exposed: message bodies, ticket subject/description/e-mail, questions,
  answers, drafts and reasons, tokens/hashes, job payloads, free-text errors.
  Exposed: ids, statuses, timestamps, counts, similarity, citation source ids,
  `eligibility_reason`, reservation keys and settlement.

## 1. Review (read-only, no database)
```bash
python -m scripts.audit_access grant    --tenant rispu > grant.sql
python -m scripts.audit_access activate --tenant rispu --days 7 > activate.sql
python -m scripts.audit_access revoke   --tenant rispu > revoke.sql
```

## 2. Create (owner role, once)
```bash
psql "$OWNER_URL" -v ON_ERROR_STOP=1 -f grant.sql
```
It aborts, applying nothing, if the slug does not match exactly one
organization or if the schema or role already exists (it never adopts or
modifies an existing object).

## 3. Activate (two steps; the password is never in SQL)
1. Set the password interactively: `psql "$OWNER_URL"` then
   `\password "rispu_audit_ro"` (or set it in the Neon console).
2. `psql "$OWNER_URL" -v ON_ERROR_STOP=1 -f activate.sql`

`activate.sql` computes the expiry on the server and applies it with
`format('ALTER ROLE %I LOGIN VALID UNTIL %L', ...)` (the expression
`VALID UNTIL (now() + interval ...)` is not valid PostgreSQL). It refuses to
run if the role is missing or was not created by this tool, and prints
`rolcanlogin = t` and the exact `rolvaliduntil`. Connect with the **direct**
(unpooled) endpoint.

## 4. Verify (as the audit role)
| Query | Expected |
|---|---|
| `SELECT current_user;` | `rispu_audit_ro` |
| `SHOW default_transaction_read_only;` | `on` |
| `SELECT count(*) FROM sessions;` | only RISPU sessions |
| `SELECT * FROM public.tickets LIMIT 1;` | `permission denied` |
| `INSERT INTO tickets (id) VALUES (1);` | denied (read-only / privilege) |
| `SELECT version_num FROM schema_revision;` | `1q0a0001` |

## 5. Audit queries (read-only)
```sql
SELECT id, status, created_at, closed_at FROM sessions ORDER BY id DESC LIMIT 10;
SELECT id, run_id, action, status, authorization_source, source_ids
  FROM agent_runs ORDER BY id DESC LIMIT 10;
SELECT agent_run_id, event_type, eligibility_reason, source_ids, rag_request_id
  FROM run_events WHERE event_type = 'public_grounded_auto_reply' ORDER BY id DESC;
SELECT request_id, status, grounded, retrieval_count, best_similarity, latency_ms
  FROM ai_requests WHERE feature = 'public_chat' ORDER BY created_at DESC LIMIT 10;
SELECT reservation_key, status, settled_at FROM reservations ORDER BY id DESC LIMIT 10;
SELECT job_type, status, count(*) FROM jobs GROUP BY 1, 2;
```

## 6. Expiry
At `VALID UNTIL` the role can no longer open **new** password-authenticated
connections (verified by test). A connection opened before expiry stays open
until it closes or is revoked; that is why revocation terminates sessions.
Expiry alone is not revocation: still run step 7.

## 7. Revoke (owner role; at the end of the window or earlier)
```bash
psql "$OWNER_URL" -v ON_ERROR_STOP=1 -f revoke.sql
```
Order, each step failing visibly:
1. Refuses to touch a role or schema that lacks this tool's marker.
2. Sets the role `NOLOGIN` and commits, so a later failure cannot leave it
   usable.
3. Terminates the role's sessions and waits until none remain; a missing
   privilege or a surviving session is an `ERROR` ("revocation is INCOMPLETE"),
   never ignored.
4. In one transaction: `DROP VIEW` for exactly the eight known views, then
   `DROP SCHEMA` **without cascade** (fails and rolls back if anything
   unexpected lives in it), then `DROP OWNED BY` and `DROP ROLE`.
5. Prints `REVOCATION COMPLETE: ... are gone` only if the role and schema are
   really absent.

If it errors: the role is already `NOLOGIN`; read the message, resolve the named
dependency or session, and re-run (it is idempotent). Do not treat the
revocation as done until the final `REVOCATION COMPLETE` row appears.

## Limits
* The coordinator intent label is not persisted in any table; classification can
  only be inferred (a `public_grounded_auto_reply` event exists only for
  `information`). Render logs (`public_chat_reply_sent.coordinator_intent`)
  carry it.
* `pg_catalog` still lists object names to any role; no row data is exposed.
* The grant/activate scripts cannot confirm a password was set (the role's
  password hash is not readable by a non-superuser).
