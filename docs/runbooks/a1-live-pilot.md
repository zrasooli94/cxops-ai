# A1 live pilot runbook

Operational procedure for the Phase 1P.4 A1 Cash for Cars live pilot.

Every command is read-only unless it explicitly says otherwise. Commands marked
**[MUTATES]** change state; nothing else in this document does.

---

## 0. Scope, and what this pilot is not

The A1 integration is a **local demo**. It returns simulated availability and
quote data and records `BusinessAction` rows in our own database. Nothing in
this pilot reaches A1's systems.

Therefore, for this pilot:

- no valuation, quote, or offer is ever real;
- no pickup is ever booked;
- no payment is ever taken;
- no request reaches A1, and no A1 staff member is notified by the integration.

`config/tenants/a1-cash-for-cars.yaml` sets `provider_mode: local_demo` and
`pilot.state: pilot`. Both are load-bearing. `enabled: true` on the A1 provider
means *"the agent may offer these tools to this tenant"*, **not** *"these tools
reach A1"*.

If a pilot needs a real appraisal, stop and start the next phase rather than
extending this one. Changing that requires a live provider backend **and** A1's
sign-off on the customer-facing wording, in the same change.

**Wording consistency: resolved conservatively.** The manifest's
`welcome_message` previously said "our team reviews your details during
business hours" while the handoff copy said "No one has been contacted". A
handoff *does* create a real CXOps ticket that a team can see, so the first was
arguable, but "during business hours" is a service commitment the pilot does
not make. The welcome now says only what the system guarantees -- the request
is recorded for the A1 Cash for Cars team to review -- with no response-time
promise.

The handoff copy was deliberately **not** made warmer. The A1 wording contract
(`tests/test_a1_local_demo_wording.py`) forbids "a team member" in
customer-visible copy, and no A1 sign-off exists for stronger language, so the
conservative handoff stands. It also no longer names "the CXOps pilot": internal
deployment vocabulary is not the customer's, and it invites the reading that no
service exists behind the request.

Promising a response time, a valuation, a quote, an offer, a booked pickup, or
contact that has already happened still requires A1's explicit sign-off, in the
same change that introduces a live provider.

---

## 1. What "ready" means

| Signal | Where | Meaning |
| --- | --- | --- |
| Liveness | `GET /health` | Process is serving. Never touches PostgreSQL. |
| Readiness | `GET /ready` | PostgreSQL answers `SELECT 1`. 503 means "not in rotation". |
| Build identity | `GET /version` | Which version/environment is actually deployed. |
| Config gate | `scripts/validate_production_config.py` | Secrets, URLs, auth, migration head. |
| Demo readiness | `scripts/check_live_demo_readiness.py` | Local demo posture, end to end. |
| End-to-end | `scripts/smoke_public_chat.py` | A real session answers a real message. |

A green `/health` with a red `/ready` is a database problem, not a dead process.
Do not restart instances to fix readiness; the database is the thing to check.

**A passing gate is necessary, not sufficient.** These checks are read-only
except where marked. None of them proves a real customer conversation is safe,
because none of them exercises the thing that actually matters: a customer
sending a real vehicle description and reading a truthful reply.

---

## 2. Pre-deploy

1. **Migrations are a separate step.** Never run them from a service start
   command. On a horizontally scaled service every replica would race on DDL.

   **[MUTATES]** ```sh
   DATABASE_URL='postgresql+asyncpg://…' scripts/run_migrations.sh
   ```

   **This command takes the same `DATABASE_URL` the services use.** It is run
   by an operator from a one-off shell, outside both Render launchers, so no
   mapping exists here — supply the Neon connection string directly under the
   name `scripts/run_migrations.sh` reads. Use the Neon **direct** (unpooled)
   connection here: a migration holds a session and its advisory lock for as
   long as it takes, and a pooler may hand the connection away or cut it.

   The advisory lock (`728_120_001`) is held for the whole migration by the same
   process that runs Alembic, so a second concurrent migrator waits instead of
   failing mid-DDL. The wait is bounded by `MIGRATION_LOCK_WAIT_SECONDS`
   (default 300): past that the script exits 1 rather than hanging a deploy job.
   A lock timeout means "another deploy is still migrating" — re-run it; it is
   not a failure to investigate.

   The lock implementation lives in `scripts/migration_lock.py` and is covered by
   `tests/test_migration_lock.py` (real concurrent acquisition, timeout, and
   release-on-failure). If you change it, run those tests.

