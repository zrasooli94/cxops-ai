# Phase 1Q release runbook (authoritative)

This replaces the merge-first order previously written in
`phase1q-commercial-launch.md` §2. **Render and Vercel deploy `master`
automatically**, so the database must already carry the new schema when the
code lands. Every step marked 🔒 is an owner action; nothing here has been run
against production.

| Item | Value |
|---|---|
| Production code today | `32dc64f` (Alembic `1p9b0001`) |
| Release | PR #19 head — record the exact reviewed SHA in step 1 |
| Migration | `1p9b0001 → 1q0a0001` (additive only; no row rewritten beyond column defaults) |
| Feature gates at release | all OFF (see §4) |

## 1. Why migrate first is safe (evidence)

On a disposable PostgreSQL migrated to `1q0a0001`, the **unchanged production
code** `32dc64f` was tested:

* its full backend suite: 2219 passed, 1 failed — the failure is
  `test_schema_gate_passes_at_head`, which asserts the database sits at the
  *old* head; the observed verdict is `ahead`, which master's own gate test
  documents as non-refusing;
* the API started and `/ready` returned 200; the schema gate logged
  `schema_gate_database_ahead_of_build … verdict=ahead` (warning, not a
  refusal);
* master's Render preflight (`preflight_production_env.py`) does not check the
  Alembic head, so a Render restart of the old code still boots; the scheduled
  GitHub worker uses the same schema gate (`ahead` = warning).

Old code writing to the new schema: `organizations.lifecycle_status` defaults to
`active` and the other new columns are nullable, so organizations created by the
old code are normal active tenants.

## 2. Release sequence

| # | Step | Owner | Verify / stop condition |
|---|---|---|---|
| 1 | Confirm PR #19 CI is green on the reviewed SHA; record `RELEASE_SHA`. No new commits after review. | 🔒 | `gh pr checks 19` all pass; head = `RELEASE_SHA` |
| 2 | Neon console: create a **new** branch from production, e.g. `pre-1q-<YYYYMMDD>`, **without a compute endpoint** (recovery point only; costs storage, not compute). | 🔒 | branch listed, no compute |
| 3 | Read-only preflight from a clean checkout of `RELEASE_SHA` with the production **direct** (non-pooler) URL: `python scripts/validate_production_config.py` and `alembic current`. | 🔒 | everything PASS except the expected `migration gap 1p9b0001 → 1q0a0001`; `alembic current` = `1p9b0001` |
| 4 | Read-only RISPU baseline: run the queries in §3 and save the output. | 🔒 | output saved |
| 5 | Apply the migration **from the `RELEASE_SHA` checkout**: `DATABASE_URL=<direct> scripts/run_migrations.sh` (single run, advisory migration lock). | 🔒 | `1p9b0001 -> 1q0a0001`; `alembic current` = `1q0a0001 (head)` |
| 6 | Verify schema and RISPU: §3 queries again; RISPU org `lifecycle_status = active`, widget/allowance/async flags unchanged; production `/ready` 200 (old code still serving; logs show `verdict=ahead`). | 🔒 | identical to step 4 except the new columns |
| 7 | Merge PR #19 **once** (merge commit; no force-push). | 🔒 | master = merge of `RELEASE_SHA` |
| 8 | Watch the automatic Render (API, worker) and Vercel deployments. | 🔒 | both green; Render log `schema_gate_ok … verdict=at_head`; preflight passed |
| 9 | Verify the release with gates off (§5). | 🔒 | all checks pass |
| 10 | **Stop.** No operator grant, no identity activation, no lead capture, no customer onboarding in this release window. | — | — |

Any failure before step 7: do not merge; the database stays at `1q0a0001`,
which the running production code tolerates (§1). Roll the schema back only if
needed (§6).

## 3. RISPU / existing-tenant preservation checks (read-only SQL)

```sql
-- Pilot tenants and their lifecycle (after migration: lifecycle_status = 'active')
SELECT id, name, external_id, created_at FROM organizations
WHERE external_id IN ('rispu', 'a1-cash-for-cars') ORDER BY id;

-- Widget state that must not change
SELECT organization_id, enabled, allowed_origins, theme_token, async_turns_enabled,
       grounded_auto_reply_enabled, monthly_ai_turn_limit, monthly_ai_budget_usd
FROM public_chat_configurations
WHERE organization_id IN (SELECT id FROM organizations WHERE external_id IN ('rispu','a1-cash-for-cars'));

-- Memberships (count and roles) must not change
SELECT organization_id, role, count(*) FROM organization_memberships
WHERE organization_id IN (SELECT id FROM organizations WHERE external_id IN ('rispu','a1-cash-for-cars'))
GROUP BY 1, 2 ORDER BY 1, 2;

-- After migration only
SELECT lifecycle_status, count(*) FROM organizations GROUP BY 1;   -- expect only 'active'
SELECT count(*) FROM platform_operators;                            -- expect 0
```

