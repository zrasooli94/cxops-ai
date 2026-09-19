"""Knowledge / RAG tenant-isolation security tests (Phase 1C.3B).

Two synthetic organizations (Org A → user-alpha, Org B → user-beta) with no
shared membership prove that every tenant-facing knowledge surface is bounded
by the resolved organization:

- document list / get / delete
- semantic search (top-k cannot be consumed by a foreign tenant's chunk)
- RAG answer + citations
- ingestion (dedupe, organization origin, chunk ownership inheritance)

Embeddings and the answer LLM are monkeypatched to deterministic doubles so the
suite never touches OpenAI. Legacy NULL-org rows are created directly to prove
they are inert.

Tests A–Q + the vector-leak regression map 1:1 onto the Phase 1C.3B spec list.
The two ``document_chunk_*`` tests form the tenant-consistency gate: they prove
the database composite FK rejects a chunk whose ``organization_id`` drifted from
its document, so a malformed chunk can never become a tenant's RAG evidence.
"""

import hashlib
import math
import os
import time
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import delete, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

os.environ["AUTH_MODE"] = "hs256"
os.environ["AUTH_JWT_SECRET"] = "z" * 32
os.environ["AUTH_JWT_ALGORITHM"] = "HS256"
os.environ["AUTH_JWT_ISSUER"] = "test-knowledge-issuer"
os.environ["AUTH_JWT_AUDIENCE"] = "test-knowledge-audience"
os.environ["AUTH_DEV_MODE"] = "False"
os.environ["ENVIRONMENT"] = "development"

from app.core.config import reset_settings_cache
from app.core.database import AsyncSessionLocal
from app.core.rbac import OrganizationRole
from app.main import app
from app.models.ai_request_log import AIRequestLog
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.knowledge_document import KnowledgeDocument
from app.models.organization import Organization
from app.models.organization_membership import OrganizationMembership
from app.repositories.knowledge_repository import KnowledgeRepository
from app.services.embedding_service import embedding_service
from app.services.knowledge_ingestion_service import KnowledgeIngestionService
from app.services.rag_service import rag_service

TEST_SECRET = "z" * 32
TEST_ISSUER = "test-knowledge-issuer"
TEST_AUDIENCE = "test-knowledge-audience"

USER_ALPHA = "user-alpha"
USER_BETA = "user-beta"
USER_NONE = "user-none"

X_TENANT = "X-CXOps-Organization-ID"

EMBEDDING_DIMS = 1536


def _configure(monkeypatch, **overrides) -> None:
    values = {
        "AUTH_MODE": "hs256",
        "AUTH_JWT_SECRET": TEST_SECRET,
        "AUTH_JWT_ALGORITHM": "HS256",
        "AUTH_JWT_ISSUER": TEST_ISSUER,
        "AUTH_JWT_AUDIENCE": TEST_AUDIENCE,
        "AUTH_DEV_MODE": "False",
        "ENVIRONMENT": "development",
    }
    values.update(overrides)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    reset_settings_cache()


@pytest.fixture(autouse=True)
def _configure_auth(monkeypatch):
    _configure(monkeypatch)
    yield