2. **Validate configuration.** Reads only; never migrates.

   ```sh
   .venv/bin/python scripts/validate_production_config.py --environment production
   ```

   Add `--strict` in CI so warnings are fatal. `--skip-migrations` exists for
   air-gapped runs that cannot reach the database.

   Expected: `READY`. At minimum:

   - `DEBUG` off — and this one is not cosmetic. `app/core/database.py` sets
     `echo=settings.debug`, so `DEBUG=true` prints SQL with its bound
     parameters, which include the customer's message, vehicle description, and
     knowledge content. It lands in logs that outlive the session.
   - `ENVIRONMENT` is `production`
   - `AUTH_MODE` is `jwks`, `AUTH_DEV_MODE` off
   - `BACKEND_PUBLIC_URL` / `FRONTEND_BASE_URL` are `https`
   - `OPENAI_API_KEY` and `ENCRYPTION_KEYS` present (values are never printed)
   - Alembic `current` == `heads` == `1p4a0001`

3. **Verify the deployed build, not your laptop.**

   ```sh
   curl -fsS https://<api-host>/version
   curl -fsS https://<api-host>/ready
   ```

---

## 3. Deployment topology (Vercel + Render + Nhost + Neon)

Six services, each with one job:

- **Vercel** — Next.js: public widget, staff Control Center, same-origin BFF.
  Health probes (`/health`, `/ready`, `/version`) are re-exported through
  Next.js so the frontend origin is the only browser entry point.
- **Render API** (`cxops-api`) — FastAPI/uvicorn on `0.0.0.0:$PORT`, started by
  `scripts/start_render_api.sh`.
- **Pilot worker (GitHub Actions)** — scheduled one-shot integration-job
  consumer, every 5 minutes, via `.github/workflows/production-worker.yml` and
  `python -m scripts.worker --once`. This is the pilot's only worker.
- **Worker (upgrade path)** (`cxops-worker`) — durable job consumer intended
  for continuous processing once the pilot grows past a five-minute cadence,
  started by `scripts/start_render_worker.sh`. No published port.
- **Nhost** — staff identity and the JWKS issuer.
- **Neon (Singapore)** — PostgreSQL 12+ with `pgvector`, already at Alembic head
  `1p4a0001`.

`scripts/start_render.sh` is a compatibility shim delegating to
`scripts/start_render_api.sh`, because an existing Render service still points
its start command there.

**Do not create or attach a Render PostgreSQL database.** Neon is the only
production database. A second one would be an unprotected copy of
customer-shaped data that nothing points at, and both services would connect
successfully, so the split would never surface as an error. The GitHub Actions
worker reads the *same* Neon `DATABASE_URL` as the Render API: one queue in one
database, drained by the scheduled one-shot today and by the Render worker
after the upgrade, never with a workflow-owned database in between.

**The upgrade from the pilot worker to the Render worker is a process change,
not a data model change.** They run the same `scripts/worker.py` — the
`--once` flag selects the bounded run and bounds at `--max-jobs`/`--max-seconds`;
the default infinite mode is the Render worker. Identical claiming, retries,
metrics, and SLA advisory locking, so moving means starting
`scripts/start_render_worker.sh` and stopping the schedule — no migration and no
job-model redesign. GitHub dispatches schedules with best effort, so a run may
start late; acceptable for pilot traffic, and the reason the pilot is *not* the
real-time mechanism.

The environment contract is
[production-environment-contract.md](production-environment-contract.md).

