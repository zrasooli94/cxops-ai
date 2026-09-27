# A1 live pilot runbook

Operational procedure for the Phase 1P.3 A1 live pilot. Every command is
read-only unless it explicitly says otherwise.

The A1 integration is a **local demo**: it returns simulated availability and
quote data. Nothing in this pilot moves money, books a real pickup, or contacts
a real customer. If a pilot needs real valuations, stop and start the next
phase instead of extending the demo.

## 0. What "ready" means

| Signal | Where | Meaning |
| --- | --- | --- |
| Liveness | `GET /health` | Process is serving. Never touches PostgreSQL. |
| Readiness | `GET /ready` | PostgreSQL answers `SELECT 1`. 503 means "not in rotation". |
| Build identity | `GET /version` | Which version/environment is actually deployed. |
| Config gate | `scripts/validate_production_config.py` | Secrets, URLs, auth, and migration head. |
| End-to-end | `scripts/smoke_public_chat.py` | A real session answers a real message. |

A green `/health` with a red `/ready` is a database problem, not a dead process.
Do not restart instances to fix readiness; the database is the thing to check.

## 1. Pre-deploy

1. **Migrations are a separate step.** Never run them from a service start
   command. On a horizontally scaled service every replica would race on DDL.

   ```sh
   DATABASE_URL='postgresql+asyncpg://…' scripts/run_migrations.sh
   ```

   The lock is held for the whole migration, by the same process that runs
   Alembic, so a second concurrent migrator waits instead of failing mid-DDL.
   The wait is bounded: after `MIGRATION_LOCK_WAIT_SECONDS` (default 300) the
   script exits 1 rather than hanging a deploy job. Treat a lock timeout as
   "another deploy is still migrating" and re-run it, not as a failure to
   investigate.

2. **Validate configuration.** This reads only; it never migrates.

   ```sh
   .venv/bin/python scripts/validate_production_config.py --environment production
   ```

   Add `--strict` in CI so warnings are fatal. `--skip-migrations` exists for
   air-gapped runs that cannot reach the database.

   Expected: `READY`. At minimum these must pass:

   - `DEBUG` off
   - `ENVIRONMENT` is `production`
   - `AUTH_MODE` is `jwks`, `AUTH_DEV_MODE` off
   - `frontend_base_url` / `backend_public_url` are `https`
   - `OPENAI_API_KEY` and `ENCRYPTION_KEYS` present (values are never printed)
   - `ALEMBIC` at head

3. **Verify the deployed build, not your laptop.**

   ```sh
   curl -fsS https://<api-host>/version
   curl -fsS https://<api-host>/ready
   ```

## 2. Onboarding the tenant

The tenant manifest is the source of truth. Edit
`config/tenants/a1-cash-for-cars.yaml`, and set the real customer origin
before going live — the shipped manifest uses the reserved
`https://www.a1cashforcars.example`, which the validator warns about and which
will never resolve.

Preview first, and read the plan:

```sh
.venv/bin/python scripts/onboard_tenant.py \
  --manifest config/tenants/a1-cash-for-cars.yaml \
  --dry-run
```

Then apply:

```sh
.venv/bin/python scripts/onboard_tenant.py \
  --manifest config/tenants/a1-cash-for-cars.yaml
```

Rules that matter under pressure:

- The command is idempotent. Re-running with an unchanged manifest reports
  `UNCHANGED` and writes nothing.
- The **raw widget key is printed only on `CREATE` and on an explicit
  `--rotate-widget-key`.** If you lose it, rotate; it is never recoverable.
- Copy the printed `<script>` snippet into the customer's site. The key travels
  in the URL, so treat the page as public and rely on the origin allowlist.
- Re-running with a changed manifest reports `UPDATE` per field; re-read the
  plan before applying if a field you did not expect appears.

## 3. Verifying the pilot

```sh
.venv/bin/python scripts/smoke_public_chat.py \
  --widget-key-file /secure/path/a1.key \
  --embedding-origin https://www.<customer-domain>
```

