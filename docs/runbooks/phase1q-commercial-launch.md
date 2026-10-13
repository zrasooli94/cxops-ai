# Phase 1Q — Commercial launch runbook (operators)

Architecture and security rationale:
[phase1q-commercial-saas.md](../architecture/phase1q-commercial-saas.md).
Business-owner instructions: [phase1q-business-owner-guide.md](../guides/phase1q-business-owner-guide.md).

**Nothing in this runbook has been executed against production.** Every step
marked 🔒 needs explicit owner approval at the time it is run.

---

## 1. Feature flags

| Flag (Render API env) | Default | Effect when on |
|---|---|---|
| `PUBLIC_LEAD_CAPTURE_ENABLED` | `false` | `POST /public/leads` stores requests; the website form opens. Off → 503, form shows "opens soon". |
| `ONBOARDING_KNOWLEDGE_SETUP_ENABLED` | `false` | Tenants still in setup may ingest knowledge (paid embeddings) before approval. |
| `LEAD_SUBMISSIONS_PER_HOUR_PLATFORM` | 30 | Platform-wide hourly ceiling for lead submissions. |
| `LEAD_SUBMISSIONS_PER_DAY_PER_EMAIL` | 3 | Per-address daily ceiling. |
| `TEAM_INVITATION_TTL_HOURS` | 72 | Invitation lifetime. |

| Frontend (Vercel, **build time**) | Default | Effect |
|---|---|---|
| `CXOPS_WIDGET_FRAME_ANCESTORS` | empty | Extra exact `https://` origins allowed to frame the widget. Requires a redeploy. |

Not flags, always on after deploy (inert without data): operator console
(needs a grant), setup wizard, team management, owner overview, lifecycle gate.

## 2. Deployment prerequisites (in order)

1. 🔒 Review and merge the PR (CI green).
2. 🔒 **Migration** `1p9b0001 → 1q0a0001` on Neon, using the existing migration
   procedure (`scripts/run_migrations.sh`, migration lock, direct endpoint).
   Additive; existing tenants become `active`. Verify `alembic current` =
   `1q0a0001`.
3. 🔒 Deploy API (Render) then frontend (Vercel). The API's schema gate and
   `validate_production_config.py` expect head `1q0a0001`; deploying code
   before the migration fails preflight (by design).
4. Smoke: `/health`, `/ready`, `/version`; RISPU widget still answers (its org
   is `active`); `/me/authorization` for an RISPU staff user shows
   `organization_status: active` and unchanged capabilities.
5. 🔒 Grant the first platform operator (§3).
6. Leave `PUBLIC_LEAD_CAPTURE_ENABLED=false` until §7 is approved.

## 3. Platform operators

```bash
python -m scripts.manage_platform_operators list
```

```bash
python -m scripts.manage_platform_operators grant --subject <nhost-user-uuid> --granted-by "<name>" --note "<why>"
```

Add `--apply` to write (🔒 in production). Revoke with
`revoke --subject … --revoked-by … --apply`; effective on the next request.
The subject is the Nhost user id (UUID), **never** an email. Operators use
`/operator` on the frontend.

## 4. Lead management

Lifecycle: `new → reviewing → contacted → approved | declined`
(`declined → reviewing` re-opens; `approved` is terminal).

1. `/operator` → Leads → filter **new**. Read the request; add notes.
2. Move to *reviewing* / *contacted* as you work it (email the contact from
   your own mailbox — there is no email integration).
3. *Approve* only when you intend to onboard the business; otherwise *decline*.
4. On an approved lead: **Create workspace and owner invitation**. This creates
   an `onboarding` organization (no AI, no widget serving, no integrations) and
   shows a one-time owner link. Send it to the contact yourself.
5. The invitee must already have an Nhost account for that email (see §9,
   decision D3). Accepting binds their Nhost user id as owner.

Duplicate submissions from one address fold into the open lead
(`submission_count`), never overwriting the first submission.

## 5. Tenant onboarding and approval

1. Owner completes **Setup** (business, domains, chatbot, optional knowledge
   prep, install snippet) and clicks **Submit for approval** →
   `pending_review`.
2. Operator → Tenants (pending first). Check: business is real, website
   matches, allowed origins are the business's own domains, display text is
   appropriate.
3. Either **Request changes** (back to `onboarding`) or **Approve**
   (`active`).
