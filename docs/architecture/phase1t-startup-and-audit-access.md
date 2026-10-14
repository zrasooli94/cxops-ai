# Phase 1T-B - start-up cost and tenant-scoped audit access

Baseline: production `7fd5a64` (Phase 1S), Alembic `1q0a0001`. No production
SQL, credential, configuration, billing or deployment was touched. Hosting
remediation (Render plan -> `0.5c-512mb`) is a separate owner action and is
what removes the platform wake-up delay; this change reduces the CPU work that
remains after it.

## 1. Where the ~41 s went

Render logs (08:19:43 start command -> 08:20:23 "Uvicorn process started") put
about 40 s between the launch command and uvicorn *before the application
code runs*. That interval is `python -m uvicorn app.main:app` loading
`app.main`: the interpreter, uvicorn and **every import of the application
and its libraries**, on a 0.1-CPU instance.

Reproduced in the production image (`python:3.11-slim`, requirements-lock),
`python -c "import app.main"`, `docker run --cpus N` (Docker Desktop VM; the
0.1 figure is noisy, repeated runs shown):

| CPU limit | before (7fd5a64) | after (1T) | change |
|---|---|---|---|
| 2.0 | 1.76 s | 1.22 s | -31 % |
| 0.5 | 3.7-3.8 s | 2.7 s | -28 % |
| 0.1 | 69-71 s | 47-49 s | about -32 % |

At 0.1 CPU the unmodified import also reproduced the production gap (42-71 s
across runs); at 0.5 CPU it is under 4 s. The preflight (a second interpreter
that imports configuration and encryption) adds 13.5 s at 0.1 CPU and 0.7 s at
0.5 CPU.

What was measured and rejected:

* *Precompiled bytecode.* The image sets `PYTHONDONTWRITEBYTECODE=1` and ships
  `app/` as source (0 of 315 files compiled), but `python -m compileall` made no
  measurable difference at 0.1 CPU (about 65 s vs 42-71 s run to run): the time
  is executing imports, not parsing source.
* *Folding the preflight into the API process.* Would save one interpreter
  start, but the deployment contract (preflight line before the launch line,
  branching on its exit status, pinned by `test_render_deployment.py`) is a
  security control. Not changed.

Self time of the import (2-CPU run): application modules 506 ms, `openai`
330 ms, `langsmith` 77 ms, `sqlalchemy` 69 ms, `fastapi` 43 ms. The OpenAI SDK
was the only large, avoidable block.

## 2. Changes

* `ChatOpenAI` and `OpenAIEmbeddings` (the OpenAI SDK) are no longer imported or
  constructed at import time. (`StateGraph` was tried lazily too: only ~15 ms
  and it moved a blocking import into the first customer turn, which a
  timing-sensitive load test caught, so it stays a module-level import.) The three clients are `functools.cached_property`
  on the existing singletons (`rag_service.llm`,
  `embedding_service.client`, `agent_workflow_service.decision_llm`), so the
  public attributes and their assignability (tests, fakes) are unchanged.
  `validate_embedding_dimensions()` still runs at import: a bad dimension still
  fails closed at start-up.
* `app/services/provider_warmup.py`: right after the schema gate and port bind,
  the clients are built on a worker thread so the first customer turn does not
  pay the SDK import. Failure is logged and never fatal; the request path
  raises the real error.
* Instrumentation (Linux, from `/proc`), three separate numbers:
  `app_import_seconds` = first `app` import -> `lifespan` start (marked *before*
  the database is touched, so the schema check is not counted);
  `schema_gate_ms` = the schema check alone; `boot_seconds`
  (`/version` and the log) = process exec -> start-up complete, i.e. interpreter
  + imports + schema gate. A slow first request can be attributed without the
  dashboard: platform wake (before `started_at`) vs imports vs schema gate.
  An earlier draft computed `app_import_seconds` after the gate and so included
  it; a test now pins the ordering.
* Client construction is exactly-once and thread-safe: the clients use
  `app.core.lazy.LazyAttribute` (a locked, double-checked, assignable
  descriptor) instead of `functools.cached_property`, which stopped locking in
  Python 3.12. The warm-up thread and an early request race for the same
  clients; a test with slow stub constructors asserts one construction per
  provider and that all callers share it. The event-loop thread that loses the
  race waits on the lock for the remainder of that construction (hundreds of
  ms), never builds a second client.
* `scripts/benchmark_startup.py`: reproducible import benchmark (see script
  docstring for the `docker run --cpus` recipe).

Unchanged by design: the production preflight and its ordering, encryption-key
validation, the schema gate (bounded retries from 1R, refuses when behind or
unreachable), every authorization/approval/AI-budget/model setting.

