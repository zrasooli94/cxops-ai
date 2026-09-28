# Replit production deployment runbook

The procedure for deploying CXOps AI to Replit, replacing the previous
Render/Vercel topology. Read
[production-environment-contract.md](production-environment-contract.md) for
*what* each value must be; this file is *how* to apply it.

> **Resource approval.** Nothing in this runbook has been executed. CXOps
> requires a paid Replit Reserved VM and a managed PostgreSQL database. Before
> creating either, stop and present
> `REPLIT_RESOURCE_CREATION_APPROVAL_REQUIRED` with the resource type, the
> configuration, and the reason. Approval for one resource is not approval for
> the other, and approval to create is not approval to enable a payment method.

## 1. Topology

One Replit project, two deployments from one source tree.

| Deployment | Serves | Published port |
| --- | --- | --- |
| `cxops-web` | Next.js frontend, public widget, staff Control Center; FastAPI on loopback behind a same-origin BFF | yes, `PORT` |
| `cxops-worker` | Durable integration-job consumer | none |

Plus one managed PostgreSQL database, 12 or newer, with `pgvector`.

**Why one published port.** The browser talks to exactly one origin. The widget,
the Control Center, and every staff API call are same-origin, which is why the
app needs no CORS configuration and why `BACKEND_PUBLIC_URL` equals
`FRONTEND_BASE_URL`. FastAPI binds `127.0.0.1` and is reachable only from the
frontend process on the same machine. A second published port would not add
capacity — it would add a second origin, a CORS policy, and a second thing to
keep in sync.

**Trade-off, stated plainly.** This means FastAPI is *not* directly reachable
at its own public URL. Anything that needs to call the API as an API — a
server-to-server integration, an external monitoring probe with a hardcoded
host — must go through the published origin instead. `/health`, `/ready`, and
`/version` are proxied through Next.js precisely so operators and probes still
have one public entry point. If a future requirement needs the API itself
published, that is a second deployment with its own domain, not a port on this
one.

## 2. One-time project setup

1. Create the Replit project from this repository. The `.replit` file pins the
   run and deployment commands; `replit.nix` pins the system packages
   (Python 3.12, Node 22, PostgreSQL client, build tooling).
2. Create the managed PostgreSQL database. Enable the vector extension as the
   database owner:

   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   ```

   This is a manual step, not a migration. The application's own database user
   frequently lacks the rights to create an extension, and the migration that
   would create it (`ceec6d2a6a89`) runs with that user — so if it is missing
   there, the migration either fails or silently leaves the column unusable.
   `scripts/validate_production_config.py` checks for the extension precisely
   because a missing one is otherwise discovered by the first RAG query.

3. Set every value in `config/replit/deployment-env.yaml` marked `secret: true`
   as a Replit Secret on the relevant deployment. Set the non-secret values
   (`ENVIRONMENT`, `DEBUG`, `PYTHONPATH`, `AUTH_MODE`, `AUTH_DEV_MODE`) as
   environment variables.

   Secrets are set **per deployment**, not project-wide. The worker needs
   `ENCRYPTION_KEYS` and `DATABASE_URL`; `cxops-web` additionally needs the
   Zendesk OAuth values. Giving both deployments every value would put an OAuth
   client secret on a process that never runs an OAuth client.

4. Record the public domain of `cxops-web` and set, on `cxops-web`:
   - `FRONTEND_BASE_URL=https://<public-domain>`
   - `BACKEND_PUBLIC_URL=https://<public-domain>` (the same value)
   - `CXOPS_PUBLIC_SITE_URL=https://<public-domain>`

   `CXOPS_PUBLIC_SITE_URL` must be set **before the first build**. It is read at
   build time by `robots.ts`, `sitemap.ts`, and static metadata; setting it on
   the running server afterwards changes nothing, and the site advertises
   `http://localhost:3000` to every crawler. `scripts/start_replit_web.sh`
   refuses to start without it, which converts a silent SEO bug into a loud
   start-up failure.

## 3. Migrations: explicit, before the deploy, never at boot

```sh
DATABASE_URL='postgresql+asyncpg://…' scripts/run_migrations.sh
```

