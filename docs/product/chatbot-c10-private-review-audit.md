# CXOps AI — C10 tenant-private reviewer assignment and audit foundation

**21 October 2026 · SOURCE ONLY · DRAFT · NOT MIGRATED OUTSIDE CI · RELEASE HOLD**

## What this delivers

C8/C9 added a staff-visible source-review UI and actual Linux Chrome tests
with **fictional** logged-in roles. The C6/C7 proof format, however, still
requires two private CSVs plus independent Python combine/score and does not
persist reviewer assignments or a defensible audit.

C10 builds the **backend schema and service only** for a future authenticated
review workflow. It does NOT publish API routes, connect the staff C8 UI to
a new backend, create real users or run database migrations outside one
disposable GitHub Actions PostgreSQL 16 service.

**Four new tables**, expand-only after migration `1q0a0001`:

| Table | Durable record | Confidentiality / integrity |
| --- | --- | --- |
| `claim_review_batches` | Tenant, fixed fictional C6 dataset ID, canonical SHA-256 digest, creator subject and timestamp | Unique `(id,organization_id)`; no source or prompt text |
| `claim_review_items` | Individual review key, canonical C6 fingerprint, SHA-256 cited passage digest, bounded synthetic source revision IDs | Composite FK to same-tenant batch, one item per batch/key |
| `claim_review_assignments` | Batch, immutable reviewer slot 1/2, stable authenticated subject IDs and assigning subject | Two unique slots and distinct subjects, composite tenant FK; current membership rechecked on writes |
| `claim_review_events` | Append-only per-claim reviewer label / third-person adjudication label, actor, assignment, SHA-256 exact evidence or rationale digest | Composite FKs to batch/item/assignment, unique review-per-item/assignment and adjudication-per-item, SQL constraints and DB trigger identity/independence check |

**No raw customer conversation, name/email/contact, submitted source passage,
plain-text evidence quotation, or private review note** is persisted by this
C10 service. `actor_subject` is still a stable user identifier; treat table
rows as sensitive internal audit data. Digests may be dictionary-attackable
if input phrases are predictable; they are not encryption. Restrict DB access
and retention in a future approved rollout.

### Application-level rules

`PrivateClaimReviewService` receives an **upstream-authenticated**
`AuthorizationContext`. Because no API is exposed, C10 tests construct
fictional contexts. **Do not treat a caller-constructed context as trusted**:
a future HTTP route must verify a real session/JWKS and current selected
organization before invoking any method. The service adds a further
database-backed check of matching current membership role and active org
lifecycle; every access binds the selected tenant ID.

- Create review batch from the **fixed, bundled fictional C6 dataset only**,
  generating per-claim/source revision hashes. No arbitrary user-provided
  dataset JSON is accepted by this service. The PostgreSQL table also
  explicitly rejects non-C6 dataset IDs.
- Assign exactly two distinct members who currently have
  `evaluation.manage`-eligible roles in that organization
  (owner/admin/supervisor) to slots 1 and 2, atomically, once.
  No changing reviewer identities after assignment; a reassign workflow
  would require a new versioned batch, not rewriting audit history.
- Allow a reviewer to see only **their own assigned** claim queue, without
  exposing other reviewers' labels, private comments or full source text.
  All normalized review/source content lives in the bundled fictional fixture
  and is fingerprint-checked against the frozen batch/item records.
- Permit each assigned reviewer one immutable submission per claim. The
  supported/contradicted label requires an **8–1,000-character exact quote
  from a cited source**. Unsupported/unclear requires 10–500 characters of
  explanation. Only SHA-256 digests and a closed label are persisted.
- A **third, distinct, currently authorized** organization member may
  adjudicate a claim only if two reviewer judgments exist and disagree.
  If labels match, no adjudication event may be inserted.
- The service adds no external provider action, agent execution, email,
  billing API or public reply, and never changes existing evaluation-run
  release decisions.

### Database defense in depth

- `(batch_id,organization_id)` composite FKs on items/assignments/events,
  plus item/assignment `(id,organization_id,batch_id)` FKs on events,
  forbid references crossing tenants or batches **even on direct DB insert**.
- PostgreSQL triggers prevent `UPDATE` and `DELETE` on all four new
  tables, and validate a direct `review` event's actor against the exact
  assignment. An `adjudication` INSERT is refused unless there are two
  submitted, disagreeing labels and the actor is not one of the two assigned.
- Partial unique indexes refuse duplicate submitted judgments from the
  same assignee or duplicate adjudication for an item. Service row locks on
  claim item and batch serialize same-item concurrent operations.
- Existing releases still work against the same schema because the C10
  migration **adds only new tables/functions/triggers**, no existing-column
  alterations. Older application code does not read these tables.
- These triggers make audit records **append-only for ordinary database
  roles**, not tamper-proof WORM: DB owners/superusers or explicit migrations
  can bypass/drop triggers. The DB currently relies on application tenant
  predicates and composite integrity constraints rather than a new
  PostgreSQL row-level-security tenant principal. C10 does NOT certify full
  defense against an attacker with raw SQL privileges.

### Auth / release caveats

Current owner/admin/supervisor roles are the only roles with
`evaluation.manage`. The model/service handles this explicitly using
the current organization-membership record and effective lifecycle
capabilities. It does not infer rights from a role name in a CSV, a query
parameter, JWT user-supplied JSON or local browser storage.

**No HTTP endpoints or client integration:** current C8 staff review stays
in-memory and downloads private CSV. C10's schema is *not installed* in a
running environment until a separate authorized migration. No reviewer
identity should be claimed independently authenticated in live systems
without a real API principal + latest membership validation.

## C10 disposable CI

GitHub Actions C10 lane:
- Before importing/migrating application, invokes the existing strict
  `scripts/chatbot_c2_disposable_guard.py`: exact CI flags, empty OpenAI/
  Zendesk/email/Telegram credentials, and exact local disposable asyncpg
  URL `127.0.0.1:5432/cxops_chatbot_c2_disposable_test`.
- PostgreSQL 16 + pgvector container, all Alembic migrations through C10,
  single Alembic HEAD proof, real async SQLAlchemy integration tests,
  prior C6 offline scorer tests, pinned Ruff.
- Tests source fingerprinting and no raw evidence storage, two-person
  slot separation, current membership and lifecycle downgrades, exact
  citation requirement, tenant-crossing direct FK attacks, forged actor
  trigger, direct audit UPDATE/DELETE trigger, duplicate review/adjudication
  conflict, and migration version.
- Optional disposable downgrade/re-upgrade verifies schema isolation and
  reversibility. Nothing is executed against a production/staging DB.

**Remaining gates:** no real enterprise data, authenticated staff API,
per-tenant DB RLS, isolated service credentials, human review independence
verification, durable evidence text encryption, reviewer-notification
workflow, browser integration, true real-model factuality measurements or
owner-authorized rollout. All are future explicit review tasks.

**RELEASE HOLD**: do not merge/deploy C10, run migrations on real DB,
activate Render Cron, change production worker, turn on Phase 1Y ticket
writes, transfer secrets or change repo visibility. Separate worker
external-provider duplicate/replay risks remain unresolved.
