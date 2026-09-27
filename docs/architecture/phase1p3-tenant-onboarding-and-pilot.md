# Phase 1P.3 — Tenant onboarding, widget configuration, and the A1 pilot

## What this phase adds

Phase 1P.2 shipped business tools and human handoff. This phase makes a tenant
operable without a code change: declarative onboarding, a staff configuration
UI, a production config gate, and a repeatable live-pilot procedure.

The central design decision is that **a tenant's configuration is a reviewed
file, not a set of console clicks.** `config/tenants/*.yaml` is the source of
truth; the console is for the small set of changes that must be possible at
3am (branding, origins, kill switch, key rotation, provider toggle).

## Onboarding

```
config/tenants/<tenant>.yaml
        │  strict schema, no secrets, 512 KiB cap
        ▼
app/tenant_onboarding/manifest.py  ── parse_manifest / load_manifest_file
        ▼
app/tenant_onboarding/planner.py   ── build_plan   (read-only, no writes)
        │                            apply_plan   (one transaction)
        ▼
scripts/onboard_tenant.py --dry-run | apply | --rotate-widget-key
```

### Identity without a migration

`organizations.external_id` (unique, nullable, `String(100)`) carries the
tenant slug. It was already there and is already unique, so onboarding needed
no DDL. A manifest without `tenant.external_id`-derived `slug` is refused: the
slug is the stable tenant key used by every later command.

### One widget per tenant, verified rather than assumed

`public_chat_configurations.organization_id` is indexed but **not unique**. A
unique constraint would have been a migration, and a migration on a table that
holds live pilot data is a heavier decision than a check. Instead
`resolve_canonical_public_chat_config` counts rows:

- exactly one → use it
- zero → `CREATE`
- more than one → `ERROR`, and `apply_plan` raises `OnboardingConflictError`

Auto-picking one of several rows would silently configure a widget the operator
did not mean, and the loser would keep serving customers.

### Actions

`CREATE`, `UPDATE`, `UNCHANGED`, `DISABLE`, `ERROR`. Every field that will
change is named in the plan before anything is written, so `--dry-run` output
is a reviewable description of the exact write that would follow.

### Transaction boundary

`apply_plan` commits the organization, widget, and provider rows in one
transaction, then ingests knowledge. The knowledge service commits internally,
so ingestion is **not** part of that transaction. This is deliberate: ingestion
embeds documents and calls OpenAI, so holding a write transaction open across
network calls would pin connections and make a provider hiccup roll back
otherwise-valid configuration.

The consequence is that ingestion failures converge on re-apply rather than
rolling back: knowledge is content-deduplicated per tenant by checksum, so
re-applying the same manifest is safe and adds nothing.

### Idempotency and key lifecycle

- Re-applying an unchanged manifest reports `UNCHANGED` and writes nothing.
- The widget key is minted only on `CREATE` or explicit `--rotate-widget-key`.
- `pk_live_` + 24 random bytes; only the SHA-256 digest is stored.
- The raw key is printed once, at creation or rotation. It is never returned by
  a read, never logged, and never recoverable — a lost key is rotated, not
  looked up.

## Staff configuration API

`/staff/tenant-config/*`, capability-gated on `integration.manage` and
tenant-scoped through `CurrentTenant`.

| Route | Method | Purpose |
| --- | --- | --- |
| `/staff/tenant-config/public-chat` | GET | Read branding, origins, limits, state |
| `/staff/tenant-config/public-chat` | PATCH | Edit branding/origins, kill switch |
| `/staff/tenant-config/public-chat/rotate-widget-key` | POST | Issue a new key, return it once |
| `/staff/tenant-config/business-integrations` | GET | Provider registry with enabled state |
| `/staff/tenant-config/business-integrations/{provider}` | POST | Toggle a provider |

Three properties are enforced rather than documented:

1. **The selector header is not proof of authorization.** The backend always
   correlates `X-CXOps-Organization-ID` against the caller's membership row, so
   naming another tenant's id yields 403.
2. **No response type can carry a secret.** There is no field for a raw key, a
   digest, a session token, or an integration credential, so none can be
   echoed by accident.
3. **Abuse limits are not staff-editable.** Rate and TTL limits stay
   manifest-owned, so an operator cannot loosen them from a browser.

## Health contract

Three probes, three different contracts, because conflating them causes
outages:

