# Production environment contract

The single authoritative checklist for a CXOps live-pilot deployment. Backend
(API), worker, and frontend all draw from this file; the per-role sections say
who needs what and why.

Validate any environment mechanically with:

```sh
.venv/bin/python scripts/validate_production_config.py --environment production
```

Expected result: `READY`. That script is the enforcement of the backend column
below. This document is the *intent* — it records why each value exists, which
the script cannot.

## Rules that apply to every value

1. **Never commit a production secret.** `config/replit/deployment-env.yaml`
   marks every one `secret: true`; the operator sets it as a Replit Secret on
   each deployment. A placeholder that reaches production is a silent outage
   or, worse, a working default key. (The previous Render blueprint is archived
   at `docs/archive/render.yaml` and is not authoritative.)
2. **Secret values are never printed.** The validator reports presence, count,
   and validity. Never `echo`, `print`, or log a secret, not even in a
   debugging attempt.
3. **Transport is TLS everywhere.** Public URLs are `https`; the database
   requests TLS. `http` on a public URL is a `FAIL`, not a warning.
4. **`DEBUG` is `false` in production.** It is a `FAIL`, not a warning, because
   debug responses leak internals.
5. **A value that is required for the process to *construct* belongs on every
   replica and the worker.** Not only on the service that happens to use it.
   `Settings` validates process-wide at import, so a missing value is a crash
   loop, not a degraded feature. This is why the worker repeats the API's auth
   and encryption variables even though it serves no HTTP traffic.

## Backend + frontend (Replit web deployment `cxops-web`)

