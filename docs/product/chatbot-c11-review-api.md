# CXOps AI C11 — Authenticated synthetic claim-review API

**21 October 2026 · SOURCE ONLY / DRAFT / RELEASE HOLD**

C10 added a private append-only review schema and synthetic-only service,
tested against a disposable PostgreSQL database but not reachable from HTTP.
C11 creates a real FastAPI route family guarded by CXOps's **existing JWT,
current tenant membership and capability system**. It does not replace
identity, modify the customer chat widget or activate production.

## Security and rollout switch

`CLAIM_REVIEW_SYNTHETIC_API_ENABLED` defaults to **false** in
`Settings`. Every endpoint has a router-level gate, which returns
`404 Not found` when the flag is off or when
`ENVIRONMENT != development` (including staging and production).
The switch is enabled only inside the dedicated GitHub Actions disposable
integration test job. Do **not** enable it in a real environment without
separate authorization, DB security review and approved data lifecycle.

An enabled preview additionally requires:
- A valid authenticated staff JWT validated by the existing
  `CurrentPrincipal` chain, **not** a username in request JSON.
- `CurrentTenant` derived from the authenticated subject's DB
  organization membership. The optional `X-CXOps-Organization-ID` is
  just a requested selector and never authorizes another tenant.
- **Both** `evaluation.read` and `evaluation.manage` on the trusted
  selected organization for all private reviewer routes.
- **`member.manage`** for creating a new synthetic batch or assigning
  reviewers: this limits administrative setup to owner/admin rather
  than any reviewer with evaluation-management privileges.
- The existing C10 service's separate current database membership,
  role and lifecycle recheck before every read/write.

A member of Org A with authority there gets **no** review access to
another tenant's batch by guessing an ID. Missing/foreign batches or
claims are returned as **generic 404s**, without existence hints or
reviewer identifiers. Invalid input returns safe 400/422; duplicates or
immutable state conflicts return generic 409; permission denial 403.
Database `IntegrityError` is rolled back and sanitized: no SQL,
source paragraphs or private reviewer text in HTTP errors.

## Exact endpoints (synthetic fixture only)

All live under `/evaluations/claim-reviews`; in production every
route is **disabled**.

| Method/path | Authorization | Behavior |
| --- | --- | --- |
| `POST /synthetic-batches` | evaluation.read + evaluation.manage + member.manage | Construct a batch for the committed **fictional C6** dataset; no request body, actor or organization selector accepted |
| `POST /batches/{batch_id}/assignments` | Same as above | Assign exactly two distinct, current tenant manager-capable reviewer subjects to immutable slots |
| `GET /batches/{batch_id}/my-queue` | evaluation.read + evaluation.manage | Return only the caller's claim keys, fingerprints and **their own** completed flags. Non-assignees get an empty queue; no other subjects, votes or notes |
| `POST /batches/{batch_id}/reviews/{review_key}` | evaluation.read + evaluation.manage + C10 assignment | Store one immutable reviewed label as the **authenticated subject**; exact cited excerpt or bounded rationale validated against the fictional fixture |
| `POST /batches/{batch_id}/adjudications/{review_key}` | evaluation.read + evaluation.manage + C10 distinct third-person check | Resolve two *different* submitted labels; never allows reviewer self-adjudication |

The only accepted label values are
`supported / contradicted / unsupported / unclear`.
Pydantic models reject additional fields, unknown labels, overlong
evidence/rationales, foreign organization IDs or caller-supplied actors.
Responses are fixed schemas containing IDs, source fingerprint metadata,
a label, timestamps or a count—not raw cited passages, review
rationales, other reviewers' votes or access tokens.

The C10 **four-table append-only PostgreSQL schema** is the only
persistence. Versioned C6 source text is loaded from bundled fictional
fixture and remains out of the database; only SHA-256 digests are stored.
An assigned reviewer sees `completed` flags for *their own* judgments
but cannot infer which labels another reviewer assigned.

The existing `/evaluations/runs/{id}/release-decision` workflow is
separate. C11 APIs NEVER approve a model, change any auto-answer
configuration, trigger a worker, invoke LLMs or process payments.

## Disposable test boundary

`.github/workflows/chatbot-c11-review-api-offline.yml` starts an exact
loopback pgvector/PostgreSQL 16 service, verifies the existing C2
development-only environment/DB guard, applies the existing C10 migration
only there and runs the actual FastAPI ASGI app with **locally signed
fictional HS256 JWTs**. No actual Nhost/JWKS credentials are used.

Acceptance coverage:
- Feature flag defaults off (and explicitly rejects non-development).
- Missing/invalid JWT, absent selected-tenant membership and read-only
  role deny access before review writes.
- Owner/admin create+assign; supervisors cannot administer assignments
  but can submit if explicitly assigned.
- No body, actor/tenant fields, private customer text or arbitrary
  datasets accepted as API inputs.
- Two reviewer slots are unique and tenant membership-bound. Only the
  assigned user can submit, once per claim; evidence exactness and
  character limits enforced.
- Real HTTP `my-queue` contains only caller's own completion flags,
  no other actor ID, label, quote or rationale.
- Org B cannot read/edit/adjudicate Org A's batch even with full
  administrator privileges of Org B.
- Distinct third member adjudicates only two disagreeing labels; no
  self-adjudication or duplicate event.
- Revoked/downgraded membership and suspended org fail closed.
- Real database snapshots contain SHA-256 digests only; DB append-only
  triggers and C10 composite tenant FK regressions are also rerun.
- No provider tokens/paid calls; pinned Ruff; exact source SHA and
  GitHub-only CI evidence recorded before any claim of completion.

## Remaining product and release requirements

1. **No C8 UI-to-API connection yet.** The staff review screen continues
   using private offline CSV. C11 is a separate source-only endpoint
   layer; production has neither migrated its C10 tables nor enabled
   the C11 feature switch.
2. This does **not** implement independent login credentials for each
   reviewer; it validates whatever authenticated subject the production
   identity boundary supplies. Real Nhost issuer/JWKS and membership
   revocation testing against isolated staging is **not** covered by
   fictional CI JWTs.
3. C10 DB append-only triggers are not cryptographic WORM. Database
   RLS/least-privilege credentials, schema rollout, retention/deletion,
   audit external replication, source text encryption, and actual
   authenticated reviewer signing need independent design/approval.
4. Synthetic ID or hash values are not proof data is free of PII.
   Never paste customer text into this prototype; the service uses
   only the checked-in fictional dataset.
5. Real model semantic answer quality, Farsi/English faithfulness and
   signed independent human reviews remain unevaluated. A valid
   citation marker is not general evidence of truth.
6. API rollout to staging, provider/DB secrets, backend deploy,
   real-account UAT or production use requires **separate approval**.
   This PR alone grants no such permission.

**RELEASE HOLD:** no merge, production deploy, real DB migration,
paid model, Render synthetic Cron activation, Phase 1Y real ticket
writes, secret copying, public repository visibility change or external
provider tool execution.
