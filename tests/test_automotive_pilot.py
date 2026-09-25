"""Phase 1M — automotive pilot seeder tests.

The seed script must be idempotent, tenant-explicit (creates ONLY its own
named organization and never touches another tenant), local and fictional
(no external Zendesk ids, no real PII, no fabricated AI/evaluation rows), and
safe to re-run. Knowledge ingestion stays fully offline via stub embeddings.

The A1 pilot org is broadcast-cleaned on entry and exit so a manual seed run or
a previously leaked test run can never falsify an assertion or leak rows.
"""

import hashlib
import math
import os
import uuid
import warnings
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select

os.environ["AUTH_MODE"] = "hs256"
os.environ["AUTH_JWT_SECRET"] = "z" * 32
os.environ["AUTH_JWT_ALGORITHM"] = "HS256"
os.environ["AUTH_JWT_ISSUER"] = "test-a1-issuer"
os.environ["AUTH_JWT_AUDIENCE"] = "test-a1-audience"
os.environ["AUTH_DEV_MODE"] = "False"
os.environ["ENVIRONMENT"] = "development"

warnings.filterwarnings("ignore")

from app.core.config import reset_settings_cache
from app.core.rbac import OrganizationRole
from app.models.agent_run import AgentRun
from app.models.ai_request_log import AIRequestLog
from app.models.conversation import Conversation
from app.models.conversation_message import ConversationMessage
from app.models.customer import Customer
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.knowledge_document import KnowledgeDocument
from app.models.organization import Organization
from app.models.organization_membership import (
    OrganizationMembership,
)
from app.models.service_escalation import ServiceEscalation
from app.models.service_queue import ServiceQueue
from app.models.sla_policy import SLAPolicy
from app.models.ticket import Ticket
from app.services.embedding_service import embedding_service
from scripts.automotive_pilot_data import (
    CUSTOMERS,
    KNOWLEDGE_DOCUMENTS,
    PILOT_ORGANIZATION_NAME,
    QUEUES,
    SCENARIOS,
    SLA_POLICIES,
    TICKET_BY_REF,
    TICKETS,
    load_agent_cases,
    load_rag_cases,
)
from scripts.seed_automotive_pilot import seed

TEST_REFERENCE = datetime(2026, 9, 1, tzinfo=UTC)

_EMBEDDING_DIMS = 1536


def _deterministic_vector(seed_text: str) -> list[float]:
    digest = hashlib.sha256(seed_text.encode("utf-8")).digest()
    state = int.from_bytes(digest[:8], "big")
    values = []
    for _ in range(_EMBEDDING_DIMS):
        state = (state * 1103515245 + 12345) % (2**31)
        values.append(((state / (2**31 - 1)) * 2.0) - 1.0)
    norm = math.sqrt(sum(value * value for value in values))
    return [value / norm for value in values]


_CHILD_TABLES = [
    (ConversationMessage, "organization_id"),
    (Conversation, "organization_id"),
    (ServiceEscalation, "organization_id"),
    (Ticket, "organization_id"),
    (Customer, "organization_id"),
    (ServiceQueue, "organization_id"),
    (SLAPolicy, "organization_id"),
    (KnowledgeChunk, "organization_id"),
    (KnowledgeDocument, "organization_id"),
]


@pytest.fixture
def _fake_embeddings(monkeypatch):
    async def _embed_text(text: str) -> list[float]:
        return _deterministic_vector(text)

    async def _embed_documents(texts: list[str]) -> list[list[float]]:
        return [_deterministic_vector(text) for text in texts]

    monkeypatch.setattr(embedding_service, "embed_text", _embed_text)
    monkeypatch.setattr(embedding_service, "embed_documents", _embed_documents)