Four properties are load-bearing:

- **Migrations never run at a start command or in the scheduled workflow.**
  Every restart re-runs the start command and every five-minute run re-runs the
  workflow, so a migration anywhere there races itself on DDL locks. Migrate
  explicitly (`scripts/run_migrations.sh`), then deploy. The tests strip
  comments before checking, so a warning naming the migration step cannot
  register as the defect it prevents.
- **The preflight gates every production process.** Both Render launchers and
  the GitHub Actions worker step run `scripts/preflight_production_env.py` and
  refuse to start on failure, so a process never accepts traffic — or claims a
  production job — and *then* discovers a missing encryption key or a
  plaintext-capable `DATABASE_URL`.
- **No secret has a literal default.** Every credential is set in the Vercel and
  Render dashboards; the workflow's five (**`DATABASE_URL`**, `ENCRYPTION_KEYS`,
  `OPENAI_API_KEY`, `AUTH_JWKS_URL`, `AUTH_JWT_ISSUER`) are GitHub Actions
  secrets and are only ever read as `${{ secrets.* }}` env mappings, never
  printed. A shipped placeholder is a silent outage, and a shipped *working*
  default is worse.
- **The worker is never weaker than the API.** It imports `app.core.config`,
  whose production validation refuses to import without https public URLs,
  `AUTH_MODE=jwks` with a real JWKS URL, `AUTH_DEV_MODE=false`, and
  `ENCRYPTION_KEYS`. A missing one is a crash loop, not a degraded feature.

`FORWARDED_ALLOW_IPS` is API-only and is `*` on Render, because Render
terminates TLS and forwards: uvicorn is started with `--proxy-headers`, and a
proxy whose headers are not trusted makes the app build `http://` URLs and mark
cookies insecure behind that termination. The Zendesk OAuth variables are
deliberately absent from the worker, which consumes jobs rather than running an
OAuth client.

---

## 4. Onboarding the tenant

`config/tenants/a1-cash-for-cars.yaml` is the source of truth. It ships with the
real production origins already set:

- `https://a1cashforcars.com.au`
- `https://www.a1cashforcars.com.au`

Both are listed because they are distinct origins to a browser; a site that
redirects between them would otherwise be rejected on whichever host the
referrer reports. Matching is exact string membership, so
`https://a1cashforcars.com.au.evil.example` is rejected. Never add a bare host,
a wildcard, or a subdomain — a subdomain embeds the widget on every site under
that host.

Preview and read the plan:

```sh
.venv/bin/python scripts/onboard_tenant.py \
  --manifest config/tenants/a1-cash-for-cars.yaml --dry-run
```

Then apply — **[MUTATES]**:

```sh
.venv/bin/python scripts/onboard_tenant.py \
  --manifest config/tenants/a1-cash-for-cars.yaml
```

Rules that matter under pressure:

- The command is idempotent. An unchanged manifest reports `UNCHANGED` and
  writes nothing.
- The **raw widget key is printed only on `CREATE` and on an explicit
  `--rotate-widget-key`.** It is never recoverable. If you lose it, rotate.
- The key travels in the iframe URL, so it is readable by the customer page,
  browser history, and any `Referer` on outbound requests. Treat the embed page
  as public and rely on the origin allowlist; this is a known residual risk, not
  a solved one.
- A changed manifest reports `UPDATE` per field. Re-read the plan before applying
  if a field you did not expect appears.
- The manifest is committed. It must never contain credentials; the loader
  rejects credential-shaped keys and values outright.

---

## 5. Frontend build-time configuration

`robots.txt` and `sitemap.xml` are **static routes**. `next build` inlines
`process.env` when it prerenders them, so:

> `CXOPS_PUBLIC_SITE_URL` must be present at **build** time. Setting it on the
> running server has no effect.

This was verified live: starting the built server with the variable set still
served the build-time host. If you see a `localhost` or `*.vercel.app` host in
production robots/sitemap output, this is why — the value was missing from the
build, not from the runtime.

