# CXOps conditional billing policy readiness

Date: 2026-10-27
Scope: source-only, development-only conditional uncertainty path
Live execution: **NO-GO**

## Implemented boundary

- Independent billing evidence remains a separate, empty registry.
- Conditional policy version: `cxops-count-uncertainty-v1`.
- Counting status remains `unknown`; it is never labeled independently verified.
- 100 count attempts reserve US$0.00030 each (US$0.03000 shared local risk
  allowance), 100 generation attempts reserve US$0.06050, and the Malaysia tax
  planning allowance is US$0.00484 once. Combined local reservation is
  US$0.09534, leaving US$0.00466 positive headroom.
- The local uncertainty allocation is not verified count pricing, a
  provider-side cap, or an all-in invoice guarantee.
- The exact tenant/run/ten-session manifest, fixture/spec/runner source,
  evidence digests, budget, policy version, authenticated accepting subject and
  expiry are immutable in the release decision.
- Only a tenant OWNER with `evaluation.manage` may submit the exact separate
  acceptance object. The server supplies the subject. The runner cannot accept,
  approve, or authorize execution for itself.
- Latest-decision ordering, expiry, source drift, manifest drift, tenant/session
  checks, durable count/generation reservations, zero SDK retries, citation
  validation, handoff suppression and ambiguous-outcome stops remain in force.

## Preserved evidence

- Paul response, exact owner-supplied bytes:
  `docs/evidence/openai-support-paul-2026-10-27.txt`
- SHA-256:
  `2096759e3e6e2dfa5842f393810536086466abd43d6dc204f070479fe346c63d`
- Original case screenshot digest:
  `ec6aeba1c797adeab058174b17ef76dcb4f01281e0d98ec0f6958561abf624bf`

Paul is attributed in the supplied text as an OpenAI Support Specialist. The
record is owner supplied; original email headers, DKIM, sending address and
account-specific applicability were not authenticated. It corrects the earlier
universal free-count statement and does not establish a count charge ceiling.

## Offline evidence

On a migrated, isolated loopback PostgreSQL 17 database named
`cxops_conditional_disposable_test`:

- 211 relevant tests passed.
- Coverage includes the verified-registry path, exact conditional acceptance,
  owner-only authorization, changed/expired evidence, insufficient headroom,
  durable conditional count reservation, conflicting/ambiguous reservations,
  latest rejection, interruption/cleanup, tenant/session isolation and the
  complete 50-turn mocked public-chat workflow.
- Two pre-existing warnings remained: one Pydantic v1 validator deprecation and
  one SQLAlchemy connection cleanup warning in the existing public-chat suite.
- Ruff and `git diff --check` passed on the intended files.

These tests prove local control mechanics only. They do not measure genuine
model quality, verify provider billing, guarantee the final invoice, or
authorize a credential/provider request.

## Required future actions

1. The owner reviews the exact printed manifest and fixed acceptance statement.
2. Using an independently authenticated owner session with
   `evaluation.manage`, the owner submits the unchanged `provider_activation`
   and exact `conditional_billing_acceptance` object to the existing release
   decision endpoint.
3. A separate, later owner authorization is still required for the one live
   development execution, process-scoped flags, and masked key entry.

No acceptance, release approval, execution authorization, credential, provider
request, or expense was created during this implementation.
