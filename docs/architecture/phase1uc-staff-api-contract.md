# Phase 1U-C — Synthetic tenant & staff read-only contract pilot

**Status:** Draft, no merge/deploy until separate review and complete CI.
Depends on Phase 1U-B's disposable-only Schemathesis job.

## Scope & authority

- Add three GET-only staff endpoints from existing OpenAPI:
  `GET /staff/public-chat/summary`, `GET /staff/public-chat/handoff`,
  `GET /staff/public-chat/sessions/{session_id}/business-actions`.
- Run only through the in-process ASGI client in the **same dedicated, local**
  disposable GitHub Actions PostgreSQL environment as 1U-B; public fuzz tests
  run first against a freshly migrated, unseeded database.
- Create two synthetic active organizations, a viewer membership for each
  under **different subjects**, and one synthetic handoff per organization.
  Every token is locally signed after the disposable DB/secret guard,
  with test-only HS256 credentials. No Nhost, Neon or production JWT.
- Query only. Assert the viewer sees its own handoff and summary, but not
  the foreign organization's session or business actions; a forged tenant
  selector is rejected. Viewer POST requests for assign/release/resolve must
  return 403 without changing any persisted status.
- Fuzz the three selected GET operations with deterministic, bounded
  Schemathesis examples. Unexpected 5xx, stack traces, returned test JWTs
  and oversized responses fail the job. When documented, validate response
  bodies against the existing OpenAPI schema.
- Include all three staff GET operations in an explicit inventory test.
  New staff GET endpoints are not silently fuzzed until reviewed.
- Provider credentials are absent. The contract guard now refuses a
  preconfigured `AUTH_JWT_SECRET`; test credentials are generated in the
  test process only **after** it has passed the guard.
- No production Python dependencies, migrations, new services or paid model
  evaluations. Staged as a separate CI test file and workflow step only.

## Exclusions and follow-up

- These are read-only staff API contract tests and negative write-permission
  checks, **not** stateful assignment or message-delivery fuzzing.
- No real IdP/JWKS, employee identities, external provider calls, webhooks,
  tenant configuration edits or customer content.
- Dedicated positive-flow business action tests and role-transition races
  belong in another milestone.
- Continue to track the public lead website/domain schema mismatch in
  Issue #27. Do not weaken validator semantics to silence test failures.

## Acceptance gates

1. Original nine anonymous operations and three staff GET operations pass
   deterministic bounded fuzz testing in one disposable CI environment.
2. Own-tenant viewer reads succeed; foreign-tenant reads and viewer mutations
   are denied; session rows are unchanged after denied requests.
3. Fail-closed guard tests prohibit remote DSN, production settings, model
   credentials and externally supplied JWT signing material.
4. Complete CI green (including backend regression, Docker, frontend, mypy,
   Ruff, and secret scan); independent review before any merge.