| Probe | Touches the database? | Non-200 meaning |
| --- | --- | --- |
| `/health` | No | Process cannot serve |
| `/ready` | Yes, `SELECT 1` | Not eligible for traffic (503) |
| `/version` | No | Build identity unknown |

`/health` deliberately does not query PostgreSQL. A database blip must not make
a platform restart healthy API instances in a loop; that converts a dependency
outage into a full outage.

A failing readiness check reports the fixed string `unavailable` and never the
driver's exception text, which routinely embeds host, user, and DSN.

## Deployment

```
Vercel  -> frontend (customer site + /chat/embed widget route)
Render  -> cxops-api    (FastAPI, stateless, /ready health check)
        -> cxops-worker (durable integration-job consumer, one per deploy)
        -> cxops-db     (PostgreSQL 16 + pgvector, internal connections only)
```

Both Render services set an explicit `dockerCommand` rather than relying on the
image `CMD`. The API runs `scripts/start_render_api.sh`, which honours `$PORT`
and trusts Render's proxy headers so the app sees `https` behind TLS
termination; without it the image `CMD` serves correctly but builds `http://`
URLs for redirects and secure cookies. The worker runs
`scripts/start_render_worker.sh`.

The worker repeats the web service's production environment values, including
`AUTH_MODE`, `AUTH_JWKS_URL`, `AUTH_DEV_MODE`, and the two public URLs. That
looks redundant, but `app/core/config.py` validates the whole `Settings` object
in every process, so a worker missing any of them is a crash loop rather than a
degraded feature.

Two structural changes from the previous single-process entry point:

- **Migrations left the start command.** `scripts/start_render.sh` ran
  `alembic upgrade head` at boot; on a horizontally scaled service every replica
  would race on DDL. Migrations are now `scripts/run_migrations.sh`, run once
  per deploy. It holds a PostgreSQL session advisory lock on the connection that
  stays open for the duration of the migration, so a second migrator waits
  rather than interleaving DDL; the wait is bounded and fails loudly.
- **The worker became its own service.** It previously ran inside every API
  process, multiplying job consumers by the replica count.

`scripts/start_render.sh` is retained as a deprecated wrapper that prints what
changed and delegates to the API script, so an existing single-instance deploy
keeps working without silently reintroducing either problem.

`uvicorn` runs with `--proxy-headers`, because the app must see the real scheme
behind the platform's TLS terminator — this matters directly now that
production refuses to boot with a non-https `FRONTEND_BASE_URL`.

## Configuration validation

`scripts/validate_production_config.py` is a read-only gate: settings, auth,
secrets, database URL shape, public URLs, tenant manifests, and the
`alembic current` vs `alembic heads` comparison. It reports `PASS` / `WARN` /
`FAIL` and a `READY` / `READY WITH WARNINGS` / `NOT READY` verdict.

It is an *operator* gate, not a security boundary. The real enforcement is in
`app/core/config.py`, which refuses to construct `Settings` in production with
`AUTH_MODE=hs256`, `AUTH_DEV_MODE` on, `DEBUG` on, or a non-https public URL.
The validator's job is to explain a failure *before* a deploy rather than
during it.

Two guarantees worth stating: it never prints a secret value, only presence
and validity; and it never issues a mutating alembic command.

## Pilot smoke

`scripts/smoke_public_chat.py` creates a real session, sends a real message,
requests a human, closes the session, and confirms the closed session refuses
further messages. It prints no message bodies, no tokens, and no keys.

## Audit trail

There is no durable audit table in this phase. Configuration mutations emit
structured logs — `tenant_public_chat_config_updated`,
`tenant_public_chat_key_rotated`, `tenant_business_integration_updated` — with
provider, changed field names, and action. They never carry customer values,
tokens, key material, key digests, or authorization headers.

This is sufficient for a single-tenant pilot and avoids a new table for data
that is currently read by operators, not queried. If the audit requirement
becomes query-based, the log event names are already the schema.

## What is deliberately not here

- No migration. Every capability in this phase uses existing columns.
- No `/embed/loader.js`. The widget is an iframe to `/chat/embed?key=`, which
  needs no first-party script on the customer site.
- No CORS or global CSP changes. The widget route is same-origin from the
  frontend, and the allowlist is enforced server-side per widget.
- No real A1 valuations, quotes, payments, or pickups. `local_demo` is the only
  shipped provider mode, and the validator fails any manifest declaring another.