| Variable | Requirement | Why |
| --- | --- | --- |
| `ENVIRONMENT` | `production` | Selects production validation rules. Anything else is a `WARN` — the rules still apply, but you are not running the configuration you think you are. |
| `DEBUG` | `false` | `FAIL` if on. Disables debug responses and tracebacks. |
| `DATABASE_URL` | Managed PostgreSQL 12+, **not** localhost, TLS requested, credentials present | `FAIL` on localhost. Prefer the internal URL Replit injects from the managed database so traffic stays on the private network. |
| `AUTH_MODE` | `jwks` | `FAIL` on `hs256`. Production must validate tokens against a real issuer. |
| `AUTH_JWKS_URL` | `https://…` | Required when `AUTH_MODE=jwks`. `FAIL` if `http`. This is the URL the API fetches keys from; it must be the issuer's, not a copy. |
| `AUTH_JWT_AUDIENCE` | The intended client identifier | The API's `aud` check. A mismatch rejects every staff token. |
| `AUTH_JWT_ISSUER` | The issuing authority | The API's `iss` check. |
| `AUTH_DEV_MODE` | `false` | `FAIL` if on. Dev mode accepts unsigned/synthetic local identities. |
| `ENCRYPTION_KEYS` | At least one valid Fernet key | `FAIL` if unset, unparseable, or containing no usable key. Stored provider credentials are unprotectable without it. Comma-separated; the first is active and the rest are decrypt-only, which is what makes rotation possible. **Losing every key makes existing stored credentials unrecoverable.** |
| `OPENAI_API_KEY` | Present | `FAIL` if unset. The agent workflow constructs `ChatOpenAI` at module import, so a missing key is a boot failure, not a degraded feature. |
| `BACKEND_PUBLIC_URL` | `https://…` | `FAIL` on `http`. Used for links in staff-visible messages and for CORS. The app refuses to boot without `https` in production. |
| `FRONTEND_BASE_URL` | `https://…` | `FAIL` on `http`. Used to build embed snippets and pilot links. |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` (the default) | **Not `*`.** On this topology the API is bound to loopback and is reached only by the frontend on the same machine, so the only source whose forwarded headers are trusted is the local one. A wildcard here would let anything that can open the socket dictate the scheme the app believes it is served over. |
| `INTERNAL_API_PORT` | `8000` (the default) | Loopback port for FastAPI. Not published: only Next.js binds the platform `PORT`. |
| `PYTHONPATH` | `.` | Repository root, so `app` and `scripts` import. Not `/app` — that was a previous platform's image layout. |
| `ZENDESK_WEBHOOK_SECRET` | If Zendesk enabled | `WARN` if absent. Not a pilot blocker. |
| `TICKET_EVENT_WEBHOOK_SECRET` | If ticket webhooks enabled | `WARN` if absent. Not a pilot blocker. |
| `ZENDESK_SUBDOMAIN` / `ZENDESK_CLIENT_ID` / `ZENDESK_CLIENT_SECRET` / `ZENDESK_REDIRECT_URI` | If Zendesk OAuth enabled | Not used by the A1 pilot. |

## Worker (Replit worker deployment `cxops-worker`)

The worker has no HTTP surface, so it has no health check and no public URL. It
still imports `app.core.config`, whose production validation is **process-wide**:
it refuses to import at all without `https` public URLs, `AUTH_MODE=jwks` with a
real JWKS URL, `AUTH_DEV_MODE=false`, and `ENCRYPTION_KEYS`. Every one of those is
therefore required here too.

| Variable | Requirement | Why |
| --- | --- | --- |
| `ENVIRONMENT` | `production` | Same process-wide validation. |
| `DEBUG` | `false` | Same. |
| `PYTHONPATH` | `.` | Same repository root. |
| `DATABASE_URL` | Same database as the web deployment, internal connection | The worker consumes integration jobs and writes `BusinessAction` rows. A different database means jobs are consumed against a schema the API never reads. |
| `AUTH_MODE`, `AUTH_JWKS_URL`, `AUTH_JWT_AUDIENCE`, `AUTH_JWT_ISSUER`, `AUTH_DEV_MODE` | Same as the API | Required to construct `Settings`; a missing value is a crash loop. The worker authenticates no inbound request, but it must still load the same validated configuration object. |
| `ENCRYPTION_KEYS` | Same value as the API | The worker reads stored provider credentials to execute integration jobs. A different key set means it cannot decrypt what the API wrote. |
| `OPENAI_API_KEY` | Present | Integration jobs can reach the agent workflow, which constructs `ChatOpenAI` at import. |
| `BACKEND_PUBLIC_URL`, `FRONTEND_BASE_URL` | Same as the API | Required to construct `Settings` in production. |
| `ZENDESK_WEBHOOK_SECRET`, `TICKET_EVENT_WEBHOOK_SECRET` | If those integrations enabled | The worker sends the webhooks. |

The worker must **not** require a public port. It serves no traffic, and
configuring a health check for one is a sign the deployment type is wrong. Its
Prometheus listener on `WORKER_METRICS_PORT` (default `9101`) is reachable only
from inside the deployment; set it to `0` only if the platform forbids extra
listeners, at the cost of losing the metrics that distinguish "idle" from
"dead".

## Frontend build

The frontend is not a separate deployment. It is built and served by `cxops-web`
on the same origin as the API, which is what keeps the browser on a single
origin and removes the need for CORS.

| Variable | Requirement | Why |
| --- | --- | --- |
| `CXOPS_PUBLIC_SITE_URL` | `https://` public origin, present **before** `npm run build` | Inlined into the build by `robots.ts`, `sitemap.ts`, and static metadata. A runtime-only value produces a site that advertises `http://localhost:3000` to every crawler, and it fails silently: the site works, the metadata does not. Falls back to `VERCEL_PROJECT_PRODUCTION_URL` / `VERCEL_URL`, then to localhost — which is why the start script refuses to run without it. |
| `BACKEND_API_URL` | Loopback API origin, read at request time | The server-side BFF and Control Center modules. `http://127.0.0.1:$INTERNAL_API_PORT` is correct and is never sent to a browser, which is why this one URL is legitimately not https. |
| Public staff auth (`NEXT_PUBLIC_*`) | Subdomain and region only | The only `NEXT_PUBLIC_*` values in this codebase are `NEXT_PUBLIC_NHOST_SUBDOMAIN` and `NEXT_PUBLIC_NHOST_REGION`, both of which are non-secret by construction. **No server secret may ever take a `NEXT_PUBLIC_` prefix** — those values are inlined into the client bundle at build time and are readable by anyone who loads the page. |
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
| A real public IP allowlist for the database | The managed database is internal-only: only deployments on the same Replit account can open a socket. A public database is not required and would be a regression. |
| `MIGRATION_LOCK_WAIT_SECONDS` | Only read by `scripts/run_migrations.sh`. Default 300s. It is a migration-step variable, not a service variable. |
