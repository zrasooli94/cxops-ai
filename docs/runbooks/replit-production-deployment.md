# Replit production deployment runbook

The procedure for deploying CXOps AI to Replit, replacing the previous
Render/Vercel topology. Read
[production-environment-contract.md](production-environment-contract.md) for
*what* each value must be; this file is *how* to apply it.

> **Resource approval.** Nothing in this runbook has been executed. CXOps
> requires two paid Replit Reserved VM deployments and an external PostgreSQL
> database. Before creating any of them, stop and present
> `REPLIT_RESOURCE_CREATION_APPROVAL_REQUIRED` with the resource type, the
> configuration, and the reason. Approval for one resource is not approval for
> the others, and approval to create is not approval to enable a payment method.

## 1. Topology

One Replit project, two deployments from one source tree. Both are **Reserved
VMs**, chosen as the deployment type in the Replit Publishing UI when the
deployment is created. `.replit` deliberately carries no `deploymentTarget`
key: Replit's own schema validator rejected the only value that was ever there,
so it was never taking effect — it was a claim in a comment, not configuration.

| Deployment | Serves | Deployment type | Published port |
| --- | --- | --- | --- |
| `cxops-web` | Next.js frontend, public widget, staff Control Center; FastAPI on loopback behind a same-origin BFF | Reserved VM | yes, `PORT` |
| `cxops-worker` | Durable integration-job consumer | Reserved VM | none |

Plus **one external PostgreSQL database** (currently Neon), 12 or newer, with
`pgvector`, already migrated to Alembic head `1p4a0001`.

**There is no Replit-managed production database.** In the Publishing UI, for
both deployments:

- **Create production database = OFF**
- **Set up production database with current development data = OFF**

Both must be off. Replit's managed PostgreSQL would be a *second* database,
seeded from development data, and nothing in this architecture points the
services at it — so it would be a decoy holding a copy of customer-shaped data
that nothing is protecting. Leaving either toggle on is the failure this
runbook most needs to prevent.

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

1. Create the Replit project from this repository. `.replit` pins the deployment
   build and run commands, the language modules (Python 3.12, Node 22), and the
   system packages. There is **no `replit.nix`**, and there must not be one: the
   system dependency list lives in `.replit` under `[nix]`, and two files
   declaring the same dependencies would be a second source of truth for the
   same thing — which is how the invalid nix attributes ended up committed in
   the first place.

2. Provision the external PostgreSQL database (Neon) and enable the vector
   extension as the database owner:

   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   ```

   This is a manual step, not a migration. The application's own database user
   frequently lacks the rights to create an extension, and the migration that
   would create it (`ceec6d2a6a89`) runs with that user — so if it is missing
   there, the migration either fails or silently leaves the column unusable.
   `scripts/validate_production_config.py` checks for the extension precisely
   because a missing one is otherwise discovered by the first RAG query.

   Then migrate it once, out of band, exactly as in section 3. Production is
   already at head `1p4a0001` with `pgvector`; there is no pending schema work.

3. Set every value in `config/replit/deployment-env.yaml` marked `secret: true`
   as a Replit Secret on the relevant deployment. Set the non-secret values
   (`ENVIRONMENT`, `DEBUG`, `PYTHONPATH`, `AUTH_MODE`, `AUTH_DEV_MODE`) as
   environment variables.

   Secrets are set **per deployment**, not project-wide. Both deployments need
   `EXTERNAL_DATABASE_URL` and `ENCRYPTION_KEYS`; `cxops-web` additionally
   needs the Zendesk OAuth values. Giving both deployments every value would
   put an OAuth client secret on a process that never runs an OAuth client.

   **`EXTERNAL_DATABASE_URL`** is the one database setting an operator supplies.
   Set the **same** Neon value on **both** deployments. Each launcher then runs

   ```sh
   export DATABASE_URL="$EXTERNAL_DATABASE_URL"
   ```

   before the production preflight and before the application process starts.
   Do **not** set `DATABASE_URL` as a deployment secret: Replit injects a
   platform-managed one, and the launchers overwrite it deliberately so the
   services cannot land on that database by accident. See
   [production-environment-contract.md](production-environment-contract.md) for
   why the assignment is unconditional rather than a fallback.

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
DATABASE_URL='<Neon direct URL>' scripts/run_migrations.sh
```

**The name here is intentionally `DATABASE_URL`, not `EXTERNAL_DATABASE_URL`.**
`scripts/run_migrations.sh` is run by an operator from a one-off shell, outside
both launchers, so it inherits no mapping — the launchers'
`export DATABASE_URL="$EXTERNAL_DATABASE_URL"` is a step inside those scripts
and exists nowhere else. The same Neon value is supplied under the name this
script reads. Do not assume the service secret flows into the migration step; it
does not.

