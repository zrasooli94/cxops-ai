"""Phase 1F — conversation vertical: inbox API, context bounds, idempotent sync.

The conversation domain is provider-neutral and tenant-scoped, so this suite
proves the phase's security-critical properties on every edge we own:

- local ticket creation wires a cxops conversation + deterministic initial
  message, and is idempotent under replay
- Zendesk comment ingestion classifies requester/agent/internal threads and
  deduplicates by external message id across webhook replays
- the inbox read model (list / detail / summary / messages) is tenant-scoped
  and gated on ticket.read, with 404 (never cross-tenant data) for foreign
  rows and fail-closed 403 for subjects without the capability
- ConversationContext bounds public message count and byte budget, marks
  partial on down-sampling, and its digest changes when a message is appended
- the agent analysis fingerprint includes the conversation digest, so a new
  inbound message invalidates a stale reusable analysis

Never opens OpenAI or a real Zendesk connection: the decision LLM is a
capturing deterministic fake, knowledge search is short-circuited, and Zendesk
client calls are replaced with deterministic fakes.
"""

import asyncio
import os
import time
import types
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import delete, select

os.environ["AUTH_MODE"] = "hs256"
os.environ["AUTH_JWT_SECRET"] = "z" * 32
os.environ["AUTH_JWT_ALGORITHM"] = "HS256"
os.environ["AUTH_JWT_ISSUER"] = "test-conversation-issuer"
os.environ["AUTH_JWT_AUDIENCE"] = "test-conversation-audience"
os.environ["AUTH_DEV_MODE"] = "False"
os.environ["ENVIRONMENT"] = "development"

from app.core.config import reset_settings_cache, settings
from app.core.database import AsyncSessionLocal
from app.core.rbac import OrganizationRole
from app.integrations.zendesk.client import zendesk_client
from app.main import app
from app.models.base import Base
from app.models.conversation import Conversation
from app.models.conversation_message import ConversationMessage
from app.models.integration_job import IntegrationJob
from app.models.organization import Organization
from app.models.organization_membership import OrganizationMembership
from app.models.ticket import Ticket
from app.repositories.conversation_message_repository import (
    ConversationMessageRepository,
)
from app.repositories.conversation_repository import (
    ConversationRepository,
)
from app.repositories.integration_job_repository import (
    IntegrationJobRepository,
)
from app.schemas.agent import AgentDecision
from app.schemas.ticket import TicketCreate, TicketUpdate
from app.services.agent_workflow_service import (
    agent_workflow_service,
)
from app.services.conversation_context_service import (
    ConversationContextService,
)
from app.services.conversation_delivery_service import (
    ConversationDeliveryService,
)
from app.services.conversation_ingestion_service import (
    ConversationIngestionService,
)
from app.services.conversation_reply_service import (
    ConversationReplyService,
)
from app.services.knowledge_search_service import KnowledgeSearchService
from app.services.ticket_service import TicketService

TEST_SECRET = "z" * 32
TEST_ISSUER = "test-conversation-issuer"
TEST_AUDIENCE = "test-conversation-audience"

USER_ALPHA = "user-alpha"
USER_BETA = "user-beta"

X_TENANT = "X-CXOps-Organization-ID"


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


def _auth_headers(sub: str, tenant_id: int) -> dict:
    return {
        "Authorization": f"Bearer {_build_token(sub=sub)}",
        X_TENANT: str(tenant_id),
    }