def _build_token(*, sub: str, secret: str = TEST_SECRET) -> str:
    now = int(time.time())
    payload = {
        "sub": sub,
        "email": f"{sub}@example.com",
        "iss": TEST_ISSUER,
        "aud": TEST_AUDIENCE,
        "exp": now + 3600,
        "iat": now,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def _auth_headers(sub: str, *, tenant_id: int | None = None) -> dict:
    headers = {"Authorization": f"Bearer {_build_token(sub=sub)}"}
    if tenant_id is not None:
        headers[X_TENANT] = str(tenant_id)
    return headers


def _deterministic_vector(*, seed_text: str) -> list[float]:
    digest = hashlib.sha256(seed_text.encode("utf-8")).digest()
    seed = int.from_bytes(digest[:8], "big")
    values = []
    state = seed
    for _ in range(EMBEDDING_DIMS):
        state = (state * 1103515245 + 12345) % (2**31)
        values.append(((state / (2**31 - 1)) * 2.0) - 1.0)
    norm = math.sqrt(sum(value * value for value in values))
    return [value / norm for value in values]


def _unit_vector(*, shared_component: int) -> list[float]:
    """Unit vector with exactly ``shared_component`` leading ones."""
    values = [0.0] * EMBEDDING_DIMS
    for index in range(shared_component):
        values[index] = 1.0
    norm = math.sqrt(shared_component)
    return [value / norm for value in values]


def _vector_with_query(*, q_weight: float, extra_weight: float) -> list[float]:
    """Unit vector with nonzero overlap with ``_unit_vector(1)`` (the query q).

    q = e1. This vector = normalize(q_weight·e1 + extra_weight·e2), so its
    cosine similarity to q is fixed and tunable.
    """
    values = [0.0] * EMBEDDING_DIMS
    values[0] = q_weight
    values[1] = extra_weight
    norm = math.sqrt(q_weight**2 + extra_weight**2)
    return [value / norm for value in values]


@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


@pytest_asyncio.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def two_orgs(db):
    """Org A (user-alpha), Org B (user-beta); cleans all knowledge/teardown rows."""
    org_ids: list[int] = []
    log_ids: list[str] = []

    async def make_org(name: str, *, subject: str | None = None) -> Organization:
        org = Organization(name=name)
        db.add(org)
        await db.flush()
        org_ids.append(org.id)
        if subject is not None:
            db.add(
                OrganizationMembership(
                    subject=subject,
                    organization_id=org.id,
                    role=OrganizationRole.OWNER,
                )
            )
        await db.commit()
        return org

    org_a = await make_org(f"org-a-{uuid.uuid4().hex[:8]}", subject=USER_ALPHA)
    org_b = await make_org(f"org-b-{uuid.uuid4().hex[:8]}", subject=USER_BETA)

    async def ingest(
        organization_id: int,
        *,
        title: str,
        content: str,
        source: str = "manual",
        metadata: dict | None = None,
    ) -> dict:
        return await KnowledgeIngestionService.ingest(
            db=db,
            organization_id=organization_id,
            title=title,
            content=content,
            source=source,
            source_uri=None,
            metadata=(metadata or {}),
        )

    async def add_membership(subject: str, organization_id: int) -> None:
        db.add(
            OrganizationMembership(
                subject=subject,
                organization_id=organization_id,
                role=OrganizationRole.OWNER,
            )
        )
        await db.commit()

    async def track_log(request_id: str) -> None:
        log_ids.append(request_id)

    yield {
        "org_a": org_a,
        "org_b": org_b,
        "ingest": ingest,
        "add_membership": add_membership,
        "track_log": track_log,
    }

    # Broadcast teardown: knowledge rows first, then logs, memberships, orgs.
    if org_ids:
        await db.execute(
            delete(KnowledgeChunk).where(
                KnowledgeChunk.organization_id.in_(org_ids)
            )
        )
        await db.execute(
            delete(KnowledgeDocument).where(
                KnowledgeDocument.organization_id.in_(org_ids)
            )
        )
    if log_ids:
        # AIRequestLog rows reference organizations; delete before org teardown.
        await db.execute(
            delete(AIRequestLog).where(
                AIRequestLog.request_id.in_(log_ids),
                AIRequestLog.organization_id.in_(org_ids),
            )
        )
        await db.execute(
            delete(AIRequestLog).where(
                AIRequestLog.organization_id.in_(org_ids)
            )
        )
    if org_ids:
        await db.execute(
            delete(OrganizationMembership).where(
                OrganizationMembership.organization_id.in_(org_ids)
            )
        )
        await db.execute(
            delete(Organization).where(Organization.id.in_(org_ids))
        )
    await db.commit()


@pytest.fixture(autouse=True)
def _block_real_openai_embeddings(monkeypatch):
    """Fail loudly if the real OpenAI embeddings client is ever invoked.

    The documented guarantee of this file is zero OpenAI network calls. Any
    access to ``embedding_service.embed_text``/``embed_documents`` from a
    knowledge test without an installed fake raises immediately, so a forgotten
    ``_fake_embeddings``/``_fake_embedding_returns`` produces a loud local
    failure instead of a live API call (and a spurious CI 401 with a test key).
    The fakes below override these raisers after this autouse fixture runs.
    """

    async def _raise(*_args):
        raise RuntimeError(
            "Real OpenAI embeddings client invoked from a knowledge test. "
            "Request _fake_embeddings or _fake_embedding_returns so the suite "
            "stays offline."
        )

    monkeypatch.setattr(embedding_service, "embed_text", _raise)
    monkeypatch.setattr(embedding_service, "embed_documents", _raise)


@pytest.fixture
def _fake_embeddings(monkeypatch):
    """Deterministic offline embeddings for both embed calls."""

    async def _embed_text(text: str) -> list[float]:
        return _deterministic_vector(seed_text=text)

    async def _embed_documents(texts: list[str]) -> list[list[float]]:
        return [_deterministic_vector(seed_text=text) for text in texts]

    monkeypatch.setattr(embedding_service, "embed_text", _embed_text)
    monkeypatch.setattr(embedding_service, "embed_documents", _embed_documents)


@pytest.fixture
def _fake_embedding_returns(monkeypatch, _fake_embeddings):
    """Override the QUERY embedding to a canned vector — fully offline.

    Depends on ``_fake_embeddings`` so the DOCUMENT embedding path
    (``embed_documents``) is deterministically stubbed too: a test that uses
    this fixture to control a query embedding and then calls
    ``two_orgs["ingest"]`` (which embeds documents) must never reach the real
    OpenAI client. Tests that use this fixture are safe to ingest, search, and
    answer offline.
    """

    def _patch(vector: list[float]):
        async def _embed_text(_text: str) -> list[float]:
            return vector

        monkeypatch.setattr(embedding_service, "embed_text", _embed_text)
        return vector

    return _patch


class _FakeLLM:
    def __init__(self, content: str) -> None:
        self.content = content

    async def ainvoke(self, _messages):
        return type(
            "_FakeResponse",
            (),
            {
                "content": self.content,
                "usage_metadata": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                },
            },
        )()


