# Production environment contract

The single authoritative checklist for a CXOps live-pilot deployment. Frontend,
API, and worker all draw from this file; the per-role sections say who needs
what and why.

## The production topology

Five services, each with one job:

| Layer | Host | Serves | Start command |
| --- | --- | --- | --- |
| Frontend | **Vercel** | Next.js: public widget, staff Control Center, same-origin BFF | build: `npm run build` |
| API | **Render** (Singapore) | FastAPI/uvicorn | `scripts/start_render_api.sh` |
| Worker | **Render** (Singapore) | Durable integration-job consumer, no published port | `scripts/start_render_worker.sh` |
| Auth | **Nhost** | Staff identity; JWKS issuer | — |
| Database | **Neon** (Singapore) | PostgreSQL + `pgvector`, at Alembic head `1p4a0001` | — |

`scripts/start_render.sh` remains as a compatibility shim that delegates to
`scripts/start_render_api.sh`, because an existing Render service still points
its start command there.

**Neon is the only production database.** Do not create or attach a
Render-managed PostgreSQL database to either service. A second database would be
a decoy holding a copy of customer-shaped data that nothing protects, and the
two services would connect to it successfully, so the split would never surface
as an error.

**No service start command runs migrations.** Every replica re-runs its start
command, so a migration there races itself on DDL locks. Migrations are a single
explicit step — see "The production database" below.

Validate any environment mechanically with:

```sh
# The launchers map this for you at boot. A manual shell has to do it itself,
# because scripts/validate_production_config.py reads DATABASE_URL.
export DATABASE_URL="$EXTERNAL_DATABASE_URL"
.venv/bin/python scripts/validate_production_config.py --environment production
```

Expected result: `READY`. That script is the enforcement of the backend column
below. This document is the *intent* — it records why each value exists, which
the script cannot.

## Rules that apply to every value

1. **Never commit a production secret.** Secrets are set in the Vercel and
   Render dashboards, never in this repository. A placeholder that reaches
   production is a silent outage or, worse, a working default key.
2. **Secret values are never printed.** The validator reports presence, count,
   and validity. Never `echo`, `print`, or log a secret, not even in a
   debugging attempt. The launchers log the *name* of the database variable
   they bound and never its value.
3. **Transport is TLS everywhere.** Public URLs are `https`; the production
   database connection requires TLS — encryption, not a preference. `http` on
   a public URL is a `FAIL`, not a warning.
4. **`DEBUG` is `false` in production.** It is a `FAIL`, not a warning, because
   debug responses leak internals.
5. **A value that is required for the process to *construct* belongs on every
   replica and the worker.** Not only on the service that happens to use it.
   `Settings` validates process-wide at import, so a missing value is a crash
   loop, not a degraded feature. This is why the worker repeats the API's auth
   and encryption variables even though it serves no HTTP traffic.

## Render API service (`cxops-api`)

Start command: `scripts/start_render_api.sh`. It runs
`scripts/preflight_production_env.py` first and refuses to start on failure, so
every value below is validated before uvicorn binds.

