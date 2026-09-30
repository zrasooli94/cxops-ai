# Production environment contract

The single authoritative checklist for a CXOps live-pilot deployment. Backend
(API), worker, and frontend all draw from this file; the per-role sections say
who needs what and why.

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

1. **Never commit a production secret.** `config/replit/deployment-env.yaml`
   marks every one `secret: true`; the operator sets it as a Replit Secret on
   each deployment. A placeholder that reaches production is a silent outage
   or, worse, a working default key. (The previous Render blueprint is archived
   at `docs/archive/render.yaml` and is not authoritative.)
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

## Backend + frontend (Replit web deployment `cxops-web`)

| Variable | Requirement | Why |
| --- | --- | --- |
| `ENVIRONMENT` | `production` | Selects production validation rules. Anything else is a `WARN` — the rules still apply, but you are not running the configuration you think you are. |
| `DEBUG` | `false` | `FAIL` if on. Disables debug responses and tracebacks. |
| `EXTERNAL_DATABASE_URL` | External TLS PostgreSQL URL, currently Neon. **Not** localhost; encryption required, not optional; credentials present | The one database setting an operator supplies. The application reads `DATABASE_URL`, so each launcher runs `export DATABASE_URL="$EXTERNAL_DATABASE_URL"` before the preflight and before startup. Replit's platform-managed `DATABASE_URL` is deliberately ignored. `FAIL` on localhost or with TLS off. See "The production database" below. |
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
| `EXTERNAL_DATABASE_URL` | **The same secret, the same database as `cxops-web`**: external TLS PostgreSQL, currently Neon | The worker consumes integration jobs and writes `BusinessAction` rows. A different database means jobs are consumed against a schema the API never reads — and both processes connect successfully, so the split never surfaces as an error. Mapped to `DATABASE_URL` exactly as on `cxops-web`, before the preflight and before the worker starts. |
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

## The production database

Production PostgreSQL is **external** — currently a Neon database, reached over
TLS. It is not a Replit-managed database, and no Replit-managed production
database is created or used.

There are two names for that one database, deliberately. Confusing them is the
most likely way to break a deployment, so the distinction is stated here rather
than left to be rediscovered:

| Where | Name | Who supplies it |
| --- | --- | --- |
| The running services (`cxops-web`, `cxops-worker`) | `EXTERNAL_DATABASE_URL` | The operator, as a Replit Secret on **both** deployments |
| `scripts/run_migrations.sh` | `DATABASE_URL` | The operator, inline, per invocation |

**The mapping, and why it is unconditional.** The application reads
`DATABASE_URL`. Each launcher derives it before anything reads it:

```sh
export DATABASE_URL="$EXTERNAL_DATABASE_URL"
```

In both launchers this runs before `scripts/preflight_production_env.py` and
before the application process starts. The preflight is the first thing that
reads `DATABASE_URL`, so a mapping placed after it would mean the gate validated
one database while the process connected to another — both steps reporting
success, with the only check that could have caught it already run.

Replit also publishes a platform-managed `DATABASE_URL` for this project. That
value is **deliberately ignored**, and is never used as a fallback. The two
share a name, so which one wins depends on injection order — and injection order
is not a property this repository can observe or assert. It differs between a
workspace, a Reserved VM, and a redeploy of the same commit. Assigning over the
top removes the question instead of betting on it. A missing
`EXTERNAL_DATABASE_URL` is then a loud failure at boot, with a message naming
the variable, rather than a silent connection to a database nobody chose.

**Never set `DATABASE_URL` as a deployment secret.** The launchers overwrite it.
Setting it is at best redundant and at worst reintroduces exactly the precedence
ambiguity the mapping exists to remove.

**Migrations take `DATABASE_URL`, not `EXTERNAL_DATABASE_URL`, on purpose.**
`scripts/run_migrations.sh` is run by an operator from a one-off shell, outside
both launchers, so it inherits no mapping step — the `export` above lives inside
those scripts and does not exist anywhere else. The same Neon value is supplied
under the name this script reads:

```sh
DATABASE_URL='<Neon direct URL>' scripts/run_migrations.sh
```

Do not assume the service secret flows into the migration step. It does not, and
running it against the wrong value either fails loudly or — worse — succeeds
against one database while the services point at another.

**The current database is already migrated.** Production Neon is at Alembic head
`1p4a0001` with `pgvector` installed. There is no pending schema work.
Migrations stay a deliberate, out-of-band, once-per-change step for *future*
changes, and never run at boot.

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
| A database IP allowlist | Production PostgreSQL is external (Neon) and reachable over TLS, so there is no internal network boundary to allow from. Access is controlled by the database credential. An allowlist would need a stable egress IP this topology does not have, and is not a current requirement. This is the reverse of the previous topology, where an internal-only database needed no public exposure. |
| `MIGRATION_LOCK_WAIT_SECONDS` | Only read by `scripts/run_migrations.sh`. Default 300s. It is a migration-step variable, not a service variable. |