@pytest.fixture
def _fake_rag_llm(monkeypatch):
    def _patch(content: str) -> None:
        monkeypatch.setattr(rag_service, "llm", _FakeLLM(content))

    return _patch


# ---------------------------------------------------------------- ingestion


@pytest.mark.asyncio
async def test_ingest_requires_organization_id(db, _fake_embeddings):
    with pytest.raises(ValueError):
        await KnowledgeIngestionService.ingest(
            db=db,
            organization_id=None,
            title="x",
            content="some body text",
            source="manual",
            source_uri=None,
            metadata={},
        )


@pytest.mark.asyncio
async def test_g_same_checksum_content_tolerated_across_orgs(
    db,
    two_orgs,
    _fake_embeddings,
):
    org_a = two_orgs["org_a"]
    org_b = two_orgs["org_b"]

    shared_content = "The exact same policy text must be owned independently."

    result_a = await two_orgs["ingest"](
        org_a.id,
        title="Shared in A",
        content=shared_content,
    )
    result_b = await two_orgs["ingest"](
        org_b.id,
        title="Shared in B",
        content=shared_content,
    )

    assert result_a["duplicate"] is False
    assert result_b["duplicate"] is False
    assert result_a["document_id"] != result_b["document_id"]


@pytest.mark.asyncio
async def test_h_duplicate_content_dedupes_within_org(
    db,
    two_orgs,
    _fake_embeddings,
):
    org_a = two_orgs["org_a"]

    first = await two_orgs["ingest"](
        org_a.id,
        title="Deposit policy",
        content="Deposits appear immediately after confirmation.",
    )
    second = await two_orgs["ingest"](
        org_a.id,
        title="Deposit policy",
        content="Deposits appear immediately after confirmation.",
    )

    assert second["duplicate"] is True
    assert second["document_id"] == first["document_id"]
    assert second["chunks_created"] == 0