4. 🔒 Before the customer's site can frame the widget in production, add its
   exact origins to `CXOPS_WIDGET_FRAME_ANCESTORS` on Vercel and redeploy.
5. Set entitlements (plan code, monthly turn limit, $ budget) in **Entitlements**.
   Keep the sum of tenant budgets ≤ `PLATFORM_MONTHLY_AI_BUDGET_USD` ≤ the
   OpenAI project hard limit.
6. Owner switches the chatbot on in **Chatbot settings**.

**Suspend** stops the widget for open and new sessions immediately and
removes write capabilities; **Reinstate** restores `active`.

## 6. Rollback and recovery

| Situation | Action |
|---|---|
| UI or API defect, data fine | Redeploy `32dc64f` (API + frontend). The previous release ignores the new columns/tables. Leave the database at `1q0a0001`. |
| A tenant misbehaves | Operator → Suspend (immediate), or owner/staff kill switch (disable widget / empty origins). |
| Leads spam | Set `PUBLIC_LEAD_CAPTURE_ENABLED=false` (Render env change → restart). Optionally lower the hourly ceiling. |
| Must remove 1Q schema | 🔒 `alembic downgrade 1p9b0001` **deletes all leads, invitations and operator grants** and the lifecycle columns. Only after a code rollback and with a backup. |
| Wrongly approved tenant | Suspend, then Request changes after reinstating, or keep suspended. |
| Lost widget key | Owner/admin rotates it in Chatbot settings (after approval). |

## 7. Decisions and approvals required before / at production

| # | Decision | Status |
|---|---|---|
| D1 | Merge + deploy Phase 1Q (migration `1q0a0001`, API, frontend) | 🔒 pending |
| D2 | Enable `PUBLIC_LEAD_CAPTURE_ENABLED` (public lead form) | 🔒 pending — also decide lead-data retention (suggest 12 months for declined) and add a privacy notice link |
| D3 | How invitees get an Nhost account: operator creates users in Nhost, or enable Nhost email sign-up (needs email verification + an in-app sign-up page) | 🔒 pending (affects Nhost production config) |
| D4 | Invitation/lead email delivery provider (e.g. transactional email) | Not built; paid service decision |
| D5 | Public pricing / plans / trial terms | Not set; site says "pricing on request" |
| D6 | Payment provider (e.g. Stripe), tax, invoicing | Not built; no charges possible |
| D7 | Allow knowledge setup before approval (`ONBOARDING_KNOWLEDGE_SETUP_ENABLED`) | Default off |
| D8 | First platform operator grant(s) | 🔒 pending |
| D9 | CAPTCHA / edge rate limiting for the lead form | Optional; third-party/paid |
| D10 | Security certification / legal pages (terms, privacy policy) | Not claimed; legal pages needed before taking payment |

## 8. $50/month infrastructure envelope

Phase 1Q adds **no new services and no paid integrations**. Marginal cost:

* Neon: four small tables; lead/invite/operator queries are indexed point
  lookups. Public lead submissions add at most 2 rate-limit upserts + 1 insert
  each (≤ 30/h by ceiling). No new background polling (no Neon compute wake-ups).
* Vercel: marketing pages are static (served from cache); one extra function
  route (`/api/public/leads`).
* Render: no change in process count.
* OpenAI: unchanged per-turn cost; **reduced exposure** — unapproved tenants
  cannot spend at all; budgets are operator-set.

## 9. First-paying-customer readiness checklist

- [ ] D1 deployed; RISPU smoke passes.
- [ ] D8 operator granted; `/operator` reachable.
- [ ] D3 decided; a test invitee can sign in.
- [ ] D5 price agreed with the customer offline (contract outside the product).
- [ ] D10 terms + privacy policy published.
- [ ] Customer lead approved → provisioned → owner accepted.
- [ ] Setup submitted; origins verified as the customer's domains.
- [ ] Origins added to `CXOPS_WIDGET_FRAME_ANCESTORS`; frontend redeployed.
- [ ] Tenant approved; entitlements set within the platform ceiling.
- [ ] Knowledge added and spot-checked with real questions.
- [ ] Chatbot switched on; a test conversation from the customer's site appears
      in their overview and handoff queue.
- [ ] Team members invited; roles reviewed.
- [ ] Billing handled manually (invoice outside the product) until D6.

**Commercial readiness today: NOT READY** for self-serve paying customers
(D1–D3, D5, D10 open). **Ready for an operator-assisted first customer** once
D1, D3 and D8 are done.