@pytest.fixture(autouse=True)
def _configure(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "hs256")
    monkeypatch.setenv("AUTH_JWT_SECRET", "z" * 32)
    monkeypatch.setenv("AUTH_JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("AUTH_JWT_ISSUER", "test-a1-issuer")
    monkeypatch.setenv("AUTH_JWT_AUDIENCE", "test-a1-audience")
    monkeypatch.setenv("AUTH_DEV_MODE", "False")
    monkeypatch.setenv("ENVIRONMENT", "development")
    reset_settings_cache()


async def _purge_org_by_name(db, name: str) -> None:
    result = await db.execute(
        select(Organization.id).where(Organization.name == name)
    )
    leaked_ids = [row[0] for row in result.all()]
    if not leaked_ids:
        return
    for tbl, col in _CHILD_TABLES:
        await db.execute(delete(tbl).where(getattr(tbl, col).in_(leaked_ids)))
    await db.execute(
        delete(OrganizationMembership).where(
            OrganizationMembership.organization_id.in_(leaked_ids)
        )
    )
    await db.execute(delete(Organization).where(Organization.id.in_(leaked_ids)))
    await db.commit()


async def _purge_pilot_org(db) -> None:
    await _purge_org_by_name(db, PILOT_ORGANIZATION_NAME)


async def _mk_decoy(db, name: str) -> Organization:
    decoy = Organization(name=name)
    db.add(decoy)
    await db.flush()
    queue = ServiceQueue(key=f"{uuid.uuid4().hex[:8]}", name="D", organization_id=decoy.id)
    db.add(queue)
    await db.flush()
    db.add(
        OrganizationMembership(
            subject=f"user-{uuid.uuid4().hex[:8]}",
            organization_id=decoy.id,
            role=OrganizationRole.OWNER,
        )
    )
    await db.commit()
    return decoy


@pytest_asyncio.fixture
async def seeded(db):
    await _purge_pilot_org(db)
    summary = await seed(
        db,
        reference=TEST_REFERENCE,
        demo_subject="user-a1-demo",
        with_knowledge=False,
        print_fn=lambda *_: None,
    )
    org = await db.get(Organization, summary["organization_id"])
    assert org is not None
    try:
        yield {"summary": summary, "org": org}
    finally:
        await _purge_pilot_org(db)


async def _row_count(db, model, organization_id: int) -> int:
    result = await db.execute(
        select(func.count()).select_from(model).where(
            model.organization_id == organization_id
        )
    )
    return int(result.scalar_one())


@pytest.mark.asyncio
async def test_seed_creates_full_pilot_tenant(db, seeded):
    org = seeded["org"]
    assert org.name == PILOT_ORGANIZATION_NAME
    assert org.external_id == "a1-automotive-pilot-demo"
    assert org.industry == "Automotive Services"

    summary = seeded["summary"]
    assert summary["created"]["tickets"] == len(TICKETS)

    for model, expected in [
        (SLAPolicy, len(SLA_POLICIES)),
        (ServiceQueue, len(QUEUES)),
        (Customer, len(CUSTOMERS)),
        (Ticket, len(TICKETS)),
        (Conversation, len(TICKETS)),
    ]:
        assert await _row_count(db, model, org.id) == expected, model.__name__

    messages = await db.execute(
        select(func.count()).select_from(ConversationMessage).where(
            ConversationMessage.organization_id == org.id
        )
    )
    assert int(messages.scalar_one()) >= len(TICKETS)

    escalations = await db.execute(
        select(func.count()).select_from(ServiceEscalation).where(
            ServiceEscalation.organization_id == org.id
        )
    )
    assert int(escalations.scalar_one()) > 0


@pytest.mark.asyncio
async def test_seed_is_idempotent(db, seeded):
    first = seeded["summary"]
    before = {
        model.__name__: await _row_count(db, model, first["organization_id"])
        for model in (SLAPolicy, ServiceQueue, Customer, Ticket, Conversation,
                      ConversationMessage, ServiceEscalation)
    }
    second = await seed(
        db,
        reference=TEST_REFERENCE,
        with_knowledge=False,
        print_fn=lambda *_: None,
    )
    assert second["organization_id"] == first["organization_id"]
    for key in ("policies", "queues", "customers", "tickets",
                "conversations", "messages", "escalations"):
        assert second["created"][key] == 0, key
    after = {
        model.__name__: await _row_count(db, model, first["organization_id"])
        for model in (SLAPolicy, ServiceQueue, Customer, Ticket, Conversation,
                      ConversationMessage, ServiceEscalation)
    }
    assert after == before


@pytest.mark.asyncio
async def test_seed_never_touches_another_tenant(db, seeded):
    decoy_name = f"A1 Dealer Corp (real customer) {uuid.uuid4().hex[:6]}"
    decoy = await _mk_decoy(db, decoy_name)
    queue = (
        await db.execute(
            select(ServiceQueue).where(ServiceQueue.organization_id == decoy.id)
        )
    ).scalar_one()
    original_subject = f"real-ticket-{uuid.uuid4().hex[:6]}"
    ticket = Ticket(
        organization_id=decoy.id,
        subject=original_subject,
        description="keep me",
        status="open",
        priority="normal",
        service_queue_id=queue.id,
        external_id="a1-valuations-001",
    )
    db.add(ticket)
    await db.commit()

    await seed(
        db,
        reference=TEST_REFERENCE,
        with_knowledge=False,
        print_fn=lambda *_: None,
    )

    decoy_tickets = (
        await db.execute(
            select(Ticket).where(Ticket.organization_id == decoy.id)
        )
    ).scalars().all()
    assert len(decoy_tickets) == 1
    assert decoy_tickets[0].subject == original_subject
    assert decoy_tickets[0].status == "open"
    assert decoy_tickets[0].external_id == "a1-valuations-001"

    pilot_count = (
        await db.execute(
            select(func.count()).select_from(Organization).where(
                Organization.name == PILOT_ORGANIZATION_NAME
            )
        )
    ).scalar_one()
    assert pilot_count == 1

    try:
        await _purge_org_by_name(db, decoy_name)
    finally:
        await _purge_pilot_org(db)


@pytest.mark.asyncio
async def test_seed_rows_are_local_and_fictional(db, seeded):
    org_id = seeded["org"].id

    tickets = (
        await db.execute(select(Ticket).where(Ticket.organization_id == org_id))
    ).scalars().all()
    assert len(tickets) == len(TICKETS)
    for ticket in tickets:
        assert ticket.external_id.startswith("a1-"), ticket.external_id

    customers = (
        await db.execute(select(Customer).where(Customer.organization_id == org_id))
    ).scalars().all()
    for customer in customers:
        assert customer.email.endswith("@example.com"), customer.email

    conversations = (
        await db.execute(
            select(Conversation).where(Conversation.organization_id == org_id)
        )
    ).scalars().all()
    assert len(conversations) == len(TICKETS)
    valid_channels = {"web", "email", "whatsapp", "chat", "ticket"}
    for conversation in conversations:
        assert conversation.provider == "cxops", conversation.provider
        assert conversation.external_thread_id is None
        assert conversation.channel in valid_channels, conversation.channel

    zendesk_count = (
        await db.execute(
            select(func.count()).select_from(Conversation).where(
                Conversation.organization_id == org_id,
                Conversation.provider == "zendesk",
            )
        )
    ).scalar_one()
    assert zendesk_count == 0


@pytest.mark.asyncio
async def test_no_fabricated_ai_or_eval_rows(db, seeded):
    org_id = seeded["org"].id
    assert await _row_count(db, AgentRun, org_id) == 0
    assert await _row_count(db, AIRequestLog, org_id) == 0


@pytest.mark.asyncio
async def test_seed_windows_channels_and_escalation_bounds(db, seeded):
    org_id = seeded["org"].id
    reference = datetime.fromisoformat(seeded["summary"]["reference"])
    window_start = reference - timedelta(days=60)
    window_end = reference + timedelta(minutes=1)

    tickets = (
        await db.execute(select(Ticket).where(Ticket.organization_id == org_id))
    ).scalars().all()
    assert len(tickets) == len(TICKETS)

    valid_channels = {"web", "email", "whatsapp", "chat", "ticket"}
    for ticket in tickets:
        assert window_start <= ticket.created_at <= window_end, ticket.external_id
        assert ticket.first_response_due_at is not None, ticket.external_id
        assert ticket.resolution_due_at is not None, ticket.external_id
        if ticket.resolved_at is not None:
            assert ticket.resolved_at <= reference, ticket.external_id

    conversations = (
        await db.execute(
            select(Conversation).where(Conversation.organization_id == org_id)
        )
    ).scalars().all()
    assert len(conversations) == len(TICKETS)
    for conversation in conversations:
        assert conversation.channel in valid_channels, conversation.channel

    escalations = (
        await db.execute(
            select(ServiceEscalation).where(
                ServiceEscalation.organization_id == org_id
            )
        )
    ).scalars().all()
    assert escalations, "expected seeded escalations"
    for escalation in escalations:
        assert escalation.triggered_at <= reference, escalation.event_key
        if escalation.resolved_at is not None:
            assert escalation.resolved_at <= reference, escalation.event_key


@pytest.mark.asyncio
async def test_knowledge_ingestion_offline(db, seeded, _fake_embeddings):
    first = seeded["summary"]
    second = await seed(
        db,
        reference=TEST_REFERENCE,
        with_knowledge=True,
        print_fn=lambda *_: None,
    )
    assert second["organization_id"] == first["organization_id"]
    assert second["knowledge"]["ingested"] == len(KNOWLEDGE_DOCUMENTS)
    assert second["knowledge"]["skipped"] is False

    docs = await db.execute(
        select(func.count()).select_from(KnowledgeDocument).where(
            KnowledgeDocument.organization_id == first["organization_id"]
        )
    )
    assert int(docs.scalar_one()) == len(KNOWLEDGE_DOCUMENTS)

    chunks = await db.execute(
        select(func.count()).select_from(KnowledgeChunk).where(
            KnowledgeChunk.organization_id == first["organization_id"]
        )
    )
    assert int(chunks.scalar_one()) >= len(KNOWLEDGE_DOCUMENTS)


@pytest.mark.asyncio
async def test_scenario_and_eval_datasets_map_to_seeded_rows(db, seeded):
    org_id = seeded["org"].id
    tickets = (
        await db.execute(select(Ticket).where(Ticket.organization_id == org_id))
    ).scalars().all()
    ids_by_ref = {
        t.external_id.split("a1-", 1)[1]: t for t in tickets
    }
    assert set(ids_by_ref) == set(TICKET_BY_REF)

    for scenario in SCENARIOS:
        assert scenario["ticket"] in ids_by_ref, scenario["id"]

    risk_refs = {
        scenario["ticket"]
        for scenario in SCENARIOS
        if scenario.get("risk") is True
    }
    assert risk_refs, "expected at least one risk-flagged scenario"

    for case in load_agent_cases():
        assert case["ticket_ref"] in ids_by_ref, case["name"]

    for case in load_rag_cases():
        if case["should_refuse"]:
            assert case["expected_sources"] == [], case["id"]


@pytest.mark.asyncio
async def test_optional_replay_is_tenant_bound(db, seeded):
    from scripts.run_automotive_pilot import _pilot_org_id, main

    org_id = seeded["org"].id
    decoy_name = f"Second Auto Dealer (demo) {uuid.uuid4().hex[:6]}"
    decoy = await _mk_decoy(db, decoy_name)
    queue = (
        await db.execute(
            select(ServiceQueue).where(ServiceQueue.organization_id == decoy.id)
        )
    ).scalar_one()
    db.add(
        Ticket(
            organization_id=decoy.id,
            subject="decoy",
            description="decoy ticket",
            status="open",
            priority="normal",
            service_queue_id=queue.id,
            external_id="a1-valuations-001",
        )
    )
    await db.commit()

    resolved_pilot_id = await _pilot_org_id(db)
    assert resolved_pilot_id == org_id

    results = await main(
        ticket_refs=["valuations-001"],
        allow_auto_queue=False,
        organization_id=None,
        dry_run=True,
    )
    assert results
    assert all(r["dry_run"] for r in results)
    assert all(r["external_id"] == "a1-valuations-001" for r in results)

    decoy_tickets = (
        await db.execute(
            select(Ticket).where(Ticket.organization_id == decoy.id)
        )
    ).scalars().all()
    assert len(decoy_tickets) == 1
    assert decoy_tickets[0].status == "open"

    try:
        await _purge_org_by_name(db, decoy_name)
    finally:
        await _purge_pilot_org(db)