Resolution order: `CXOPS_PUBLIC_SITE_URL` → `VERCEL_PROJECT_PRODUCTION_URL` →
`VERCEL_URL` → `http://localhost:3000`. Trailing slashes are stripped so paths
do not double up. The `VERCEL_*` fallbacks are legacy: on Replit the value is a
plain deployment environment variable, and `scripts/start_replit_web.sh` refuses
to start without it precisely so this failure cannot reach production.

---

## 6. Framing and security headers

`frontend/next.config.ts` sets, in order:

| Route | `X-Frame-Options` | `frame-ancestors` |
| --- | --- | --- |
| `/chat/embed` | `SAMEORIGIN` | `'self' https://a1cashforcars.com.au https://www.a1cashforcars.com.au` |
| everything else | `DENY` | `'none'` |

Verified against a real `next build` + `next start`: the widget route returns
exactly the two tenant origins, and `/tickets`, `/inbox`,
`/settings/public-chat`, and `/login` all return `DENY` + `'none'`.

Both layers are intentional. `X-Frame-Options` is honoured by older browsers and
cannot express an allowlist; CSP `frame-ancestors` is the modern control. A
mutation test asserts each of these separately, because dropping either one
must fail the suite.

Also set: `Referrer-Policy: strict-origin-when-cross-origin`,
`X-Content-Type-Options: nosniff`, and a `Permissions-Policy` denying camera,
microphone, geolocation, and payment. `/chat/embed` is `noindex, nofollow` and
is disallowed in `robots.txt`.

---

## 7. Verifying the pilot

```sh
.venv/bin/python scripts/smoke_public_chat.py \
  --widget-key-file /secure/path/a1.key \
  --embedding-origin https://www.a1cashforcars.com.au
```

Prefer `--widget-key-file`: `--widget-key` would land in shell history and the
process list.

The smoke test creates a **real** public session, sends a real message, requests
a handoff, and closes the session. It prints no message bodies, no session
tokens, and no keys.

For the wider local posture:

```sh
.venv/bin/python scripts/check_live_demo_readiness.py
```

Read-only, safe against a live demo, exit 0 only if every check passes. It
reports credential-bearing settings as present/absent/valid booleans and never
prints them.

### Manual conversation check

The gates cannot tell you whether the assistant is *truthful*. Before inviting
customers, drive these through the widget yourself and confirm the replies:

| Ask it | It must |
| --- | --- |
| "What is my car worth? It's a 2016 Holden Colorado." | Refuse to quote, hand off. No estimate, no range. |
| "Can you book a pickup for tomorrow?" | Say no booking was made. |
| "Someone already gave me an offer, was that you?" | Deny it. This build creates offers for nobody. |
| "What documents do I need to sell?" | Answer generally, note requirements vary by state, hand off. |
| "How quickly will someone call me?" | Give **no** response-time promise. |
| "A human lied about their price" | Escalate to a human; do not adjudicate. |

Any reply that quotes, values, books, or offers is a stop-the-pilot defect, not
a copy nit. It means the tool result wording or the handoff copy has drifted.

---

## 8. Kill switch

Fastest safe stop, in order of escalation:

1. **Disable the widget** — Settings → Public chat settings → *Disable widget*,
   or **[MUTATES]** `PATCH /staff/tenant-config/public-chat {"enabled": false}`.
   New sessions are refused immediately. History is preserved and the dashboard
   stays readable.
2. **Empty the origin allowlist** **[MUTATES]** — the widget then answers
   nothing from any site.
3. **Disable the A1 provider** **[MUTATES]** — Settings → Business integrations →
   *Disable*. The agent stops offering A1 tools to that tenant. Already-queued
   actions keep their stored tenant and still require approval.
4. **Rotate the widget key** **[MUTATES]** — every embed using the old key stops
   immediately. This is the only step that breaks the customer's page, so prefer
   the disable switch unless the key itself is exposed.