@pytest.mark.asyncio
async def test_n_and_o_ownership_origins_from_current_tenant(
    db,
    client,
    two_orgs,
    _fake_embeddings,
):
    org_a = two_orgs["org_a"]

    response = await client.post(
        "/knowledge/documents",
        json={
            "title": "Tenant origin policy",
            "content": "Ownership must come from the authenticated tenant.",
            "source": "manual",
            "metadata": {"department": "security"},
        },
        headers=_auth_headers(USER_ALPHA),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["duplicate"] is False

    document_id = body["document_id"]

    document = await db.get(KnowledgeDocument, document_id)
    assert document is not None
    assert document.organization_id == org_a.id

    chunks = (
        await db.execute(
            select(KnowledgeChunk).where(
                KnowledgeChunk.document_id == document_id
            )
        )
    ).scalars().all()

    assert len(chunks) > 0
    assert all(chunk.organization_id == org_a.id for chunk in chunks)


# ------------------------------------------------------------- list/get/delete


@pytest.mark.asyncio
async def test_a_list_returns_only_owned_documents(
    db,
    client,
    two_orgs,
    _fake_embeddings,
):
    await two_orgs["ingest"](
        two_orgs["org_a"].id,
        title="Alpha-owned document",
        content="Alpha content body.",
    )
    await two_orgs["ingest"](
        two_orgs["org_b"].id,
        title="Beta-owned document",
        content="Beta content body.",
    )

    response = await client.get(
        "/knowledge/documents",
        headers=_auth_headers(USER_ALPHA),
    )

    assert response.status_code == 200
    titles = [item["title"] for item in response.json()]
    assert "Alpha-owned document" in titles
    assert "Beta-owned document" not in titles


@pytest.mark.asyncio
async def test_b_get_foreign_document_404(
    db,
    client,
    two_orgs,
    _fake_embeddings,
):
    result_b = await two_orgs["ingest"](
        two_orgs["org_b"].id,
        title="Beta secret",
        content="Secret that alpha must never see.",
    )

    response = await client.get(
        f"/knowledge/documents/{result_b['document_id']}",
        headers=_auth_headers(USER_ALPHA),
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_c_delete_foreign_document_404_and_own_delete_ok(
    db,
    client,
    two_orgs,
    _fake_embeddings,
):
    result_b = await two_orgs["ingest"](
        two_orgs["org_b"].id,
        title="Beta deletable",
        content="Beta content.",
    )
    result_a = await two_orgs["ingest"](
        two_orgs["org_a"].id,
        title="Alpha deletable",
        content="Alpha content.",
    )

    response = await client.delete(
        f"/knowledge/documents/{result_b['document_id']}",
        headers=_auth_headers(USER_ALPHA),
    )

    assert response.status_code == 404

    still_there = await db.get(KnowledgeDocument, result_b["document_id"])
    assert still_there is not None
    assert still_there.organization_id == two_orgs["org_b"].id

    response = await client.delete(
        f"/knowledge/documents/{result_a['document_id']}",
        headers=_auth_headers(USER_ALPHA),
    )

    assert response.status_code == 204

    gone = await db.get(KnowledgeDocument, result_a["document_id"])
    assert gone is None

    chunks = (
        await db.execute(
            select(KnowledgeChunk).where(
                KnowledgeChunk.document_id == result_a["document_id"]
            )
        )
    ).scalars().all()
    assert chunks == []


@pytest.mark.asyncio
async def test_q_foreign_document_existence_not_revealed(
    db,
    client,
    two_orgs,
    _fake_embeddings,
):
    result_b = await two_orgs["ingest"](
        two_orgs["org_b"].id,
        title="Beta existence oracle",
        content="Beta content.",
    )

    foreign = await client.get(
        f"/knowledge/documents/{result_b['document_id']}",
        headers=_auth_headers(USER_ALPHA),
    )
    missing = await client.get(
        "/knowledge/documents/999999991",
        headers=_auth_headers(USER_ALPHA),
    )

    assert foreign.status_code == 404
    assert missing.status_code == 404
    assert foreign.json()["detail"] == missing.json()["detail"]

    foreign_delete = await client.delete(
        f"/knowledge/documents/{result_b['document_id']}",
        headers=_auth_headers(USER_ALPHA),
    )
    missing_delete = await client.delete(
        "/knowledge/documents/999999991",
        headers=_auth_headers(USER_ALPHA),
    )

    assert foreign_delete.status_code == 404
    assert missing_delete.status_code == 404
    assert foreign_delete.json()["detail"] == missing_delete.json()["detail"]


# --------------------------------------------------------------- tenant gating


@pytest.mark.asyncio
async def test_i_forged_selector_403_before_rag(
    db,
    client,
    two_orgs,
    _fake_embeddings,
):
    org_b = two_orgs["org_b"]

    response = await client.post(
        "/knowledge/search",
        json={"query": "anything", "limit": 5},
        headers=_auth_headers(USER_ALPHA, tenant_id=org_b.id),
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_j_no_membership_403(
    db,
    client,
    two_orgs,
    _fake_embeddings,
):
    await two_orgs["ingest"](
        two_orgs["org_a"].id,
        title="Alpha content",
        content="Alpha body.",
    )

    response = await client.post(
        "/knowledge/search",
        json={"query": "anything", "limit": 5},
        headers=_auth_headers(USER_NONE),
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_k_multi_membership_without_selector_409(
    db,
    client,
    two_orgs,
    _fake_embeddings,
):
    await two_orgs["add_membership"](USER_ALPHA, two_orgs["org_b"].id)

    response = await client.post(
        "/knowledge/search",
        json={"query": "anything", "limit": 5},
        headers=_auth_headers(USER_ALPHA),
    )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_l_valid_selector_scopes_to_selected_org(
    db,
    client,
    two_orgs,
    _fake_embeddings,
):
    org_b = two_orgs["org_b"]
    await two_orgs["add_membership"](USER_ALPHA, org_b.id)

    shared = "Shared content owned by both organizations."

    await two_orgs["ingest"](two_orgs["org_a"].id, title="A copy", content=shared)
    result_b = await two_orgs["ingest"](org_b.id, title="B copy", content=shared)

    response = await client.post(
        "/knowledge/search",
        json={"query": "shared content policy", "limit": 5},
        headers=_auth_headers(USER_ALPHA, tenant_id=org_b.id),
    )

    assert response.status_code == 200
    results = response.json()
    assert len(results) > 0
    assert all(
        item["document_id"] == result_b["document_id"] for item in results
    )


# ------------------------------------------------------------------- search


@pytest.mark.asyncio
async def test_d_search_returns_only_owned_chunks(
    db,
    client,
    two_orgs,
    _fake_embeddings,
):
    result_a = await two_orgs["ingest"](
        two_orgs["org_a"].id,
        title="Alpha refund policy",
        content="Approved refunds are initiated within two business days.",
    )
    await two_orgs["ingest"](
        two_orgs["org_b"].id,
        title="Beta refund policy",
        content="Beta refunds are initiated within forty business days.",
    )

    response = await client.post(
        "/knowledge/search",
        json={"query": "refund policy", "limit": 5},
        headers=_auth_headers(USER_ALPHA),
    )

    assert response.status_code == 200
    results = response.json()
    assert len(results) > 0
    assert all(
        item["document_id"] == result_a["document_id"] for item in results
    )


@pytest.mark.asyncio
async def test_m_legacy_null_org_knowledge_is_inert(
    db,
    client,
    two_orgs,
    _fake_embeddings,
):
    await two_orgs["ingest"](
        two_orgs["org_a"].id,
        title="Alpha live document",
        content="Alpha content.",
    )

    legacy = KnowledgeDocument(
        organization_id=None,
        title="Legacy unowned document",
        source="legacy",
        source_uri=None,
        checksum=uuid.uuid4().hex,
        metadata_json={},
    )
    await KnowledgeRepository.create_document(db, legacy)
    legacy_chunk = KnowledgeChunk(
        organization_id=None,
        document_id=legacy.id,
        chunk_index=0,
        content="Legacy unowned chunk content that must stay inert.",
        token_count=10,
        metadata_json={"title": "Legacy unowned document"},
        embedding=_deterministic_vector(seed_text="legacy"),
    )
    await KnowledgeRepository.create_chunks(db, [legacy_chunk])

    list_response = await client.get(
        "/knowledge/documents",
        headers=_auth_headers(USER_ALPHA),
    )

    assert list_response.status_code == 200
    titles = [item["title"] for item in list_response.json()]
    assert "Legacy unowned document" not in titles

    # Legacy chunk is never returned because it fails the tenant predicate,
    # regardless of its embedding distance to the query.
    search_response = await client.post(
        "/knowledge/search",
        json={"query": "legacy unowned chunk content", "limit": 5},
        headers=_auth_headers(USER_ALPHA),
    )

    assert search_response.status_code == 200
    results = search_response.json()
    assert all(
        item["chunk_id"] != legacy_chunk.id for item in results
    )


# ------------------------------------------------------ vector-leak regression


def test_p_semantic_search_statement_embeds_tenant_predicate():
    query = _deterministic_vector(seed_text="irrelevant")

    statement = KnowledgeRepository.semantic_search_statement(
        embedding=query,
        organization_id=4242,
        limit=5,
    )

    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "knowledge_chunks.organization_id" in sql
    knowledge_predicate_index = sql.index("knowledge_chunks.organization_id")
    order_index = sql.index("ORDER BY knowledge_chunks.embedding <=>")
    assert knowledge_predicate_index < order_index
    assert sql.count("SELECT") == 1
    assert "LIMIT" in sql


@pytest.mark.asyncio
async def test_p_vector_leak_top_k_never_served_by_foreign_chunk(
    db,
    two_orgs,
):
    """The tenant predicate is fused into retrieval, not applied afterwards.

    Org B's chunk is a PERFECT match for the query (cosine distance 0.0). Org
    A's chunk is only moderately similar. Searching Org A with top_k=1 MUST
    return Org A's chunk. If anyone moved the organization filter out of the
    SQL statement (global nearest neighbors first, filter later in Python),
    Org B's perfect match would consume the top-1 rank and this test fails.
    """
    org_a = two_orgs["org_a"]
    org_b = two_orgs["org_b"]

    query_vector = _unit_vector(shared_component=1)  # e1 -> B is distance 0
    a_vector = _vector_with_query(q_weight=0.6, extra_weight=0.8)  # distance 0.4

    async def _make_document(
        organization_id: int,
        *,
        title: str,
        content: str,
        embedding: list[float],
    ) -> None:
        document = KnowledgeDocument(
            organization_id=organization_id,
            title=title,
            source="direct",
            source_uri=None,
            checksum=uuid.uuid4().hex,
            metadata_json={},
        )
        await KnowledgeRepository.create_document(db, document)
        await KnowledgeRepository.create_chunks(
            db,
            [
                KnowledgeChunk(
                    organization_id=organization_id,
                    document_id=document.id,
                    chunk_index=0,
                    content=content,
                    token_count=8,
                    metadata_json={"title": title},
                    embedding=embedding,
                )
            ],
        )

    chunk_a_content = "Org A chunk (moderate similarity)"
    await _make_document(
        org_a.id,
        title="A document",
        content=chunk_a_content,
        embedding=a_vector,
    )

    chunk_b_content = "Org B chunk (perfect similarity)"
    await _make_document(
        org_b.id,
        title="B document",
        content=chunk_b_content,
        embedding=query_vector,
    )

    matches = await KnowledgeRepository.semantic_search_for_tenant(
        db,
        embedding=query_vector,
        organization_id=org_a.id,
        limit=1,
    )

    assert len(matches) == 1
    chunk, _distance = matches[0]
    assert chunk.content == chunk_a_content
    assert chunk.content != chunk_b_content


@pytest.mark.asyncio
async def test_e_api_search_top_k_returns_own_chunk_over_closer_foreign(
    db,
    client,
    two_orgs,
    _fake_embedding_returns,
):
    """API-level variant: an Org A request never surfaces an Org B chunk even
    when Org B's chunk is a perfect vector match for the query embedding."""
    org_a = two_orgs["org_a"]
    org_b = two_orgs["org_b"]

    query_vector = _unit_vector(shared_component=1)
    _fake_embedding_returns(query_vector)

    async def _make_document(
        organization_id: int,
        *,
        title: str,
        content: str,
        embedding: list[float],
    ) -> None:
        document = KnowledgeDocument(
            organization_id=organization_id,
            title=title,
            source="direct",
            source_uri=None,
            checksum=uuid.uuid4().hex,
            metadata_json={},
        )
        await KnowledgeRepository.create_document(db, document)
        await KnowledgeRepository.create_chunks(
            db,
            [
                KnowledgeChunk(
                    organization_id=organization_id,
                    document_id=document.id,
                    chunk_index=0,
                    content=content,
                    token_count=8,
                    metadata_json={"title": title},
                    embedding=embedding,
                )
            ],
        )

    await _make_document(
        org_a.id,
        title="A doc",
        content="alpha-modest",
        embedding=_vector_with_query(q_weight=0.6, extra_weight=0.8),
    )
    await _make_document(
        org_b.id,
        title="B doc",
        content="beta-perfect",
        embedding=query_vector,
    )

    response = await client.post(
        "/knowledge/search",
        json={"query": "irrelevant", "limit": 1},
        headers=_auth_headers(USER_ALPHA),
    )

    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert results[0]["content"] == "alpha-modest"


# ------------------------------------------------------------------- RAG


@pytest.mark.asyncio
async def test_f_rag_answer_cites_only_owned_documents(
    db,
    client,
    two_orgs,
    _fake_embedding_returns,
    _fake_rag_llm,
):
    result_a = await two_orgs["ingest"](
        two_orgs["org_a"].id,
        title="Alpha identity policy",
        content="Identity verification requires an approved identity document.",
    )
    await two_orgs["ingest"](
        two_orgs["org_b"].id,
        title="Beta identity policy",
        content="Beta identity verification requires a secret beta document.",
    )

    # Force the query embedding to exactly match Org A's chunk so grounding
    # passes the similarity threshold deterministically offline.
    a_chunks = (
        await db.execute(
            select(KnowledgeChunk).where(
                KnowledgeChunk.document_id == result_a["document_id"]
            )
        )
    ).scalars().all()
    assert a_chunks
    _fake_embedding_returns(a_chunks[0].embedding)

    _fake_rag_llm(
        "Identity verification requires an approved identity document. [S1]"
    )

    response = await client.post(
        "/knowledge/answer",
        json={"question": "What does identity verification require?"},
        headers=_auth_headers(USER_ALPHA),
    )

    assert response.status_code == 200
    body = response.json()
    await two_orgs["track_log"](body["request_id"])

    assert body["grounded"] is True
    assert body["sources"], "expected at least one grounded source"

    beta_document = (
        await db.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.title == "Beta identity policy"
            )
        )
    ).scalar_one()

    source_document_ids = {
        source["document_id"] for source in body["sources"]
    }
    assert source_document_ids == {result_a["document_id"]}
    assert beta_document.id not in source_document_ids


# --------------------------- document/chunk tenant consistency (1C.3B gate)


@pytest.mark.asyncio
async def test_document_chunk_org_mismatch_insert_rejected(
    db,
    two_orgs,
):
    """The database itself rejects a chunk whose organization drifted from its
    document (Option A composite FK).

    Org B owns ``doc_b``. A malformed chunk attempts to attach Org A ownership
    to that Org B document. The composite foreign key
    ``(document_id, organization_id) → knowledge_documents(id, organization_id)``
    makes the insert fail with an IntegrityError — the row can never exist, so
    it can never be ranked as Org A evidence.
    """
    org_a = two_orgs["org_a"]
    org_b = two_orgs["org_b"]

    org_a_id = org_a.id
    org_b_id = org_b.id

    doc_b = KnowledgeDocument(
        organization_id=org_b_id,
        title="Beta private document",
        source="direct",
        source_uri=None,
        checksum=uuid.uuid4().hex,
        metadata_json={},
    )
    await KnowledgeRepository.create_document(db, doc_b)

    doc_b_id = doc_b.id

    malformed = KnowledgeChunk(
        organization_id=org_a_id,
        document_id=doc_b_id,
        chunk_index=0,
        content="Malformed: Org A ownership attached to an Org B document.",
        token_count=10,
        metadata_json={"title": doc_b.title},
        embedding=_deterministic_vector(seed_text="mismatch"),
    )

    with pytest.raises(IntegrityError):
        await KnowledgeRepository.create_chunks(db, [malformed])

    await db.rollback()

    org_a_matches = await KnowledgeRepository.semantic_search_for_tenant(
        db=db,
        embedding=_deterministic_vector(seed_text="mismatch"),
        organization_id=org_a_id,
        limit=5,
    )
    assert all(
        chunk.document_id != doc_b_id for chunk, _ in org_a_matches
    )

    org_b_matches = await KnowledgeRepository.semantic_search_for_tenant(
        db=db,
        embedding=_deterministic_vector(seed_text="mismatch"),
        organization_id=org_b_id,
        limit=5,
    )
    assert all(
        chunk.document_id != doc_b_id for chunk, _ in org_b_matches
    )


@pytest.mark.asyncio
async def test_document_chunk_mismatch_never_becomes_own_rag_evidence(
    db,
    client,
    two_orgs,
    _fake_embeddings,
    _fake_embedding_returns,
    _fake_rag_llm,
):
    """End-to-end replay of the mismatch attack at the API boundary.

    Org A owns a real document. Org B owns Document B. The attack tries to
    attach an Org A-owned chunk to Document B (document_id=doc_b.id,
    organization_id=Org A). Because the composite FK forbids the row, Org A's
    search and RAG pipelines can never surface or cite content pointing at the
    Org B document.
    """
    org_a = two_orgs["org_a"]
    org_b = two_orgs["org_b"]

    org_a_id = org_a.id
    org_b_id = org_b.id

    result_a = await two_orgs["ingest"](
        org_a_id,
        title="Alpha evidence",
        content="Alpha owns this evidence.",
    )

    doc_b = KnowledgeDocument(
        organization_id=org_b_id,
        title="Beta secret document",
        source="direct",
        source_uri=None,
        checksum=uuid.uuid4().hex,
        metadata_json={},
    )
    await KnowledgeRepository.create_document(db, doc_b)

    doc_b_id = doc_b.id

    malformed = KnowledgeChunk(
        organization_id=org_a_id,
        document_id=doc_b_id,
        chunk_index=0,
        content="Malformed: Org A evidence pointing at an Org B document.",
        token_count=10,
        metadata_json={"title": doc_b.title},
        embedding=_deterministic_vector(seed_text="mismatch"),
    )

    with pytest.raises(IntegrityError):
        await KnowledgeRepository.create_chunks(db, [malformed])

    await db.rollback()

    a_chunks = (
        await db.execute(
            select(KnowledgeChunk).where(
                KnowledgeChunk.document_id == result_a["document_id"]
            )
        )
    ).scalars().all()
    assert a_chunks
    _fake_embedding_returns(a_chunks[0].embedding)

    search_response = await client.post(
        "/knowledge/search",
        json={"query": "closest match", "limit": 5},
        headers=_auth_headers(USER_ALPHA),
    )
    assert search_response.status_code == 200
    results = search_response.json()
    assert len(results) >= 1
    assert all(
        item["document_id"] == result_a["document_id"] for item in results
    )
    assert all(
        "Beta secret document" not in item["content"] for item in results
    )

    _fake_rag_llm("Alpha owns this evidence. [S1]")

    answer_response = await client.post(
        "/knowledge/answer",
        json={"question": "Which evidence points at a Beta document?"},
        headers=_auth_headers(USER_ALPHA),
    )
    assert answer_response.status_code == 200
    body = answer_response.json()
    await two_orgs["track_log"](body["request_id"])

    assert body["grounded"] is True
    assert body["sources"]

    source_document_ids = {
        source["document_id"] for source in body["sources"]
    }
    assert source_document_ids == {result_a["document_id"]}
    assert doc_b_id not in source_document_ids
    assert all("Beta secret document" not in source["content"] for source in body["sources"])


# ===================================================================
# Phase 1E.3.1: knowledge corpus summary is tenant-scoped
# ===================================================================


@pytest.mark.asyncio
async def test_summary_endpoint_returns_tenant_scoped_counts(
    client, two_orgs, _fake_embeddings
):
    """GET /knowledge/summary returns counts scoped to the resolved tenant."""
    org_a = two_orgs["org_a"]
    org_b = two_orgs["org_b"]

    await two_orgs["ingest"](
        org_a.id,
        title="Alpha policy",
        content="Alpha organization policy content.",
    )
    await two_orgs["ingest"](
        org_b.id,
        title="Beta policy",
        content="Beta organization policy content.",
    )

    r_alpha = await client.get(
        "/knowledge/summary",
        headers=_auth_headers(USER_ALPHA),
    )
    assert r_alpha.status_code == 200
    summary_alpha = r_alpha.json()
    assert summary_alpha["document_count"] >= 1
    assert summary_alpha["chunk_count"] >= 1

    r_beta = await client.get(
        "/knowledge/summary",
        headers=_auth_headers(USER_BETA),
    )
    assert r_beta.status_code == 200
    summary_beta = r_beta.json()

    # Each tenant only sees their own corpus counts.
    assert summary_alpha["document_count"] == summary_beta["document_count"]