Prefer `--widget-key-file` over `--widget-key`: the key would otherwise land in
shell history and in the process list.

The smoke test creates a **real** public session, sends a real message, requests
a human, and closes the session. It prints no message bodies, no session
tokens, and no keys. A failure means the widget is not pilot-ready; fix it
before inviting a customer.

## 4. Kill switch

Fastest safe stop, in order of escalation:

1. **Disable the widget** — Settings → Public chat settings → *Disable widget*,
   or `PATCH /staff/tenant-config/public-chat {"enabled": false}`.
   New sessions are refused immediately. History is preserved, and the
   dashboard is still readable.
2. **Empty the origin allowlist** — the widget answers nothing from any site.
3. **Disable the A1 provider** — Settings → Business integrations → *Disable*.
   The agent stops offering A1 tools to that tenant. Already-queued actions
   keep their stored tenant and still require approval.
4. **Rotate the widget key** — every embed using the old key stops working
   immediately. This is the only step that breaks a customer's page, so prefer
   the disable switch unless the key itself is exposed.

Each of these is tenant-scoped and auditable through structured logs
(`tenant_public_chat_config_updated`, `tenant_public_chat_key_rotated`,
`tenant_business_integration_updated`)
carrying provider, field names, and action only — never customer values, keys,
digests, or authorization headers.

## 5. Rollback

There is no new schema in Phase 1P.3, so rollback is configuration, not DDL.

| Situation | Action | Data effect |
| --- | --- | --- |
| Bad branding or origins | Re-apply the previous manifest | Config only |
| Widget exposed to the wrong site | Disable, fix origins, re-enable | None |
| Widget key leaked | Rotate | Old key dead; history intact |
| Provider misbehaving | Disable the provider | No new A1 tools offered |
| Pilot over | `enabled: false` in the manifest, re-apply | Widget off, history kept |

Deleting a tenant is deliberately **not** part of this runbook: it would take
knowledge documents and conversation history with it. Disable first, confirm the
customer is off, and treat deletion as a separate, reviewed change.

## 6. Troubleshooting

| Symptom | Likely cause | Check |
| --- | --- | --- |
| `/ready` is 503 | Database unreachable or credentials wrong | `alembic current`; the readiness payload reports `unavailable` without leaking the DSN |
| `/health` fine, widget 404-style error | Key rotated, embed still uses the old one | Rotate state in Settings; re-copy the snippet |
| Widget loads but says origin rejected | `document.referrer` origin not in the allowlist | Add the exact origin, including scheme and `www` |
| Widget loads, agent silent | `OPENAI_API_KEY` missing/invalid | `validate_production_config.py`; the widget cannot answer without it |
| No A1 tools offered | Provider disabled for the tenant | Settings → Business integrations |
| Knowledge not found | Documents not ingested for this tenant | Re-apply the manifest; ingestion is content-deduplicated |
| `NOT READY` from the validator | A `FAIL` line above it | Read the report; it names each failing check |

## 7. Signals to watch during the pilot

- `/ready` status and `/version` per deploy.
- Public chat session creation rate, message rate, and handoff rate.
- Rate-limit rejections (per-config session limits and per-IP message limits).
- Agent-run failure rate and latency for public-chat conversations.
- `tenant_public_chat_config_updated` / `tenant_public_chat_key_rotated` /
  `tenant_business_integration_updated` events — any of these during
  the pilot should have a matching operator action.
- Knowledge-ingestion failures during a re-apply.

## 8. Known gaps for this pilot

- No durable audit table. Mutations are recorded as structured logs only; that
  is sufficient for a single-tenant pilot and is a deliberate Phase 1P.3
  decision, not an oversight.
- The A1 provider is a local demo. No real quotes, payments, or pickups.
- One widget row per tenant is assumed. A tenant with two rows is reported as
  `ERROR` and refused rather than auto-repaired.