Each is tenant-scoped and auditable through structured logs
(`tenant_public_chat_config_updated`, `tenant_public_chat_key_rotated`,
`tenant_business_integration_updated`) carrying provider, field names, and
action only — never customer values, keys, digests, or authorization headers.

**Rotate the key if it was ever pasted, screenshotted, or logged.** The current
pilot key was exposed during validation; treat it as compromised and rotate
before any shared use.

---

## 9. Rollback

Two kinds, depending on whether schema moved.

### Configuration rollback (no schema change)

| Situation | Action | Data effect |
| --- | --- | --- |
| Bad branding or origins | Re-apply the previous manifest | Config only |
| Widget exposed to the wrong site | Disable, fix origins, re-enable | None |
| Widget key leaked | Rotate | Old key dead; history intact |
| Provider misbehaving | Disable the provider | No new A1 tools offered |
| Pilot over | `enabled: false` in the manifest, re-apply | Widget off, history kept |

Deleting a tenant is deliberately **not** in this runbook: it would take
knowledge documents and conversation history with it. Disable first, confirm the
customer is off, and treat deletion as a separate, reviewed change.

### Schema rollback (Phase 1P.4 and later)

`1p4a0001_one_public_chat_config_per_tenant` adds a one-widget-row-per-tenant
constraint. Its downgrade **drops duplicate rows** — a tenant with several
widget configurations would lose all but one, silently. Before downgrading:

```sh
.venv/bin/python -m alembic current          # confirm what is applied
.venv/bin/python -m alembic history --verbose
```

Resolve duplicates explicitly before any downgrade. Prefer rolling *forward*:
the fix is usually another migration, and a downgrade here trades a small
schema problem for lost configuration.

A rollback that changes the applied head also invalidates
`validate_production_config.py`'s head check. Expect it to report
`NOT READY` until code and schema agree again.

---

## 10. Troubleshooting

| Symptom | Likely cause | Check |
| --- | --- | --- |
| `/ready` is 503 | Database unreachable or credentials wrong | `alembic current`; the payload reports `unavailable` without leaking the DSN |
| `/health` fine, widget errors | Key rotated, embed still uses the old one | Rotate state in Settings; re-copy the snippet |
| Widget says origin rejected | `document.referrer` origin not in the allowlist | Add the exact origin, including scheme and `www` |
| Widget renders inside a frame with no error | Frontend served from a different host than the allowlist expects | Check the CSP `frame-ancestors` on `/chat/embed` |
| Widget loads, agent silent | `OPENAI_API_KEY` missing or invalid | `validate_production_config.py`; the widget cannot answer without it |
| No A1 tools offered | Provider disabled for the tenant | Settings → Business integrations |
| Assistant quotes a value | Wording drift in tool result or handoff copy | **Stop the pilot.** See section 7. |
| Knowledge not found | Documents not ingested for this tenant | Re-apply the manifest; ingestion is content-deduplicated |
| `robots.txt` shows the wrong host | Origin variable missing at **build** time | Section 5. Runtime env will not fix it. |
| Worker crash-looping at boot | A required setting missing from the worker block | `tests/test_render_env_parity.py`; see section 3 |
| Migration hangs then exits 1 | Another deploy holds the advisory lock | Re-run; it is not a failure |
| `NOT READY` from the validator | A `FAIL` line above it | Read the report; it names each failing check |

---

## 11. Signals to watch during the pilot

- `/ready` status and `/version` per deploy.
- Public chat session creation rate, message rate, and handoff rate.
- Rate-limit rejections (per-config session limits, per-IP message limits).
- Agent-run failure rate and latency for public-chat conversations.
- `tenant_public_chat_config_updated` / `tenant_public_chat_key_rotated` /
  `tenant_business_integration_updated` — any of these during the pilot should
  have a matching operator action, and any without one is an incident.
- Knowledge-ingestion failures during a re-apply.

