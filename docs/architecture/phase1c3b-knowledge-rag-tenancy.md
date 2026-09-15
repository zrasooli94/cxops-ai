# Phase 1C.3B — Knowledge / RAG Tenant Isolation

This document describes the tenant boundary added to every knowledge/RAG surface
in Phase 1C.3B: document ownership, chunk ownership, tenant-scoped ingestion,
and tenant-filtered vector retrieval. It closes the previously Critical defect
where a semantic search could retrieve content belonging to another
organization.

**Three invariants govern this phase:**

> **Vector similarity never overrides the tenant boundary.**

> **Legacy NULL-org knowledge is inert until explicitly migrated.**

> **A chunk's organization can never drift from its document.** Because
> `organization_id` is duplicated onto `knowledge_chunks` for direct vector
> filtering, the replicated owner column is bound to the document at the
> database: the composite foreign key
> `(document_id, organization_id) → knowledge_documents(id, organization_id)`
> (backed by `UNIQUE (id, organization_id)` on the document) refuses any chunk
> whose `organization_id` does not match its document's. Vector queries keep
> filtering on the chunk's own `organization_id` — the DB guarantee makes the
> chunk-level predicate sufficient, so no post-join document check is needed.

---

## Data Flow

```
AuthenticatedPrincipal                       (Phase 1B — who the user is)
  → CurrentTenant                            (Phase 1C.1 — which org, resolved
       from memberships; the X-CXOps-Organization-ID header is only a selector)
  → tenant-owned KnowledgeDocument
  → tenant-owned KnowledgeChunk
  → tenant-filtered vector retrieval         (WHERE organization_id = :org, in SQL)
     → RAG answer grounding
     → tenant-only citations
```

Ownership of every new document/chunk originates **only** from
`tenant.organization_id`. It is never taken from the request payload, a JWT org
claim, browser-supplied metadata, or the checksum owner. No knowledge route
accepts an `organization_id` in its request body.

---

## Ownership Model

`organization_id` is added as a nullable FK → `organizations.id` on **both**
tables (document and chunk), matching the Phase 1C.3B preferred design:

| Table | Column | Purpose |
|-------|--------|---------|
| `knowledge_documents.organization_id` | int FK, NULL = legacy/unowned | document owner |
| `knowledge_chunks.organization_id` | int FK, NULL = legacy/unowned | chunk owner — the column the vector query predicates on |

Direct `organization_id` on chunks (rather than a join to the document) is the
chosen design because the top-k retrieval query must place the tenant predicate
on the table being ranked by embedding distance. A document join could be made
correct, but the direct column makes the "predicate fused with retrieval" proof
trivial and matches the phase requirement explicitly.

Chunks also keep `document_id` and the existing
`uq_knowledge_chunk_document_index (document_id, chunk_index)` constraint.

### Document / Chunk Tenant Consistency Gate

`organization_id` is deliberately duplicated onto `knowledge_chunks` so the
vector query can predicate directly on the table being ranked. That replicated
owner must not be able to drift from the owning document, or a chunk Org A owns
could cite an Org B document as RAG evidence. The drift is closed at the
database, not in application code:

`alembic/versions/1c3b0002_knowledge_document_chunk_consistency.py`:

- adds `UNIQUE (id, organization_id)` on `knowledge_documents` (the composite
  FK target; `id` alone is already the PK, so this exists purely as the target)
- replaces the chunk's single-column `document_id` FK with the composite
  foreign key `(document_id, organization_id) →
  knowledge_documents(id, organization_id)` with `ON DELETE CASCADE`

The composite FK is the tenant-consistency constraint: inserting a chunk whose
`organization_id` differs from its document's violates the FK. Postgres `MATCH
SIMPLE` semantics leave NULL-org legacy chunks exempt, which is the staged
legacy contract — NULL rows remain inert and are later explicitly mapped and
moved to `NOT NULL`. The vector-retrieval query is **unchanged** (still filters
the chunk's own `organization_id`); the DB guarantee makes that chunk-level
predicate sufficient, so no extra document join or post-filter is required.

---

## Staged Legacy Migration

`alembic/versions/1c3b0001_knowledge_tenant_isolation.py`:

- adds `organization_id` **nullable** on both tables (no backfill — no
  arbitrary assignment of existing global rows)
- adds FKs to `organizations.id`
- adds btree indexes on each `organization_id`
- replaces the global unique index `ix_knowledge_documents_checksum` with the
  tenant-composite unique constraint
  `ux_knowledge_documents_organization_id_checksum`
- downgrade reverses every step and restores the global unique checksum index

**Migration staging rationale:** unresolved legacy rows keep
`organization_id = NULL` and become inert. A later, separately-authorized
migration maps legacy rows to explicit organizations and then
`ALTER ... SET NOT NULL`. That NOT NULL transition is **not** part of this
phase; it must occur only after legacy data is explicitly mapped.

Model metadata exactly matches the migration schema — `alembic check` reports
`No new upgrade operations detected.`

---

## Checksum / Deduplication

Old: `checksum` was globally unique — one content fingerprint for every
organization; an existing document in Org B could block Org A's ingest and
doubled as a cross-tenant existence oracle.

New: uniqueness is the composite `(organization_id, checksum)`.

- same file uploaded twice in Org A → deduped within Org A
- same file in Org A and Org B → both organizations may own their own copy
- a checksum collision never becomes an oracle, a blocker, or a reference to
  another tenant's document
- legacy NULL-org rows never satisfy a tenant's dedupe lookup

The dedupe lookup is `get_document_by_checksum_for_tenant(db, checksum,
organization_id)` — always scoped.

---

## Semantic Search — Tenant Filter Is Part of the SQL

The repository exposes **no global search**. The single retrieval entry point
requires `organization_id` (an `int`, never optional):

```
KnowledgeRepository.semantic_search_statement(embedding, organization_id, limit)
  → SELECT knowledge_chunks.*, embedding <=> :q AS distance
    WHERE knowledge_chunks.organization_id = :org
    ORDER BY embedding <=> :q
    LIMIT :k