| Variable | Requirement | Why |
| --- | --- | --- |
| `ENVIRONMENT` | `production` | Selects production validation rules. Anything else is a `WARN` — the rules still apply, but you are not running the configuration you think you are. |
| `DEBUG` | `false` | `FAIL` if on. Disables debug responses and tracebacks. |
| `DATABASE_URL` | **Neon direct (unpooled) TLS URL.** **Not** localhost; encryption required, not optional; credentials present | The application reads this name directly, so it is set as a Render environment variable on both services. `FAIL` on localhost, on `sslmode=prefer`, or with TLS off. See "The production database" below. |
| `FRONTEND_BASE_URL` | **Vercel production origin**, `https://…` | `FAIL` on `http`. Used to build embed snippets and pilot links. |
| `BACKEND_PUBLIC_URL` | **This Render API's own origin**, `https://…` | `FAIL` on `http`. Used for links in staff-visible messages and for CORS. The app refuses to boot without `https` in production. |
| `AUTH_MODE` | `jwks` | `FAIL` on `hs256`. Production must validate tokens against Nhost's real issuer. |
| `AUTH_JWKS_URL` | Nhost's `https://…` JWKS endpoint | Required when `AUTH_MODE=jwks`. `FAIL` if `http`. This is where the API fetches keys from; it must be the issuer's, not a copy. |
| `AUTH_JWT_ISSUER` | Nhost project URL (the `iss` claim) | The API's `iss` check. A mismatch rejects every staff token. |
| `AUTH_JWT_AUDIENCE` | The intended client identifier | The API's `aud` check. |
| `AUTH_DEV_MODE` | `false` | `FAIL` if on. Dev mode accepts unsigned/synthetic local identities. |
| `ENCRYPTION_KEYS` | At least one valid Fernet key | `FAIL` if unset, unparseable, or containing no usable key. Stored provider credentials are unprotectable without it. Comma-separated; the first is active and the rest are decrypt-only, which is what makes rotation possible. **Losing every key makes existing stored credentials unrecoverable.** |
| `OPENAI_API_KEY` | Present | `FAIL` if unset. The agent workflow constructs `ChatOpenAI` at module import, so a missing key is a boot failure, not a degraded feature. |
| `FORWARDED_ALLOW_IPS` | `*` | Render terminates TLS and forwards, so the proxy is the only source of forwarded headers. uvicorn is started with `--proxy-headers`; without trusting the proxy the app builds `http://` URLs and marks cookies insecure behind that termination. |
| `PORT` | Assigned by Render | The launcher binds `0.0.0.0:$PORT`. Do not hardcode it. |
| `ZENDESK_WEBHOOK_SECRET` | If Zendesk enabled | `WARN` if absent. Not a pilot blocker. |
| `TICKET_EVENT_WEBHOOK_SECRET` | If ticket webhooks enabled | `WARN` if absent. Not a pilot blocker. |
| `ZENDESK_SUBDOMAIN` / `ZENDESK_CLIENT_ID` / `ZENDESK_CLIENT_SECRET` / `ZENDESK_REDIRECT_URI` | If Zendesk OAuth enabled | Not used by the A1 pilot. |

## Render worker service (`cxops-worker`)

Start command: `scripts/start_render_worker.sh`. The same production preflight
runs first.

The worker has no HTTP surface, so it has no health check and no published port.
It still imports `app.core.config`, whose production validation is
**process-wide**: it refuses to import at all without `https` public URLs,
`AUTH_MODE=jwks` with a real JWKS URL, `AUTH_DEV_MODE=false`, and
`ENCRYPTION_KEYS`. Every one of those is therefore required here too.

| Variable | Requirement | Why |
| --- | --- | --- |
| `ENVIRONMENT` | `production` | Same process-wide validation. |
| `DEBUG` | `false` | Same. |
| `DATABASE_URL` | **The same Neon URL as the API** | The worker consumes integration jobs and writes `BusinessAction` rows. A different database means jobs are consumed against a schema the API never reads — and both processes connect successfully, so the split never surfaces as an error. |
| `AUTH_MODE`, `AUTH_JWKS_URL`, `AUTH_JWT_AUDIENCE`, `AUTH_JWT_ISSUER`, `AUTH_DEV_MODE` | Same as the API | Required to construct `Settings`; a missing value is a crash loop. The worker authenticates no inbound request, but it must still load the same validated configuration object. |
| `ENCRYPTION_KEYS` | Same value as the API | The worker reads stored provider credentials to execute integration jobs. A different key set means it cannot decrypt what the API wrote. |
| `OPENAI_API_KEY` | Present | Integration jobs can reach the agent workflow, which constructs `ChatOpenAI` at import. |
| `FRONTEND_BASE_URL`, `BACKEND_PUBLIC_URL` | Same as the API | Required to construct `Settings` in production. |
| `ZENDESK_WEBHOOK_SECRET`, `TICKET_EVENT_WEBHOOK_SECRET` | If those integrations enabled | The worker sends the webhooks. |