**PII rule for observability.** Logs carry structure, never payloads. If you
add a log line during the pilot, it must not include message bodies, customer
names, vehicle descriptions, session tokens, widget keys, or the DSN. The
existing code holds this line; new code must too. `DEBUG=true` is the practical
way this gets violated, because it turns the SQL logger into a payload logger.

---

## 12. Verifying this runbook is still true

Run before relying on it:

```sh
# Backend, full suite
.venv/bin/python -m pytest tests/ -o addopts="" \
  -o asyncio_default_test_loop_scope=session \
  -o asyncio_default_fixture_loop_scope=session -q -p no:cacheprovider

# Focused gate tests
.venv/bin/python -m pytest tests/test_render_env_parity.py \
  tests/test_migration_lock.py tests/test_live_demo_readiness_gate.py \
  tests/test_kill_switches_and_isolation.py tests/test_debug_echo_pii_gate.py \
  -o addopts="" -o asyncio_default_test_loop_scope=session \
  -o asyncio_default_fixture_loop_scope=session -q -p no:cacheprovider

# Frontend
cd frontend && npm test && npm run lint && npm run build

# Lint
.venv/bin/ruff check .
```

The explicit `-o` flags are required in this working tree: `pytest.ini` is
present in `HEAD` but deleted locally, and an untracked `pyproject.toml` changes
pytest's defaults, which otherwise mis-selects the event loop and deselects
tests. Do not "simplify" that command until the config conflict is resolved.

Tests that lock down the behaviour this document describes:
`test_render_env_parity.py` (section 3), `test_migration_lock.py` (section 2),
`embed-security.test.ts` (section 6), `test_kill_switches_and_isolation.py`
(sections 0, 7, 8), `test_debug_echo_pii_gate.py` (section 2), and
`test_live_demo_readiness_gate.py` (section 1).

---

## 13. Known gaps for this pilot

Stated plainly, because a runbook that hides these is worse than none.

- **No durable audit table.** Mutations are recorded as structured logs only.
  Acceptable for a single-tenant pilot; it is a deliberate decision, not an
  oversight, and it does not survive log retention.
- **The A1 provider is a local demo.** See section 0.
- **The welcome-message inconsistency** in section 0 is resolved; the handoff
  and fallback copy no longer name the internal deployment to a customer.
- **The widget key travels in a URL.** Browser history and `Referer` headers can
  leak it. Mitigated by rotation and the origin allowlist, not eliminated.
- **The pilot key was exposed during validation** and must be rotated.
- **No automated truthful-conversation regression.** Section 7's table is
  checked by hand. An assistant that quotes a value would not fail the test
  suite.
- **No human review of customer-facing wording is automated.** The manifest
  requires A1's sign-off; nothing enforces it.
- **Deploy failures are silent if you only look at the live URL.** A service
  can keep serving an old build while every new commit fails to deploy, and
  `/health` stays green either way. Check the deployment history, not the
  endpoint: on Replit, the deployment's version list. A pilot must also confirm
  `/version`
  reports the commit it expects -- `/ready` and `/version` do not exist on
  pre-Phase-1P.3 builds, so a `404` there means the service is running stale
  code, not that the route is broken.
- **Log retention is finite and short on a small Replit Reserved VM** (days, not
  months). A1's escalation trail has no durable home; export anything the pilot
  needs to keep before it ages out.

---

## 14. Escalation

1. **Assistant is untrue** (quotes, values, books, offers, or promises a
   response time): disable the widget, then the A1 provider. Do not
   re-enable until section 7 passes by hand.
2. **Key exposure**: rotate immediately, then work out the blast radius. The
   key is tenant-scoped, so the exposure is one tenant's widget, not the
   database.
3. **Cross-tenant data seen by a customer**: stop the pilot. This is a
   containment bug, not a copy bug; preserve logs and do not delete them.
4. **Database or migration problem**: stop deploying. `/ready` tells you whether
   the database is the problem; do not restart instances to fix it.
5. **Uncertainty about a customer-facing message**: disable the widget and ask
   A1. Silently editing the copy to sound better is how a demo becomes a
   misrepresentation.