```

The tenant predicate is part of the statement **before** nearest-neighbor
ranking. Top-k eligibility is restricted to the tenant's own chunks, so a
foreign tenant's near-perfect match can never consume a rank slot and then be
"filtered away" in Python — the spec's explicitly prohibited pattern.

`KnowledgeSearchService.search`, `rag_service.answer`, and every RAG caller all
require `organization_id` and never fall back to an unscoped search. There is
deliberately no `semantic_search_unscoped` — no code path is authorized to read
unowned knowledge.

---

## RAG Answer Pipeline

```
CurrentTenant
  → query embedding
  → tenant-scoped semantic search (SQL predicate)
  → tenant-owned evidence (chunks)
  → filter_and_format_sources (similarity threshold)
  → grounded prompt
  → answer + citations
```

Citations (`sources`) are built exclusively from the tenant-scoped result set.
Every `document_id`, `chunk_id`, title, and content in the response belongs to
the resolved organization. NULL-org chunks cannot be selected — they fail the
SQL predicate — so they can never appear in evidence or citations.

---

## Internal / Agent Callers

- **Agent workflow** (`agent_workflow_service._retrieve_knowledge`): derives
  the RAG organization from the persisted `ticket.organization_id` in workflow
  state and passes it through. A ticket with `organization_id = NULL` (legacy)
  receives **no knowledge** — the workflow returns empty sources and never
  falls back to a global search. (Full AgentRun/approval/execution tenancy
  remains Phase 1C.3C.)
- **RAG evaluation** (`RAGEvaluationService`) and the CLI scripts
  `scripts/evaluate_rag.py` / `scripts/seed_knowledge_base.py`: both now
  require an explicit `--organization-id`. The seed script has no
  `organization_id = None` path — ingestion fails closed rather than create an
  unowned row.

---

## Legacy NULL-org Behavior

Rows with `organization_id = NULL` are unreachable by every tenant-facing path:

- not returned by `list_documents_for_tenant`
- not returned by `get_document_for_tenant` (foreign or missing → 404)
- not deletable by `delete_document_for_tenant`
- not eligible in `semantic_search_for_tenant` (SQL predicate misses NULL)
- never a dedupe match
- never referenced in citations

No temporary "global knowledge" concept is exposed.

---

## Index / pgvector Review

| Name | Kind | Justification |
|------|------|---------------|
| `ix_knowledge_documents_organization_id` | btree | `WHERE organization_id = :org` on list/get/delete/dedupe |
| `ix_knowledge_chunks_organization_id` | btree | vector retrieval's org predicate |
| `ix_knowledge_chunks_document_id` | btree | pre-existing FK index (unchanged) |
| `ux_knowledge_documents_organization_id_checksum` | unique composite | tenant dedupe |

There is no embedding index today — vector search is a sequential scan, so
adding `WHERE organization_id = :org` is trivially servable: Postgres filters by
organization before computing cosine distance, and the btree org index narrows
the candidate set. A global HNSW/IVFFlat index on `embedding` was not added:
such an index is proximity-based across the whole corpus and does not change the
correctness requirement (the tenant predicate must still apply), and its
approximate-k behavior makes no correctness argument. Correctness-first: keep
the org predicate + btree org index, and evaluate a per-tenant-capable vector
index strategy in a performance phase.

---

## Security Tests

`tests/test_knowledge_tenant.py` (18 tests, offline/deterministic — embeddings
and the answer LLM are monkeypatched):

- A list returns only owned documents
- B foreign document read is 404
- C foreign document delete is 404; own delete cascades chunks
- D search returns only owned chunks
- E Org B's closer-chunk never appears in Org A's top-k (API)
- F RAG answer cites only Org A documents
- G same checksum tolerated across organizations
- H duplicate same-content upload dedupes within an organization
- I forged `X-CXOps-Organization-ID` → 403 before RAG access
- J no membership → 403
- K multi-membership without selector → 409
- L valid selector scopes knowledge to the selected org
- M legacy NULL-org document/chunk is inert (list + search)
- N document ownership comes from CurrentTenant
- O every generated chunk inherits the same organization
- P repository SQL embeds the tenant predicate + vector-leak regression (the
  test returns the foreign chunk if the predicate is removed)
- Q foreign document existence is not revealed (indistinguishable 404s)
- ingestion fails closed without `organization_id`

**Vector-leak regression (spec #15):** Org A chunk at cosine distance 0.4, Org
B chunk at distance 0.0. Searching Org A with `top_k=1` returns Org A's chunk.
A post-retrieval filter would return Org B's perfect match. The strongly-fused
predicate is verified both functionally and by compiling the statement.

---

## Scope Boundaries

In scope: knowledge/RAG tenancy + minimum caller changes.

Explicitly deferred:
- AgentRun / approval / execution tenancy → **Phase 1C.3C**
- Observability tenancy → later phase
- RBAC → **Phase 1D**
- Organization switcher UI → **Phase 1C.4**
- `NOT NULL` transition on `organization_id` → a separately-authorized legacy
  cleanup migration