The worker must **not** require or publish a port. It serves no traffic, and
configuring a health check for one is a sign the service is misconfigured. Its
Prometheus listener on `WORKER_METRICS_PORT` (default `9101`) is not published;
set it to `0` only if the platform forbids extra listeners, at the cost of losing
the metrics that distinguish "idle" from "dead".

## Vercel frontend

| Variable | Requirement | Why |
| --- | --- | --- |
| `BACKEND_API_URL` | **Render API https origin**, read at request time | The server-side BFF and Control Center modules call this. It is read from `process.env` per request, so one built image can be pointed at a different API without a rebuild. Only the server ever sees it — the browser stays same-origin with Vercel, which is why the public chat route needs no CORS. |
| `CXOPS_PUBLIC_SITE_URL` | **Vercel production origin**, `https://…`, present before the build | Inlined into the build by `robots.ts`, `sitemap.ts`, and static metadata. A runtime-only value produces a site that advertises `http://localhost:3000` to every crawler, and it fails silently: the site works, the metadata does not. Vercel also provides `VERCEL_PROJECT_PRODUCTION_URL` / `VERCEL_URL`. |
| `NEXT_PUBLIC_NHOST_SUBDOMAIN` | Nhost project subdomain | Public by construction. Embedded in the client bundle. |
| `NEXT_PUBLIC_NHOST_REGION` | Nhost region (e.g. `ap-southeast-1`) | Same. |
| `NEXT_STANDALONE_OUTPUT` | `off` where Vercel requires it | Vercel runs `next start` behind its own routing, which cannot use a standalone server output. The name describes the capability being turned off, not a hosting provider. |
| Public staff auth (`NEXT_PUBLIC_*`) | Subdomain and region only | The only `NEXT_PUBLIC_*` values in this codebase are `NEXT_PUBLIC_NHOST_SUBDOMAIN` and `NEXT_PUBLIC_NHOST_REGION`. **No server secret may ever take a `NEXT_PUBLIC_` prefix** — those values are inlined into the client bundle at build time and are readable by anyone who loads the page. |
| Server-only secrets | Never `NEXT_PUBLIC_` | The BFF reads server secrets from non-public env names. See "Env boundary" below. |

### Env boundary

- `NEXT_PUBLIC_*` is inlined into the browser bundle. Public by definition.
- Everything else is server-side only and must stay that way.
- The public chat BFF route (`/api/public/chat/*`) is unauthenticated by
  design — it is the widget's server-side proxy. It carries no staff identity and
  returns no-store.
- Staff routes stay authenticated. The staff tenant selector is a UI affordance;
  authorization is enforced server-side from the token's membership claims, so
  selecting another tenant in the UI cannot widen access.

## What is deliberately *not* required

| Value | Why not |
| --- | --- |
| A real business-provider credential | The A1 integration is `provider_mode: local_demo`. No A1 API exists in this build, so there is nothing to authenticate against. This is the single most important line in this document: **the pilot cannot perform a real valuation, quote, or pickup**, and no environment variable can change that. |
| A database IP allowlist | Production PostgreSQL is external (Neon) and reachable over TLS, so there is no internal network boundary to allow from. Access is controlled by the database credential. An allowlist would need a stable egress IP this topology does not have, and is not a current requirement. |
| A Render-managed PostgreSQL database | Neon is the production database. Creating a Render database would produce a second, unprotected copy of customer-shaped data that nothing points at, and both services would connect to whichever they were configured with — so the split would never surface as an error. |
| `MIGRATION_LOCK_WAIT_SECONDS` | Only read by `scripts/run_migrations.sh`. Default 300s. It is a migration-step variable, not a service variable. |