Run this once per schema change, from a one-off shell, **before** deploying the
code that needs it. Never from a start command: every restart re-runs the start
command, so a migration there races itself and produces DDL errors that read
like application bugs. The worker is likewise a single consumer per deployment
rather than one per web replica, so a migration on the worker would be
unnecessary and would race its own restart.

The script takes an advisory lock, so two concurrent runs serialise instead of
colliding. That is a safety net, not permission to automate it into a boot.

Order matters: migrate, then deploy. The reverse deploys code that expects a
column that does not exist yet.

## 4. Deploy

Deploy `cxops-web` and `cxops-worker` from the same commit. `cxops-web` runs
`scripts/start_replit_web.sh`, which:

1. creates the virtualenv and installs `requirements-lock.txt` when the venv is
   missing,
2. runs `scripts/preflight_production_env.py` — production posture, https
   public URLs, JWKS auth, encryption keys, non-localhost database — and
   **exits non-zero on any violation**,
3. builds the frontend with `CXOPS_PUBLIC_SITE_URL` present,
4. starts FastAPI on loopback and Next.js on `PORT` under a supervisor that
   forwards `SIGTERM` and exits non-zero if either child dies.

`cxops-worker` runs `scripts/start_replit_worker.sh`, which does the same
preflight then `exec`s the worker. It runs no migration.

The preflight is deliberately before the servers, not after: a process that
starts and then discovers it has no encryption key has already accepted traffic.

## 5. Verify

```sh
# 1. Configuration and database capabilities
.venv/bin/python scripts/validate_production_config.py --environment production
#    Expect: READY. It now also probes PostgreSQL version, pgvector, and the
#    one-configuration-per-tenant constraint.

# 2. Liveness, readiness, and version through the public origin
curl -fsS https://<public-domain>/health
curl -fsS https://<public-domain>/ready
curl -fsS https://<public-domain>/version

# 3. End-to-end widget path
.venv/bin/python scripts/smoke_public_chat.py --frontend-base-url https://<public-domain>
```

`/ready` must report the expected Alembic head. If `/ready` or `/version`
returns `404`, the deployment is running pre-Phase-1P.3 code, not a broken
route — a stale deployment and a missing route look identical from the outside,
which is why step 2 exists.

## 6. Tenants

A1 and RISPU are **separate Replit deployments** that embed the central CXOps
widget. Nothing about the widget's framing depends on where those sites are
hosted:

- `config/tenants/<tenant>.yaml` is the source of truth for allowed origins.
- `frontend/next.config.ts` holds the matching `frame-ancestors` allowlist.
- Onboarding a tenant means setting its real custom domain in both places, plus
  its `CXOPS_PUBLIC_SITE_URL`-derived snippet and widget key.

**No `*.replit.app` wildcard, ever.** Every Replit deployment gets a sibling
subdomain of one shared parent, so a wildcard there is not a narrow exception —
it is every tenant on the platform, including an account created this
afternoon. The widget carries a tenant's public key in its URL, so a hostile
parent could frame it, read the key, and repoint the victim at a lookalike
origin. `tests/test_deployment_env_parity.py` fails the build if a wildcard
appears in the security policy.

A1 currently ships in `provider_mode: local_demo` with both production origins
already declared. No environment variable changes that; see the pilot runbook.

## 7. Rollback

Revert to the previous deployment of each Replit deployment. This is safe
because **migrations are additive and forward-only**: the previous code tolerates
the new schema. It is not safe in the other direction — code that depends on a
new column cannot roll back past a migration that created it without also
reverting the schema, so roll back code immediately and leave the extra column
in place.

If a migration must be undone, write a new `downgrade()` and run it explicitly.
Never `alembic downgrade` against production to "get back to a known state":
the additive-only property that makes rollback safe is the thing that gets
destroyed the moment you use it under pressure.

## 8. What this runbook does not cover

- **Rollback of a bad migration.** Deliberately; see above.
- **Multi-region or HA.** Two deployments is a topology, not a redundancy
  strategy. The worker's job claiming is row-locked, so it is *safe* to run two
  workers, but only one consumer per deployment is the intended configuration.
- **Anything about the previous platform.** The Render blueprint is archived at
  `docs/archive/render.yaml` for history only, and the `start_render_*.sh`
  scripts are retained unchanged but deprecated. Neither is authoritative.