Prefer the Neon **direct** (unpooled) URL here rather than the pooled one the
services use. A migration holds a session and an advisory lock for as long as
it takes, and a pooler is free to hand that connection away or cut it. A direct
URL costs nothing extra, because the only client is a human running a one-off
command.

Run this once per schema change, from a one-off shell, **before** deploying the
code that needs it. Never from a start command: every restart re-runs the start
command, so a migration there races itself and produces DDL errors that read
like application bugs. Neither launcher runs a migration, and
`tests/test_deployment_env_parity.py` fails the build if one appears. The worker
is likewise a single consumer per deployment rather than one per web replica, so
a migration on the worker would be unnecessary and would race its own restart.

The script takes an advisory lock, so two concurrent runs serialise instead of
colliding. That is a safety net, not permission to automate it into a boot.

Order matters: migrate, then deploy. The reverse deploys code that expects a
column that does not exist yet.

Production is already at head `1p4a0001` with `pgvector`, so there is nothing
to run before the first deploy of this topology. The step exists for the next
schema change.

## 4. Deploy

Deploy `cxops-web` and `cxops-worker` from the same commit.

### `cxops-web`: build once, start cheap

The web deployment has two commands, and the split is load-bearing rather than
cosmetic:

| Phase | `.replit` key | Script |
| --- | --- | --- |
| Build | `[deployment] build` | `scripts/build_replit_web.sh` |
| Run | `[deployment] run` | `scripts/start_replit_web.sh` |

**`scripts/build_replit_web.sh`** runs once, before the deployment starts, and
owns everything that must not repeat:

1. checks the build-time variables `next build` inlines into the bundle
   (`CXOPS_PUBLIC_SITE_URL`, `NEXT_PUBLIC_NHOST_SUBDOMAIN`,
   `NEXT_PUBLIC_NHOST_REGION`),
2. `pip install --target .replit-python/ -r requirements.txt` with the system
   interpreter — no venv, no PATH change, no mutation of the image's
   site-packages — then verifies the packages actually import from that
   directory,
3. `npm ci`,
4. `npm run build` (the Next.js production bundle),
5. stages `.next/static` and `public/` into the standalone output and verifies
   `frontend/.next/standalone/server.js` exists.

**`scripts/start_replit_web.sh`** runs on every container start and installs
**nothing** — no venv, no `pip install`, no `npm ci`, no `next build`. It:

1. fails immediately if the prebuilt standalone server is absent, naming the
   build phase as the remedy rather than suggesting a restart,
2. maps `EXTERNAL_DATABASE_URL` onto `DATABASE_URL`,
3. prepends `.replit-python/` to `PYTHONPATH` and checks the packaged packages
   import from it,
4. runs `scripts/preflight_production_env.py` — production posture, https
   public URLs, JWKS auth, encryption keys, non-localhost database — and
   **exits non-zero on any violation**,
5. starts FastAPI on loopback and Next.js on `PORT` under a supervisor that
   forwards `SIGTERM` and exits non-zero if either child dies.

Why the split. The frontend build takes minutes, and it used to run inside the
start command — on every restart, before anything bound the published port. A
healthy deployment was therefore reported as `hostingpid1: an open port was not
detected`, because the port opened after the health check's window rather than
after the build. Replit's Publishing tool does pass deployment configuration to
the build command, so the split is available. The same reasoning applies to the
Python half: the platform does not promise to install `requirements.txt` for a
hosted deployment, so the build phase does it into a project-local `--target`
directory and the run phase imports from there.
`tests/test_replit_config.py` fails the build if the runtime launcher grows a
venv, a `pip install`, or a Next build back.

### `cxops-worker`: same database, separate process

`cxops-worker` is a **separate deployment** with its own Run command,
`scripts/start_replit_worker.sh`, selected when that deployment is created. It
is not a second process inside the web deployment: one consumer per deployment
is the intended shape, and a job consumer inside every web replica would
multiply consumers by the replica count.

It maps the same `EXTERNAL_DATABASE_URL` onto `DATABASE_URL` — before anything
else runs, so a missing secret is reported immediately rather than after a venv
build — then creates `.venv` on a fresh VM, installs `requirements.txt` into it
if `app` does not already import, runs the same preflight, and `exec`s the
worker. It runs no migration.

The worker still installs into a venv where the web deployment does not: it
imports `app.core.encryption`, so it needs the project's own dependencies, and
`.replit-python/` is produced by the web deployment's build phase. Its
preflight is deliberately before the worker, not after: a process that starts
and then discovers it has no encryption key has already begun claiming jobs.

## 5. Verify

```sh
# 1. Configuration and database capabilities
#    From a shell that has the mapped value, exactly as the launchers set it at
#    boot -- the validator reads DATABASE_URL, not EXTERNAL_DATABASE_URL.
export DATABASE_URL="$EXTERNAL_DATABASE_URL"
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