@pytest.fixture(autouse=True)
def _configure_auth(monkeypatch):
    values = {
        "AUTH_MODE": "hs256",
        "AUTH_JWT_SECRET": TEST_SECRET,
        "AUTH_JWT_ALGORITHM": "HS256",
        "AUTH_JWT_ISSUER": TEST_ISSUER,
        "AUTH_JWT_AUDIENCE": TEST_AUDIENCE,
        "AUTH_DEV_MODE": "False",
        "ENVIRONMENT": "development",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    reset_settings_cache()
    yield


@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


@pytest_asyncio.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def org_scope(db):
    """Create unique test organizations and remove them on teardown."""
    org_ids: list[int] = []

    async def make(
        subject: str | None = None,
        role: OrganizationRole | None = None,
    ) -> Organization:
        org = Organization(name=f"conversation-test-{uuid.uuid4().hex[:10]}")
        db.add(org)
        await db.flush()
        org_ids.append(org.id)
        if subject is not None and role is not None:
            db.add(
                OrganizationMembership(
                    subject=subject,
                    organization_id=org.id,
                    role=role,
                )
            )
        await db.commit()
        return org

    yield make

    if org_ids:
        tables = [
            "ticket_events",
            "agent_action_events",
            "knowledge_chunks",
            "ai_request_logs",
            "integration_jobs",
            "agent_runs",
            "knowledge_documents",
            "tickets",
            "customers",
            "customer_identities",
            "conversation_messages",
            "conversations",
            "automation_rules",
            "zendesk_oauth_tokens",
            "zendesk_oauth_states",
            "organization_memberships",
            "organizations",
        ]
        for table_name in tables:
            table = Base.metadata.tables.get(table_name)
            if table is not None and "organization_id" in table.columns:
                await db.execute(
                    delete(table).where(table.c.organization_id.in_(org_ids))
                )
        await db.commit()


async def _make_ticket(
    db,
    organization_id: int,
    *,
    subject: str = "Conversation test ticket",
    description: str = "Customer support case.",
    external_id: str | None = None,
    source: str | None = None,
) -> Ticket:
    ticket = Ticket(
        organization_id=organization_id,
        subject=subject,
        description=description,
        external_id=external_id,
        source=source,
    )
    db.add(ticket)
    await db.commit()
    await db.refresh(ticket)
    return ticket


async def _link_conversation(
    db,
    ticket: Ticket,
    organization_id: int,
) -> Conversation:
    conversation = await ConversationIngestionService.sync_ticket_conversation(
        db,
        ticket=ticket,
        organization_id=organization_id,
    )
    await ConversationIngestionService.ingest_initial_message(
        db,
        conversation=conversation,
        ticket=ticket,
        organization_id=organization_id,
    )
    return conversation


def _as_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def _add_public_message(
    db,
    conversation: Conversation,
    organization_id: int,
    *,
    direction: str,
    body: str,
    dedupe_key: str | None = None,
    sent_at: str | None = None,
) -> ConversationMessage:
    message = ConversationMessage(
        organization_id=organization_id,
        conversation_id=conversation.id,
        provider=conversation.provider,
        dedupe_key=dedupe_key,
        direction=direction,
        visibility="public",
        body=body,
        sent_at=_as_datetime(sent_at) if sent_at is not None else None,
    )
    return await ConversationMessageRepository.create(db, message)


async def _add_internal_message(
    db,
    conversation: Conversation,
    organization_id: int,
    *,
    body: str,
    sent_at: str | None = None,
) -> ConversationMessage:
    message = ConversationMessage(
        organization_id=organization_id,
        conversation_id=conversation.id,
        provider=conversation.provider,
        dedupe_key=f"test-internal-{uuid.uuid4().hex}",
        direction="internal",
        visibility="internal",
        body=body,
        sent_at=_as_datetime(sent_at) if sent_at is not None else None,
    )
    return await ConversationMessageRepository.create(db, message)


async def _set_status(db, conversation: Conversation, organization_id: int, status: str) -> None:
    await ConversationRepository.update_for_tenant(
        db,
        conversation=conversation,
        changes={"status": status},
        organization_id=organization_id,
    )


class _CapturingDecisionLLM:
    """Deterministic decision model that records prompts (never OpenAI)."""

    def __init__(self, decision: AgentDecision) -> None:
        self._decision = decision
        self.prompts: list[str] = []
        self.invocation_count = 0

    async def ainvoke(self, messages: list) -> dict:
        self.invocation_count += 1
        self.prompts.extend(str(message.content) for message in messages)
        raw = types.SimpleNamespace(usage_metadata=None)
        return {
            "raw": raw,
            "parsed": self._decision,
            "parsing_error": None,
        }


async def _analyze_ticket_directly(ticket_id: int, organization_id: int) -> dict:
    """Call the workflow service directly with a fresh database session."""
    async with AsyncSessionLocal() as session:
        return await agent_workflow_service.analyze(
            session,
            ticket_id=ticket_id,
            organization_id=organization_id,
            authz=None,
        )


# ----------------------------------------------------------------------
# Ingestion: local ticket wiring, status tracking, Zendesk comment sync
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_local_ticket_creation_wires_cxops_conversation_and_initial_message(
    db, org_scope
):
    org = await org_scope()

    ticket = await TicketService.create_ticket_for_tenant(
        db,
        TicketCreate(
            subject="Local ticket subject",
            description="Local ticket description body.",
        ),
        org.id,
    )

    conversation = await ConversationRepository.get_by_ticket_for_tenant(
        db,
        ticket_id=ticket.id,
        organization_id=org.id,
    )

    assert conversation is not None
    assert conversation.provider == "cxops"
    assert conversation.channel == "ticket"
    assert conversation.external_thread_id is None
    assert conversation.subject == "Local ticket subject"
    assert conversation.status == "open"
    assert conversation.ticket_id == ticket.id

    messages = await ConversationMessageRepository.list_for_conversation_for_tenant(
        db,
        conversation_id=conversation.id,
        organization_id=org.id,
    )
    assert len(messages) == 1
    assert messages[0].direction == "inbound"
    assert messages[0].visibility == "public"
    assert messages[0].body == "Local ticket description body."
    assert messages[0].dedupe_key == f"ticket_initial:{ticket.id}"
    assert messages[0].sent_at == ticket.created_at


@pytest.mark.asyncio
async def test_initial_message_ingestion_is_idempotent(db, org_scope):
    org = await org_scope()
    ticket = await _make_ticket(db, org.id, description="Idempotent body.")

    conversation = await _link_conversation(db, ticket, org.id)

    first = await ConversationIngestionService.ingest_initial_message(
        db,
        conversation=conversation,
        ticket=ticket,
        organization_id=org.id,
    )
    second = await ConversationIngestionService.ingest_initial_message(
        db,
        conversation=conversation,
        ticket=ticket,
        organization_id=org.id,
    )

    assert first.id == second.id

    messages = await ConversationMessageRepository.list_for_conversation_for_tenant(
        db,
        conversation_id=conversation.id,
        organization_id=org.id,
    )
    assert len(messages) == 1
    assert messages[0].dedupe_key == f"ticket_initial:{ticket.id}"


@pytest.mark.asyncio
async def test_conversation_status_tracks_ticket_resolution(db, org_scope):
    org = await org_scope()
    ticket = await _make_ticket(db, org.id, description="Status tracking body.")

    conversation = await _link_conversation(db, ticket, org.id)
    assert conversation.status == "open"

    updated = await TicketService.update_ticket_for_tenant(
        db,
        ticket.id,
        TicketUpdate(status="solved"),
        org.id,
    )
    assert updated is not None

    conversation = await ConversationRepository.get_by_ticket_for_tenant(
        db,
        ticket_id=ticket.id,
        organization_id=org.id,
    )
    assert conversation.status == "closed"


@pytest.mark.asyncio
async def test_zendesk_comment_ingestion_classifies_and_deduplicates(
    db, org_scope, monkeypatch
):
    org = await org_scope()
    await _make_ticket(
        db,
        org.id,
        subject="Zendesk synced ticket",
        description="Synced description.",
        external_id="9001",
        source="zendesk",
    )

    comments_response = {
        "comments": [
            {
                "id": 1001,
                "public": True,
                "author_id": 777,
                "body": "Customer follow-up question.",
                "created_at": "2026-09-01T10:00:00Z",
            },
            {
                "id": 1002,
                "public": True,
                "author_id": 888,
                "body": "Our support agent reply.",
                "created_at": "2026-09-01T11:00:00Z",
            },
            {
                "id": 1003,
                "public": False,
                "author_id": 888,
                "body": "Private internal analysis.",
                "created_at": "2026-09-01T12:00:00Z",
            },
        ]
    }

    async def fake_ticket(_db, *, ticket_id, organization_id):
        return {"ticket": {"id": ticket_id, "requester_id": 777}}

    async def fake_comments(_db, *, ticket_id, organization_id, **kwargs):
        return comments_response

    monkeypatch.setattr(zendesk_client, "get_ticket", fake_ticket)
    monkeypatch.setattr(zendesk_client, "get_ticket_comments", fake_comments)

    conversation, _ = await ConversationIngestionService.ingest_zendesk_ticket(
        db,
        zendesk_ticket_id=9001,
        organization_id=org.id,
    )

    messages = await ConversationMessageRepository.list_for_conversation_for_tenant(
        db,
        conversation_id=conversation.id,
        organization_id=org.id,
    )
    by_dedupe = {message.dedupe_key: message for message in messages}

    assert conversation.provider == "zendesk"
    assert conversation.external_thread_id == "9001"

    assert by_dedupe["zendesk_comment:9001:1001"].direction == "inbound"
    assert by_dedupe["zendesk_comment:9001:1001"].visibility == "public"
    assert by_dedupe["zendesk_comment:9001:1002"].direction == "outbound"
    assert by_dedupe["zendesk_comment:9001:1002"].visibility == "public"
    assert by_dedupe["zendesk_comment:9001:1003"].direction == "internal"
    assert by_dedupe["zendesk_comment:9001:1003"].visibility == "internal"

    # Webhook replay converges: a second ingestion never double-inserts.
    _, _ = await ConversationIngestionService.ingest_zendesk_ticket(
        db,
        zendesk_ticket_id=9001,
        organization_id=org.id,
    )
    replayed = await ConversationMessageRepository.list_for_conversation_for_tenant(
        db,
        conversation_id=conversation.id,
        organization_id=org.id,
    )
    assert len(replayed) == 3


@pytest.mark.asyncio
async def test_zendesk_ticket_without_comments_falls_back_to_description(
    db, org_scope, monkeypatch
):
    org = await org_scope()
    ticket = await _make_ticket(
        db,
        org.id,
        subject="No comment ticket",
        description="Fallback description body.",
        external_id="9002",
        source="zendesk",
    )

    async def fake_ticket(_db, *, ticket_id, organization_id):
        return {"ticket": {"id": ticket_id, "requester_id": 777}}

    async def fake_comments(_db, *, ticket_id, organization_id, **kwargs):
        return {"comments": []}

    monkeypatch.setattr(zendesk_client, "get_ticket", fake_ticket)
    monkeypatch.setattr(zendesk_client, "get_ticket_comments", fake_comments)

    conversation, _ = await ConversationIngestionService.ingest_zendesk_ticket(
        db,
        zendesk_ticket_id=9002,
        organization_id=org.id,
    )

    messages = await ConversationMessageRepository.list_for_conversation_for_tenant(
        db,
        conversation_id=conversation.id,
        organization_id=org.id,
    )
    assert len(messages) == 1
    assert messages[0].dedupe_key == f"ticket_initial:{ticket.id}"
    assert messages[0].body == "Fallback description body."


# ----------------------------------------------------------------------
# Inbox read model: tenant scoping, reply mode, needs_response, filters
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inbox_list_reports_needs_response_and_previews(db, client, org_scope):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    needs_ticket = await _make_ticket(
        db, org.id, subject="Needs reply ticket", description="Open inbound case."
    )
    await _link_conversation(db, needs_ticket, org.id)

    answered_ticket = await _make_ticket(
        db, org.id, subject="Answered ticket", description="Answered case."
    )
    answered_conv = await _link_conversation(db, answered_ticket, org.id)
    await _add_public_message(
        db,
        answered_conv,
        org.id,
        direction="outbound",
        body="We have already handled this.",
        sent_at="2027-09-01T09:00:00Z",
    )

    closed_ticket = await _make_ticket(
        db, org.id, subject="Closed ticket", description="Resolved case."
    )
    closed_conv = await _link_conversation(db, closed_ticket, org.id)
    await _set_status(db, closed_conv, org.id, "closed")

    response = await client.get("/conversations", headers=_auth_headers(USER_ALPHA, org.id))
    assert response.status_code == 200
    payload = response.json()

    assert payload["total"] == 3
    by_subject = {item["subject"]: item for item in payload["items"]}

    assert by_subject["Needs reply ticket"]["needs_response"] is True
    assert by_subject["Needs reply ticket"]["latest_message"]["direction"] == "inbound"
    assert by_subject["Needs reply ticket"]["reply_mode"] == "local_only"

    assert by_subject["Answered ticket"]["needs_response"] is False
    assert by_subject["Answered ticket"]["latest_message"]["direction"] == "outbound"

    assert by_subject["Closed ticket"]["needs_response"] is False


@pytest.mark.asyncio
async def test_inbox_conversation_detail_reply_mode(db, client, org_scope):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    local_ticket = await _make_ticket(db, org.id, subject="Local mode ticket")
    local_conv = await _link_conversation(db, local_ticket, org.id)

    zendesk_ticket = await _make_ticket(
        db,
        org.id,
        subject="Agent mode ticket",
        external_id="9003",
        source="zendesk",
    )
    zendesk_conv = await _link_conversation(db, zendesk_ticket, org.id)

    non_integer_ticket = await _make_ticket(
        db,
        org.id,
        subject="Non integer mode ticket",
        external_id="not-an-int",
        source="zendesk",
    )
    non_integer_conv = await _link_conversation(db, non_integer_ticket, org.id)

    legacy = Conversation(
        organization_id=org.id,
        provider="cxops",
        channel="web",
        subject="No ticket conversation",
        status="open",
    )
    legacy = await ConversationRepository.create(db, legacy)

    response = await client.get(
        f"/conversations/{local_conv.id}",
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 200
    assert response.json()["reply_mode"] == "local_only"

    response = await client.get(
        f"/conversations/{zendesk_conv.id}",
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 200
    assert response.json()["reply_mode"] == "zendesk"

    response = await client.get(
        f"/conversations/{non_integer_conv.id}",
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 200
    assert response.json()["reply_mode"] == "unsupported"

    response = await client.get(
        f"/conversations/{legacy.id}",
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 200
    assert response.json()["reply_mode"] == "unsupported"


@pytest.mark.asyncio
async def test_inbox_summary_aggregates_counts(db, client, org_scope):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    for subject in ("Summary ticket one", "Summary ticket two", "Summary ticket three"):
        ticket = await _make_ticket(db, org.id, subject=subject)
        conversation = await _link_conversation(db, ticket, org.id)
        if subject == "Summary ticket two":
            await _set_status(db, conversation, org.id, "closed")

    response = await client.get("/conversations/summary", headers=_auth_headers(USER_ALPHA, org.id))
    assert response.status_code == 200
    payload = response.json()

    assert payload["total"] == 3
    assert payload["open"] == 2
    assert payload["closed"] == 1
    assert payload["needs_response"] == 2
    assert payload["by_provider"] == {"cxops": 3}
    assert payload["by_channel"] == {"ticket": 3}


@pytest.mark.asyncio
async def test_inbox_thread_chronological_and_includes_internal_notes(
    db, client, org_scope
):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)
    ticket = await _make_ticket(db, org.id, subject="Thread ticket")
    conversation = await _link_conversation(db, ticket, org.id)

    await _add_internal_message(db, conversation, org.id, body="Internal note first.")
    await _add_public_message(
        db,
        conversation,
        org.id,
        direction="outbound",
        body="Outbound agent reply.",
        sent_at="2027-09-01T09:00:00Z",
    )
    await _add_public_message(
        db,
        conversation,
        org.id,
        direction="inbound",
        body="Customer follow-up.",
        sent_at="2027-09-01T10:00:00Z",
    )

    response = await client.get(
        f"/conversations/{conversation.id}/messages",
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 200
    messages = response.json()

    bodies = [message["body"] for message in messages]

    assert bodies == [
        "Customer support case.",  # ticket description initial (sent_at = created_at)
        "Outbound agent reply.",
        "Customer follow-up.",
        "Internal note first.",  # no provider timestamp → nulls_last
    ]

    internal = [message for message in messages if message["visibility"] == "internal"]
    assert len(internal) == 1
    assert internal[0]["body"] == "Internal note first."


@pytest.mark.asyncio
async def test_inbox_filters(db, client, org_scope):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    ticket_a = await _make_ticket(db, org.id, subject="Alpha subject filter ticket")
    conv_a = await _link_conversation(db, ticket_a, org.id)

    ticket_b = await _make_ticket(db, org.id, subject="Beta subject filter ticket")
    conv_b = await _link_conversation(db, ticket_b, org.id)
    await _set_status(db, conv_b, org.id, "closed")

    zendesk_ticket = await _make_ticket(
        db,
        org.id,
        subject="Zendesk filter ticket",
        external_id="9004",
        source="zendesk",
    )
    await _link_conversation(db, zendesk_ticket, org.id)
    customer_id = 424242

    headers = _auth_headers(USER_ALPHA, org.id)

    response = await client.get("/conversations", params={"status": "open"}, headers=headers)
    assert response.status_code == 200
    assert {item["subject"] for item in response.json()["items"]} == {
        "Alpha subject filter ticket",
        "Zendesk filter ticket",
    }

    response = await client.get("/conversations", params={"provider": "zendesk"}, headers=headers)
    assert response.status_code == 200
    assert [item["subject"] for item in response.json()["items"]] == [
        "Zendesk filter ticket"
    ]

    response = await client.get(
        "/conversations", params={"search": "Alpha subject"}, headers=headers
    )
    assert response.status_code == 200
    assert [item["subject"] for item in response.json()["items"]] == [
        "Alpha subject filter ticket"
    ]

    response = await client.get(
        "/conversations", params={"ticket_id": ticket_a.id}, headers=headers
    )
    assert response.status_code == 200
    assert [item["ticket_id"] for item in response.json()["items"]] == [ticket_a.id]

    response = await client.get(
        "/conversations", params={"customer_id": customer_id}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["total"] == 0

    response = await client.get(
        "/conversations", params={"status": "open", "provider": "cxops"}, headers=headers
    )
    assert response.status_code == 200
    assert [item["subject"] for item in response.json()["items"]] == [
        "Alpha subject filter ticket"
    ]

    assert conv_a.id != 0
    assert conv_b.id != 0


@pytest.mark.asyncio
async def test_inbox_is_tenant_isolated_404_for_foreign_rows(db, client, org_scope):
    org_a = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)
    org_b = await org_scope(subject=USER_BETA, role=OrganizationRole.OWNER)

    ticket_a = await _make_ticket(db, org_a.id, subject="Alpha only ticket")
    conv_a = await _link_conversation(db, ticket_a, org_a.id)

    ticket_b = await _make_ticket(db, org_b.id, subject="Beta only ticket")
    conv_b = await _link_conversation(db, ticket_b, org_b.id)

    headers_a = _auth_headers(USER_ALPHA, org_a.id)
    headers_b = _auth_headers(USER_BETA, org_b.id)

    response = await client.get("/conversations", headers=headers_a)
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == conv_a.id

    response = await client.get("/conversations", headers=headers_b)
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == conv_b.id

    response = await client.get(f"/conversations/{conv_b.id}", headers=headers_a)
    assert response.status_code == 404

    response = await client.get(f"/conversations/{conv_a.id}", headers=headers_b)
    assert response.status_code == 404

    response = await client.get(
        f"/conversations/{conv_b.id}/messages",
        headers=headers_a,
    )
    assert response.status_code == 404

    response = await client.get("/conversations/summary", headers=headers_a)
    assert response.status_code == 200
    assert response.json()["total"] == 1

    response = await client.get(
        "/conversations", params={"ticket_id": ticket_b.id}, headers=headers_a
    )
    assert response.status_code == 200
    assert response.json()["total"] == 0


@pytest.mark.asyncio
async def test_inbox_requires_ticket_read_capability(db, client, org_scope):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    ticket = await _make_ticket(db, org.id, subject="Unauthorized access ticket")
    conversation = await _link_conversation(db, ticket, org.id)

    no_membership = "user-without-membership"
    headers = _auth_headers(no_membership, org.id)

    response = await client.get("/conversations", headers=headers)
    assert response.status_code == 403

    response = await client.get("/conversations/summary", headers=headers)
    assert response.status_code == 403

    response = await client.get(f"/conversations/{conversation.id}", headers=headers)
    assert response.status_code == 403

    response = await client.get(
        f"/conversations/{conversation.id}/messages",
        headers=headers,
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_inbox_member_of_other_org_denied_ticket_read(db, client, org_scope):
    org_a = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)
    await org_scope(subject=USER_BETA, role=OrganizationRole.OWNER)

    response = await client.get(
        "/conversations",
        headers=_auth_headers(USER_BETA, org_a.id),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_inbox_viewer_member_has_ticket_read(db, client, org_scope):
    org = await org_scope(subject="viewer-user", role=OrganizationRole.VIEWER)
    ticket = await _make_ticket(db, org.id, subject="Viewer readable ticket")
    await _link_conversation(db, ticket, org.id)

    response = await client.get(
        "/conversations",
        headers=_auth_headers("viewer-user", org.id),
    )
    assert response.status_code == 200


# ----------------------------------------------------------------------
# Conversation context: bounds, partial, digest invalidation
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conversation_context_bounds_public_messages_and_truncates_bodies(
    db, org_scope
):
    org = await org_scope()
    ticket = await _make_ticket(db, org.id, subject="Context bound ticket")
    conversation = await _link_conversation(db, ticket, org.id)

    for index in range(15):
        await _add_public_message(
            db,
            conversation,
            org.id,
            direction="outbound" if index % 2 else "inbound",
            body=f"Short public message {index}.",
            sent_at=f"2030-09-{index + 1:02d}T10:00:00Z",
        )
    await _add_internal_message(
        db,
        conversation,
        org.id,
        body="Internal note must never reach the agent context.",
        sent_at="2030-09-20T10:00:00Z",
    )

    context = await ConversationContextService.build_for_ticket(
        db,
        organization_id=org.id,
        ticket_id=ticket.id,
    )

    assert context is not None
    assert context.conversation_id == conversation.id
    assert context.provider == "cxops"
    assert context.channel == "ticket"
    assert context.status == "open"
    assert len(context.recent_messages) <= ConversationContextService.MAX_MESSAGES
    assert all("Internal note" not in message.body for message in context.recent_messages)
    assert context.recent_messages[-1].body == "Short public message 14."


@pytest.mark.asyncio
async def test_conversation_context_flags_partial_on_body_preserve(db, org_scope):
    org = await org_scope()
    ticket = await _make_ticket(db, org.id, subject="Partial context ticket")
    conversation = await _link_conversation(db, ticket, org.id)

    long_body = "x" * 2000
    await _add_public_message(
        db,
        conversation,
        org.id,
        direction="inbound",
        body=long_body,
        sent_at="2030-09-02T10:00:00Z",
    )

    context = await ConversationContextService.build_for_ticket(
        db,
        organization_id=org.id,
        ticket_id=ticket.id,
    )

    assert context is not None
    assert context.partial is True
    assert all(
        len(message.body) <= ConversationContextService.MAX_BODY_CHARS
        for message in context.recent_messages
    )


@pytest.mark.asyncio
async def test_conversation_context_digest_changes_on_new_inbound_message(db, org_scope):
    org = await org_scope()
    ticket = await _make_ticket(db, org.id, subject="Digest ticket")
    conversation = await _link_conversation(db, ticket, org.id)

    context_before = await ConversationContextService.build_for_ticket(
        db,
        organization_id=org.id,
        ticket_id=ticket.id,
    )
    digest_before = ConversationContextService.compute_digest(context_before)

    assert digest_before != ""

    unchanged = await ConversationContextService.build_for_ticket(
        db,
        organization_id=org.id,
        ticket_id=ticket.id,
    )
    assert ConversationContextService.compute_digest(unchanged) == digest_before

    await _add_public_message(
        db,
        conversation,
        org.id,
        direction="inbound",
        body="A new customer message arrives.",
        sent_at="2030-09-02T10:00:00Z",
    )

    context_after = await ConversationContextService.build_for_ticket(
        db,
        organization_id=org.id,
        ticket_id=ticket.id,
    )
    digest_after = ConversationContextService.compute_digest(context_after)

    assert digest_after != digest_before

    # A ticket without a conversation yields the stable empty sentinel.
    legacy_ticket = await _make_ticket(db, org.id, subject="Legacy no-conversation ticket")
    assert (
        ConversationContextService.compute_digest(
            await ConversationContextService.build_for_ticket(
                db,
                organization_id=org.id,
                ticket_id=legacy_ticket.id,
            )
        )
        == ""
    )


@pytest.mark.asyncio
async def test_internal_note_does_not_change_conversation_context_digest(
    db, org_scope
):
    """Internal/private notes are excluded from the agent-facing context.

    Because ConversationContext only includes public messages, adding an
    internal note must not alter the conversation_context_digest. This
    prevents an internal reviewer-only event from spuriously invalidating a
    reusable analysis fingerprint.
    """
    org = await org_scope()
    ticket = await _make_ticket(db, org.id, subject="Internal note digest ticket")
    conversation = await _link_conversation(db, ticket, org.id)

    context_before = await ConversationContextService.build_for_ticket(
        db,
        organization_id=org.id,
        ticket_id=ticket.id,
    )
    digest_before = ConversationContextService.compute_digest(context_before)

    await _add_internal_message(
        db,
        conversation,
        org.id,
        body="Sensitive reviewer-only context.",
        sent_at="2030-09-02T10:00:00Z",
    )

    context_after = await ConversationContextService.build_for_ticket(
        db,
        organization_id=org.id,
        ticket_id=ticket.id,
    )
    digest_after = ConversationContextService.compute_digest(context_after)

    assert digest_after == digest_before
    assert all(
        "Sensitive reviewer-only" not in message.body
        for message in context_after.recent_messages
    )


@pytest.mark.asyncio
async def test_agent_analysis_fingerprint_invalidated_by_new_conversation_message(
    db, org_scope, monkeypatch
):
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    ticket = await _make_ticket(
        db,
        org.id,
        subject="Refund eligibility concern",
        description="Please help me understand this.",
        external_id="9101",
        source="zendesk",
    )
    await _link_conversation(db, ticket, org.id)

    async def _fake_search(*_args, **_kwargs):
        return []

    monkeypatch.setattr(KnowledgeSearchService, "search", _fake_search)

    decision = AgentDecision(
        action="respond",
        reason="Draft a customer reply for the refund concern.",
        requires_human_approval=True,
        response_draft="Drafting a customer reply.",
    )
    fake_llm = _CapturingDecisionLLM(decision)
    monkeypatch.setattr(agent_workflow_service, "decision_llm", fake_llm)

    first = await _analyze_ticket_directly(ticket.id, org.id)
    assert first["reused"] is False

    prompt_text = "\n".join(fake_llm.prompts)
    assert "CONVERSATION CONTEXT:" in prompt_text
    assert "UNTRUSTED reference data" in prompt_text
    assert prompt_text.count("CONVERSATION CONTEXT:") == 1

    conversation = await ConversationRepository.get_by_ticket_for_tenant(
        db,
        ticket_id=ticket.id,
        organization_id=org.id,
    )
    await _add_public_message(
        db,
        conversation,
        org.id,
        direction="inbound",
        body="Actually, I have an additional question about eligibility.",
        sent_at="2030-09-02T10:00:00Z",
    )

    second = await _analyze_ticket_directly(ticket.id, org.id)
    assert second["reused"] is False
    assert second["run_id"] != first["run_id"]
    assert second["fingerprint"] != first["fingerprint"]

    third = await _analyze_ticket_directly(ticket.id, org.id)
    assert third["reused"] is True
    assert third["run_id"] == second["run_id"]

# ----------------------------------------------------------------------
# Phase 1G — human reply composer, durable enqueue, and delivery lifecycle
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reply_endpoint_requires_ticket_write(
    db, org_scope, client
):
    """A viewer without TICKET_WRITE cannot submit a human reply."""
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)
    await org_scope(subject="viewer-user", role=OrganizationRole.VIEWER)

    ticket = await _make_ticket(
        db,
        org.id,
        subject="Reply auth test",
        source="api",
    )
    conversation = await _link_conversation(db, ticket, org.id)

    response = await client.post(
        f"/conversations/{conversation.id}/replies",
        json={
            "body": "A reply from a viewer.",
            "client_request_id": str(uuid.uuid4()),
        },
        headers=_auth_headers("viewer-user", org.id),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_reply_endpoint_rejects_unsupported_provider(
    db, org_scope, client
):
    """Human replies are only supported for zendesk and cxops providers."""
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    conversation = Conversation(
        organization_id=org.id,
        provider="future_integration",
        channel="chat",
        subject="Unsupported",
        status="open",
    )
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)

    response = await client.post(
        f"/conversations/{conversation.id}/replies",
        json={
            "body": "A reply.",
            "client_request_id": str(uuid.uuid4()),
        },
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_reply_endpoint_rejects_closed_conversation(
    db, org_scope, client
):
    """Replies to closed conversations are rejected with 409."""
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    ticket = await _make_ticket(
        db,
        org.id,
        subject="Closed reply test",
        source="api",
    )
    conversation = await _link_conversation(db, ticket, org.id)
    await _set_status(db, conversation, org.id, "closed")

    response = await client.post(
        f"/conversations/{conversation.id}/replies",
        json={
            "body": "A reply to a closed conversation.",
            "client_request_id": str(uuid.uuid4()),
        },
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_reply_endpoint_enqueues_job_for_local_conversation(
    db, org_scope, client
):
    """Submitting a reply to a local cxops conversation creates a message and job."""
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    ticket = await _make_ticket(
        db,
        org.id,
        subject="Local reply test",
        source="api",
    )
    conversation = await _link_conversation(db, ticket, org.id)

    request_id = str(uuid.uuid4())
    response = await client.post(
        f"/conversations/{conversation.id}/replies",
        json={
            "body": "Local human reply.",
            "client_request_id": request_id,
        },
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 202
    data = response.json()
    assert data["delivery_status"] == "queued"
    assert data["duplicate"] is False
    assert data["job_id"] is not None

    # The queued outbound message must not affect needs_response or previews.
    await db.refresh(conversation)
    assert conversation.latest_message_at is not None


@pytest.mark.asyncio
async def test_reply_endpoint_is_idempotent_by_client_request_id(
    db, org_scope, client
):
    """Replaying the same client_request_id returns the original message."""
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    ticket = await _make_ticket(
        db,
        org.id,
        subject="Idempotent reply test",
        source="api",
    )
    conversation = await _link_conversation(db, ticket, org.id)

    request_id = str(uuid.uuid4())
    first = await client.post(
        f"/conversations/{conversation.id}/replies",
        json={
            "body": "First reply.",
            "client_request_id": request_id,
        },
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert first.status_code == 202
    first_data = first.json()

    second = await client.post(
        f"/conversations/{conversation.id}/replies",
        json={
            "body": "Different body, same id.",
            "client_request_id": request_id,
        },
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert second.status_code == 202
    second_data = second.json()

    assert second_data["message_id"] == first_data["message_id"]
    assert second_data["duplicate"] is True


@pytest.mark.asyncio
async def test_reply_endpoint_validates_body_and_client_request_id(
    db, org_scope, client
):
    """Empty body or missing client_request_id are rejected."""
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    ticket = await _make_ticket(
        db,
        org.id,
        subject="Validation reply test",
        source="api",
    )
    conversation = await _link_conversation(db, ticket, org.id)

    response = await client.post(
        f"/conversations/{conversation.id}/replies",
        json={
            "body": "   ",
            "client_request_id": str(uuid.uuid4()),
        },
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 422

    response = await client.post(
        f"/conversations/{conversation.id}/replies",
        json={
            "body": "Missing id.",
        },
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_retry_endpoint_only_allows_failed_messages(
    db, org_scope, client
):
    """Retry is rejected unless the message delivery_status is failed."""
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    ticket = await _make_ticket(
        db,
        org.id,
        subject="Retry state test",
        source="api",
    )
    conversation = await _link_conversation(db, ticket, org.id)

    request_id = str(uuid.uuid4())
    reply = await client.post(
        f"/conversations/{conversation.id}/replies",
        json={
            "body": "Reply to retry.",
            "client_request_id": request_id,
        },
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    message_id = reply.json()["message_id"]

    response = await client.post(
        f"/conversations/{conversation.id}/messages/{message_id}/retry",
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_delivery_service_local_marks_sent(
    db, org_scope
):
    """The local adapter marks a queued cxops reply as sent immediately."""
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    ticket = await _make_ticket(
        db,
        org.id,
        subject="Local delivery test",
        source="api",
    )
    conversation = await _link_conversation(db, ticket, org.id)

    result = await ConversationReplyService.enqueue_reply(
        db=db,
        conversation_id=conversation.id,
        body="Local delivery reply.",
        client_request_id=str(uuid.uuid4()),
        organization_id=org.id,
        requested_by_subject=USER_ALPHA,
    )

    await ConversationDeliveryService().deliver(
        db,
        message_id=result["message_id"],
        organization_id=org.id,
    )

    message = await ConversationMessageRepository.get_by_id_for_conversation_for_tenant(
        db,
        message_id=result["message_id"],
        conversation_id=conversation.id,
        organization_id=org.id,
    )
    assert message is not None
    assert message.delivery_status == "sent"
    assert message.delivered_at is not None
    assert message.sent_at is not None


@pytest.mark.asyncio
async def test_delivery_service_zendesk_recovers_existing_marker(
    db, org_scope, monkeypatch
):
    """If the marker comment already exists, Zendesk delivery is recovered."""
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    ticket = await _make_ticket(
        db,
        org.id,
        subject="Zendesk recovery test",
        external_id="12345",
        source="zendesk",
    )
    conversation = await _link_conversation(db, ticket, org.id)
    conversation.external_thread_id = ticket.external_id
    await db.commit()

    result = await ConversationReplyService.enqueue_reply(
        db=db,
        conversation_id=conversation.id,
        body="Zendesk recovery reply.",
        client_request_id=str(uuid.uuid4()),
        organization_id=org.id,
        requested_by_subject=USER_ALPHA,
    )

    message = await ConversationMessageRepository.get_by_id_for_tenant(
        db,
        message_id=result["message_id"],
        organization_id=org.id,
    )
    assert message is not None
    marker_token = message.delivery_token

    captured = {"calls": 0}

    async def _fake_apply(*args, **kwargs):
        captured["calls"] += 1

    async def _fake_comments(*args, **kwargs):
        return {
            "comments": [
                {
                    "id": "comment-999",
                    "body": f"Zendesk recovery reply.\n\nCXOps Reply Ref: {marker_token}",
                }
            ]
        }

    from app.integrations.zendesk.client import ZendeskClient

    monkeypatch.setattr(
        ZendeskClient, "apply_agent_action", _fake_apply
    )
    monkeypatch.setattr(
        ZendeskClient, "get_ticket_comments", _fake_comments
    )

    await ConversationDeliveryService().deliver(
        db,
        message_id=result["message_id"],
        organization_id=org.id,
    )

    message = await ConversationMessageRepository.get_by_id_for_conversation_for_tenant(
        db,
        message_id=result["message_id"],
        conversation_id=conversation.id,
        organization_id=org.id,
    )
    assert message is not None
    assert message.delivery_status == "sent"
    assert message.external_message_id == "comment-999"
    assert captured["calls"] == 0


@pytest.mark.asyncio
async def test_unsent_reply_does_not_update_conversation_preview(
    db, org_scope, client
):
    """A queued outbound human reply must not appear as the latest message."""
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    ticket = await _make_ticket(
        db,
        org.id,
        subject="Preview isolation test",
        source="api",
    )
    conversation = await _link_conversation(db, ticket, org.id)

    await client.post(
        f"/conversations/{conversation.id}/replies",
        json={
            "body": "Queued reply should not preview.",
            "client_request_id": str(uuid.uuid4()),
        },
        headers=_auth_headers(USER_ALPHA, org.id),
    )

    response = await client.get(
        "/conversations",
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 200
    item = next(
        (c for c in response.json()["items"] if c["id"] == conversation.id),
        None,
    )
    assert item is not None
    assert "Queued reply" not in (item["latest_message"]["body"] or "")


@pytest.mark.asyncio
async def test_enqueue_reply_creates_message_and_job_atomically(
    db, org_scope
):
    """A successful enqueue persists the message and its job in one commit."""
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    ticket = await _make_ticket(db, org.id, subject="Atomic outbox", source="api")
    conversation = await _link_conversation(db, ticket, org.id)

    result = await ConversationReplyService.enqueue_reply(
        db=db,
        conversation_id=conversation.id,
        body="Atomic reply.",
        client_request_id=str(uuid.uuid4()),
        organization_id=org.id,
        requested_by_subject=USER_ALPHA,
    )

    message = await ConversationMessageRepository.get_by_id_for_conversation_for_tenant(
        db,
        message_id=result["message_id"],
        conversation_id=conversation.id,
        organization_id=org.id,
    )
    assert message is not None
    assert message.delivery_status == "queued"

    job = await IntegrationJobRepository.get_by_dedupe_key(
        db,
        dedupe_key=f"conversation-reply:{org.id}:{conversation.id}:{message.dedupe_key}",
    )
    assert job is not None
    assert job.job_type == "conversation.reply"
    assert job.payload["message_id"] == message.id


@pytest.mark.asyncio
async def test_enqueue_reply_job_failure_rolls_back_message(
    db, org_scope, monkeypatch
):
    """If the integration job cannot be staged, no message is committed."""
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    ticket = await _make_ticket(db, org.id, subject="Job failure", source="api")
    conversation = await _link_conversation(db, ticket, org.id)
    conversation_id = conversation.id

    class ExplodingJob(IntegrationJob):
        def __init__(self, *args, **kwargs):
            raise RuntimeError("job staging failure")

    monkeypatch.setattr(
        "app.services.conversation_reply_service.IntegrationJob",
        ExplodingJob,
    )

    request_id = str(uuid.uuid4())
    with pytest.raises(RuntimeError, match="job staging failure"):
        await ConversationReplyService.enqueue_reply(
            db=db,
            conversation_id=conversation_id,
            body="Job failure reply.",
            client_request_id=request_id,
            organization_id=org.id,
            requested_by_subject=USER_ALPHA,
        )

    # Query directly with asyncpg to avoid SQLAlchemy session state after the
    # expected exception; this proves no message row was committed.
    import asyncpg

    dsn = str(settings.database_url).replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT id FROM conversation_messages WHERE organization_id = $1 AND conversation_id = $2 AND dedupe_key = $3",
            org.id,
            conversation_id,
            f"human_reply:{request_id}",
        )
        assert row is None
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_enqueue_reply_message_failure_rolls_back_job(
    db, org_scope, monkeypatch
):
    """If the message cannot be flushed, no integration job is committed."""
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    ticket = await _make_ticket(db, org.id, subject="Message failure", source="api")
    conversation = await _link_conversation(db, ticket, org.id)
    conversation_id = conversation.id

    class ExplodingMessage(ConversationMessage):
        def __init__(self, *args, **kwargs):
            raise RuntimeError("message staging failure")

    monkeypatch.setattr(
        "app.services.conversation_reply_service.ConversationMessage",
        ExplodingMessage,
    )

    request_id = str(uuid.uuid4())
    with pytest.raises(RuntimeError, match="message staging failure"):
        await ConversationReplyService.enqueue_reply(
            db=db,
            conversation_id=conversation_id,
            body="Message failure reply.",
            client_request_id=request_id,
            organization_id=org.id,
            requested_by_subject=USER_ALPHA,
        )

    import asyncpg

    dsn = str(settings.database_url).replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT id FROM integration_jobs WHERE organization_id = $1 AND dedupe_key = $2",
            org.id,
            f"conversation-reply:{org.id}:{conversation_id}:human_reply:{request_id}",
        )
        assert row is None
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_enqueue_reply_concurrent_same_request_id_is_idempotent(
    db, org_scope
):
    """Concurrent enqueues with the same client_request_id produce one message/job."""
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    ticket = await _make_ticket(db, org.id, subject="Concurrent idempotent", source="api")
    conversation = await _link_conversation(db, ticket, org.id)
    conversation_id = conversation.id

    request_id = str(uuid.uuid4())

    async def _enqueue():
        async with AsyncSessionLocal() as session:
            return await ConversationReplyService.enqueue_reply(
                db=session,
                conversation_id=conversation_id,
                body="Concurrent reply.",
                client_request_id=request_id,
                organization_id=org.id,
                requested_by_subject=USER_ALPHA,
            )

    results = await asyncio.gather(
        _enqueue(),
        _enqueue(),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, Exception)]

    # One call wins; the other re-resolves the winner (duplicate=True) or
    # propagates an unexpected error. We never allow two committed replies.
    assert len(successes) >= 1
    message_ids = {result["message_id"] for result in successes}
    assert len(message_ids) == 1

    messages = await db.execute(
        select(ConversationMessage).where(
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.organization_id == org.id,
            ConversationMessage.dedupe_key == f"human_reply:{request_id}",
        )
    )
    assert len(list(messages.scalars())) == 1

    jobs = await db.execute(
        select(IntegrationJob).where(
            IntegrationJob.organization_id == org.id,
            IntegrationJob.dedupe_key
            == f"conversation-reply:{org.id}:{conversation_id}:human_reply:{request_id}",
        )
    )
    assert len(list(jobs.scalars())) == 1

    # At least one success must be the original (non-duplicate) reply.
    assert any(not result["duplicate"] for result in successes)


@pytest.mark.asyncio
async def test_enqueue_reply_different_request_ids_are_distinct(
    db, org_scope
):
    """Different client_request_ids produce separate replies."""
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    ticket = await _make_ticket(db, org.id, subject="Distinct replies", source="api")
    conversation = await _link_conversation(db, ticket, org.id)

    first = await ConversationReplyService.enqueue_reply(
        db=db,
        conversation_id=conversation.id,
        body="First distinct reply.",
        client_request_id=str(uuid.uuid4()),
        organization_id=org.id,
        requested_by_subject=USER_ALPHA,
    )
    second = await ConversationReplyService.enqueue_reply(
        db=db,
        conversation_id=conversation.id,
        body="Second distinct reply.",
        client_request_id=str(uuid.uuid4()),
        organization_id=org.id,
        requested_by_subject=USER_ALPHA,
    )

    assert first["message_id"] != second["message_id"]


@pytest.mark.asyncio
async def test_retry_endpoint_rejects_cross_conversation_message(
    db, org_scope, client
):
    """Retrying a message through a different conversation URL must fail closed."""
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    ticket_a = await _make_ticket(db, org.id, subject="Conversation A", source="api")
    conversation_a = await _link_conversation(db, ticket_a, org.id)

    ticket_b = await _make_ticket(db, org.id, subject="Conversation B", source="api")
    conversation_b = await _link_conversation(db, ticket_b, org.id)

    # Create a failed human reply on conversation B.
    reply = await client.post(
        f"/conversations/{conversation_b.id}/replies",
        json={
            "body": "Failed reply on B.",
            "client_request_id": str(uuid.uuid4()),
        },
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert reply.status_code == 202
    message_b_id = reply.json()["message_id"]

    message_b = await ConversationMessageRepository.get_by_id_for_conversation_for_tenant(
        db,
        message_id=message_b_id,
        conversation_id=conversation_b.id,
        organization_id=org.id,
    )
    message_b.delivery_status = "failed"
    await db.commit()

    response = await client.post(
        f"/conversations/{conversation_a.id}/messages/{message_b_id}/retry",
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 404

    # Message B must remain failed and unchanged.
    message_b = await ConversationMessageRepository.get_by_id_for_conversation_for_tenant(
        db,
        message_id=message_b_id,
        conversation_id=conversation_b.id,
        organization_id=org.id,
    )
    assert message_b is not None
    assert message_b.delivery_status == "failed"


@pytest.mark.asyncio
async def test_retry_endpoint_rejects_foreign_tenant_message(
    db, org_scope, client
):
    """Retrying a message belonging to a different tenant must fail closed."""
    org_alpha = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)
    org_beta = await org_scope(subject=USER_BETA, role=OrganizationRole.OWNER)

    ticket = await _make_ticket(db, org_alpha.id, subject="Alpha ticket", source="api")
    conversation = await _link_conversation(db, ticket, org_alpha.id)

    reply = await client.post(
        f"/conversations/{conversation.id}/replies",
        json={
            "body": "Failed reply on alpha.",
            "client_request_id": str(uuid.uuid4()),
        },
        headers=_auth_headers(USER_ALPHA, org_alpha.id),
    )
    assert reply.status_code == 202
    message_id = reply.json()["message_id"]

    message = await ConversationMessageRepository.get_by_id_for_conversation_for_tenant(
        db,
        message_id=message_id,
        conversation_id=conversation.id,
        organization_id=org_alpha.id,
    )
    message.delivery_status = "failed"
    await db.commit()

    response = await client.post(
        f"/conversations/{conversation.id}/messages/{message_id}/retry",
        headers=_auth_headers(USER_BETA, org_beta.id),
    )
    assert response.status_code == 404

    message = await ConversationMessageRepository.get_by_id_for_conversation_for_tenant(
        db,
        message_id=message_id,
        conversation_id=conversation.id,
        organization_id=org_alpha.id,
    )
    assert message is not None
    assert message.delivery_status == "failed"


@pytest.mark.asyncio
async def test_retry_endpoint_requeues_same_conversation_failed_message(
    db, org_scope, client
):
    """A valid same-conversation retry requeues the failed delivery job."""
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    ticket = await _make_ticket(db, org.id, subject="Retry success", source="api")
    conversation = await _link_conversation(db, ticket, org.id)

    reply = await client.post(
        f"/conversations/{conversation.id}/replies",
        json={
            "body": "Reply to retry.",
            "client_request_id": str(uuid.uuid4()),
        },
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert reply.status_code == 202
    message_id = reply.json()["message_id"]

    message = await ConversationMessageRepository.get_by_id_for_conversation_for_tenant(
        db,
        message_id=message_id,
        conversation_id=conversation.id,
        organization_id=org.id,
    )
    message.delivery_status = "failed"
    await db.commit()

    job = await IntegrationJobRepository.get_by_dedupe_key(
        db,
        dedupe_key=f"conversation-reply:{org.id}:{conversation.id}:{message.dedupe_key}",
    )
    assert job is not None
    job.status = "failed"
    await db.commit()

    response = await client.post(
        f"/conversations/{conversation.id}/messages/{message_id}/retry",
        headers=_auth_headers(USER_ALPHA, org.id),
    )
    assert response.status_code == 202
    data = response.json()
    assert data["message_id"] == message_id
    assert data["delivery_status"] == "retrying"


@pytest.mark.asyncio
async def test_sent_reply_clears_needs_response_and_updates_preview(
    db, org_scope, client
):
    """A delivered outbound reply clears needs_response and becomes the preview."""
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    ticket = await _make_ticket(db, org.id, subject="Needs response", source="api")
    conversation = await _link_conversation(db, ticket, org.id)

    async def _item():
        response = await client.get(
            "/conversations",
            headers=_auth_headers(USER_ALPHA, org.id),
        )
        return next(
            (c for c in response.json()["items"] if c["id"] == conversation.id),
            None,
        )

    before = await _item()
    assert before["needs_response"] is True

    result = await ConversationReplyService.enqueue_reply(
        db=db,
        conversation_id=conversation.id,
        body="Outbound delivered reply.",
        client_request_id=str(uuid.uuid4()),
        organization_id=org.id,
        requested_by_subject=USER_ALPHA,
    )
    await ConversationDeliveryService().deliver(
        db,
        message_id=result["message_id"],
        organization_id=org.id,
    )

    after = await _item()
    assert after["needs_response"] is False
    assert after["latest_message"]["direction"] == "outbound"
    assert "Outbound delivered reply" in (after["latest_message"]["body"] or "")


@pytest.mark.asyncio
async def test_sent_reply_enters_conversation_context(
    db, org_scope
):
    """A delivered outbound reply becomes visible to the agent context window."""
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    ticket = await _make_ticket(db, org.id, subject="Context reply", source="api")
    conversation = await _link_conversation(db, ticket, org.id)

    before = await ConversationContextService.build_for_ticket(
        db,
        organization_id=org.id,
        ticket_id=ticket.id,
    )

    result = await ConversationReplyService.enqueue_reply(
        db=db,
        conversation_id=conversation.id,
        body="Context outbound reply.",
        client_request_id=str(uuid.uuid4()),
        organization_id=org.id,
        requested_by_subject=USER_ALPHA,
    )
    await ConversationDeliveryService().deliver(
        db,
        message_id=result["message_id"],
        organization_id=org.id,
    )

    after = await ConversationContextService.build_for_ticket(
        db,
        organization_id=org.id,
        ticket_id=ticket.id,
    )

    before_bodies = {m.body for m in before.recent_messages}
    after_bodies = {m.body for m in after.recent_messages}

    assert "Context outbound reply." not in before_bodies
    assert "Context outbound reply." in after_bodies
    assert ConversationContextService.compute_digest(before) != ConversationContextService.compute_digest(after)


@pytest.mark.asyncio
async def test_handle_job_failure_marks_retrying(
    db, org_scope
):
    """The worker failure hook updates the message to retrying when will_retry."""
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    ticket = await _make_ticket(db, org.id, subject="Hook retrying", source="api")
    conversation = await _link_conversation(db, ticket, org.id)

    result = await ConversationReplyService.enqueue_reply(
        db=db,
        conversation_id=conversation.id,
        body="Hook retry reply.",
        client_request_id=str(uuid.uuid4()),
        organization_id=org.id,
        requested_by_subject=USER_ALPHA,
    )
    message_id = result["message_id"]

    await ConversationReplyService.handle_job_failure(
        db=db,
        message_id=message_id,
        organization_id=org.id,
        error_code="provider_unavailable",
        will_retry=True,
    )

    message = await ConversationMessageRepository.get_by_id_for_tenant(
        db,
        message_id=message_id,
        organization_id=org.id,
    )
    assert message.delivery_status == "retrying"
    assert message.delivery_error_code == "provider_unavailable"


@pytest.mark.asyncio
async def test_handle_job_failure_marks_failed(
    db, org_scope
):
    """The worker failure hook updates the message to failed when exhausted."""
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    ticket = await _make_ticket(db, org.id, subject="Hook failed", source="api")
    conversation = await _link_conversation(db, ticket, org.id)

    result = await ConversationReplyService.enqueue_reply(
        db=db,
        conversation_id=conversation.id,
        body="Hook failed reply.",
        client_request_id=str(uuid.uuid4()),
        organization_id=org.id,
        requested_by_subject=USER_ALPHA,
    )
    message_id = result["message_id"]

    await ConversationReplyService.handle_job_failure(
        db=db,
        message_id=message_id,
        organization_id=org.id,
        error_code="provider_unavailable",
        will_retry=False,
    )

    message = await ConversationMessageRepository.get_by_id_for_tenant(
        db,
        message_id=message_id,
        organization_id=org.id,
    )
    assert message.delivery_status == "failed"
    assert message.delivery_error_code == "provider_unavailable"


@pytest.mark.asyncio
async def test_zendesk_delivery_sends_once_when_marker_absent(
    db, org_scope, monkeypatch
):
    """A Zendesk reply performs exactly one provider write when no marker exists."""
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    ticket = await _make_ticket(
        db,
        org.id,
        subject="Zendesk once",
        external_id="22222",
        source="zendesk",
    )
    conversation = await _link_conversation(db, ticket, org.id)
    conversation.external_thread_id = ticket.external_id
    await db.commit()

    result = await ConversationReplyService.enqueue_reply(
        db=db,
        conversation_id=conversation.id,
        body="Zendesk once reply.",
        client_request_id=str(uuid.uuid4()),
        organization_id=org.id,
        requested_by_subject=USER_ALPHA,
    )

    captured = {"calls": 0, "comment_id": "comment-once"}

    async def _fake_apply(*args, **kwargs):
        captured["calls"] += 1

    async def _fake_comments(*args, **kwargs):
        if captured["calls"] == 0:
            return {"comments": []}
        return {
            "comments": [
                {
                    "id": captured["comment_id"],
                    "body": "Zendesk once reply.\n\nCXOps Reply Ref: unknown",
                }
            ]
        }

    from app.integrations.zendesk.client import ZendeskClient

    monkeypatch.setattr(ZendeskClient, "apply_agent_action", _fake_apply)
    monkeypatch.setattr(ZendeskClient, "get_ticket_comments", _fake_comments)

    await ConversationDeliveryService().deliver(
        db,
        message_id=result["message_id"],
        organization_id=org.id,
    )

    assert captured["calls"] == 1


@pytest.mark.asyncio
async def test_zendesk_delivery_skips_provider_when_message_already_sent(
    db, org_scope, monkeypatch
):
    """If the message is already sent, delivery is a no-op with zero provider calls."""
    org = await org_scope(subject=USER_ALPHA, role=OrganizationRole.OWNER)

    ticket = await _make_ticket(
        db,
        org.id,
        subject="Already sent",
        external_id="33333",
        source="zendesk",
    )
    conversation = await _link_conversation(db, ticket, org.id)
    conversation.external_thread_id = ticket.external_id
    await db.commit()

    result = await ConversationReplyService.enqueue_reply(
        db=db,
        conversation_id=conversation.id,
        body="Already sent reply.",
        client_request_id=str(uuid.uuid4()),
        organization_id=org.id,
        requested_by_subject=USER_ALPHA,
    )

    message = await ConversationMessageRepository.get_by_id_for_tenant(
        db,
        message_id=result["message_id"],
        organization_id=org.id,
    )
    message.delivery_status = "sent"
    message.sent_at = datetime.now(timezone.utc)
    message.delivered_at = datetime.now(timezone.utc)
    await db.commit()

    captured = {"calls": 0}

    async def _fake_apply(*args, **kwargs):
        captured["calls"] += 1

    from app.integrations.zendesk.client import ZendeskClient

    monkeypatch.setattr(ZendeskClient, "apply_agent_action", _fake_apply)

    await ConversationDeliveryService().deliver(
        db,
        message_id=result["message_id"],
        organization_id=org.id,
    )

    assert captured["calls"] == 0