## 4. Gates that must be OFF at release (Render env)

| Variable | Release value |
|---|---|
| `PUBLIC_LEAD_CAPTURE_ENABLED` | `false` (unset) |
| `INVITATION_IDENTITY_MODE` | `disabled` (unset) |
| `ONBOARDING_KNOWLEDGE_SETUP_ENABLED` | `false` (unset) |
| `NHOST_AUTH_URL`, `PUBLIC_CONTACT_EMAIL` | unset |
| Vercel `CXOPS_WIDGET_FRAME_ANCESTORS` | unchanged (unset) |

No new secret is required by this release.

## 5. Post-deploy verification (gates off)

* API `/health` 200, `/ready` 200, `/version`; worker one-shot run green.
* RISPU widget on `https://www.rispu.com`: opens, sends a message, reply or
  handoff as before; staff inbox shows it. `/me/authorization` for a RISPU staff
  user: `organization_status: "active"`, capability list unchanged.
* `/early-access` shows "requests open soon"; `GET /public/leads/availability`
  → `accepting: false`; `POST /public/leads` → 503.
* `/platform-admin/me` with any staff token → 403 (no operators exist).
* `POST /invitations/accept` → 503 (identity disabled).
* Marketing pages render; `/platform` redirects to `/features`.

## 6. Rollback (preserves customer data)

| Situation | Action | Data effect |
|---|---|---|
| Problem found **before merge** (steps 5–6) | Leave the database at `1q0a0001` (old code tolerates it) and fix forward, or downgrade (below). | none |
| Problem **after merge** | Revert the merge commit on master (one revert commit) → Render/Vercel redeploy the previous code. **Keep the database at `1q0a0001`.** | none: old code ignores new columns/tables; conversations, tickets, knowledge untouched |
| Schema must be removed | Only after a code rollback, and only if no Phase 1Q data needs keeping: `alembic downgrade 1p9b0001`. | deletes leads, invitations, operator grants and lifecycle columns; customer chat data untouched |
| Catastrophic data problem | Restore from the `pre-1q-<date>` Neon branch | discards everything written after step 2 — last resort, owner decision |

Caveat for a code rollback **after onboarding has started** (not in this
release window): the old code ignores `lifecycle_status`, so organizations in
`onboarding`/`pending_review`/`suspended` would regain full capabilities.
Before reverting in that situation, disable those tenants' widgets and export
then remove their memberships (operator decision).

## 7. After the release window (separate approvals)

1. 🔒 Audit legacy `external_id` values (query in `phase1q-commercial-launch.md` §2a).
2. 🔒 Set the OpenAI project hard limit ≤ `PLATFORM_MONTHLY_AI_BUDGET_USD`.
3. 🔒 Grant the first platform operator (`scripts/manage_platform_operators.py … --apply`).
4. 🔒 Activate identity verification (§8).
5. 🔒 Legal review and publication of privacy policy / terms; `PUBLIC_CONTACT_EMAIL`.
6. 🔒 First customer (operator-assisted procedure in `phase1q-commercial-launch.md` §5).
7. 🔒 Public lead capture, pricing, agreements, payments — each separately.

## 8. Activating Nhost identity verification (owner-controlled)

Nothing below has been done; no Nhost setting or account was changed.

1. In the Nhost dashboard, confirm the project's Auth service URL
   (`https://<subdomain>.auth.<region>.nhost.run/v1`) and that email
   verification is enforced for the sign-in methods you allow (password or
   magic link). Do **not** enable anonymous sign-in for staff.
2. For each invitee, ensure an Nhost user exists for the *invited* address and
   that its email is verified (operator-created user + the verification or
   password-reset email, or verified self sign-up if you later enable it).
3. On Render (API): set `NHOST_AUTH_URL=<that URL>` and
   `INVITATION_IDENTITY_MODE=nhost_userinfo`; restart.
4. Verify with a test invitation to an address you control: an unverified
   account is refused ("Verify your email…"), another account is refused
   ("issued to a different email address"), the verified invitee succeeds.
5. Rollback: unset `INVITATION_IDENTITY_MODE` (returns to `disabled`, 503).

Guarantees once active: the API sends the caller's own access token only to
`NHOST_AUTH_URL` (https, no redirects followed, 5 s timeout), accepts the
answer only if `id` equals the token's `sub`, `emailVerified` is true and the
account is neither anonymous nor disabled, and redeems the invitation only for
the matching address. Provider errors and timeouts fail closed (503); logs
contain error types and ids, never tokens or emails.