## 3. Tests (`tests/test_phase1t_startup.py`)

The OpenAI SDK not loaded at `import app.main`; lazy build on first use and
assignability; warm-up never raises; boot metrics only after start-up;
`lifespan` refuses to serve and never starts warm-up when the schema gate
fails; `start_render_api.sh` with an invalid production environment (DEBUG on,
dev auth, no encryption key, ...) exits non-zero, names each failure and never
reaches the launch line; the benchmark runs and reports the SDK is not
imported.

## 4. Review of the Phase 1T-A `audit_rispu` proposal

Checked against the models, migrations and event payloads at `7fd5a64`:

| Item | Finding | Resolution |
|---|---|---|
| Column types | `agent_runs.sources`, `workflow_path`, `agent_action_events.event_data`, `ai_request_logs.sources` are JSONB (verified in models and a migrated database) | `jsonb_path_query_array` is valid |
| Tenant key | `organizations.external_id` is UNIQUE; manifest slug is stored there | validated: must match exactly one organization |
| Tenant binding | A view joining `organizations` on the slug would follow a later rename or re-assignment | **Changed:** the organization id is resolved once at apply time and baked into each view as a constant; tested by re-pointing the slug |
| Casts | `(event_data->>'retrieval_count')::int` ran for every event type | **Changed:** projections are `CASE`-guarded by event type and returned as text |
| Secrets in SQL | draft created the role with a password | **Changed:** role is `NOLOGIN`; login and password are set out of band; no password appears in any statement |
| Sensitive fields | `agent_runs.reason`/`response_draft`, `event_data.decision.response_draft`, `tickets.subject/description/requester_email`, `ai_request_logs.question/answer/error_message`, `integration_jobs.payload/last_error/dedupe_key`, `public_chat_sessions.token_hash` | none exposed; tested against seeded `SECRET-*` values |
| Revocation | draft only dropped objects | **Changed:** also `NOLOGIN` and best-effort `pg_terminate_backend`, `DROP OWNED`, idempotent |
| Classification evidence | the coordinator intent is **not persisted** anywhere in the database (confirmed in models/events) | cannot be audited from SQL; `public_grounded_auto_reply` (which the gate only permits for `information`) is the durable proxy. Persisting `coordinator_intent`/`skip_reason` is a separate code change |

## 5. Audit access implementation

See `docs/runbooks/phase1t-audit-access.md` for the exact grant, activation,
verification, expiry and revocation procedure. Not an Alembic migration on
purpose: it is temporary, per-tenant, owner-run, and must not become part of
schema history or run on every environment. The generator
(`python -m scripts.audit_access grant|activate|revoke`) only prints SQL.

Corrections after independent review:

* **Activation SQL**: `VALID UNTIL (now() + interval ...)` is not valid
  PostgreSQL. Activation is now its own script that computes the timestamp
  server-side and applies it with `format('ALTER ROLE %I LOGIN VALID UNTIL %L')`;
  it refuses a missing role or one without this tool's marker.
* **Revocation**: no `CASCADE`. It drops exactly the eight known views, then the
  schema in restricted form, so unexpected content aborts and rolls back; the
  role is locked out (`NOLOGIN`, committed) before anything else; sessions are
  terminated and *proved* gone (a missing privilege or a surviving session is an
  error); it refuses to drop a role/schema without the tool's marker; it prints
  `REVOCATION COMPLETE` only when the role and schema are absent.

Tests (`tests/test_phase1t_audit_access.py`, disposable PostgreSQL; the role
is activated with an ephemeral random password passed as a bound parameter and
**authenticated directly**, not via `SET ROLE`):

* Isolation: only the pinned tenant's rows in every view with a second tenant
  seeded; slug re-pointing does not move the views; no sensitive column exists
  and no seeded secret value appears in any result.
* Privileges: `SELECT` on base tables is `permission denied`; insert, delete and
  create are denied, also after the session turns read-only off.
* Settings in force on a real login: `default_transaction_read_only=on`,
  `statement_timeout=10s` (a `pg_sleep(12)` is cancelled at about 10 s),
  `search_path`, and the connection limit (a third connection is refused).
* Lifecycle: activation sets a computed expiry; after `VALID UNTIL` passes, new
  password logins fail while an already-open session survives expiry alone;
  revocation terminates that session, removes role and schema, and is
  idempotent.
* Failure safety: a missing tenant aborts and applies nothing; a pre-existing
  same-name role or schema is neither adopted by grant nor dropped by revoke; a
  foreign object inside the audit schema makes revoke fail visibly, leaves the
  role `NOLOGIN` and everything else intact, and a re-run after cleanup
  succeeds.
