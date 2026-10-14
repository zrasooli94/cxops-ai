"""Phase 1U-D: one continuous public-chat to staff-handoff lifecycle.

All requests use an in-process ASGI client and disposable PostgreSQL.
The only AI decision is a deterministic fake. No Nhost, model, Telegram,
email, Zendesk or production services are contacted.

The individual operations have their own existing tests; these tests certify
that their persisted states compose into one coherent multi-actor journey.
"""
from __future__ import annotations

import asyncio
import secrets
import uuid

import pytest
from sqlalchemy import func, select

from app.core.rbac import OrganizationRole
from app.models.conversation import Conversation
from app.models.conversation_message import ConversationMessage
from app.models.integration_job import IntegrationJob
from app.models.public_chat import PublicChatSession
from app.models.ticket import Ticket
from app.services.public_chat_service import PublicChatService, hash_digest
from tests import test_public_chat as widget_base
from tests import test_staff_public_chat_resolve as staff_base

# Reuse the established real-DB test fixtures and their complete namespace
# teardown rather than developing a second, less-audited purge mechanism.
_configure_auth = staff_base._configure_auth
_p1p7r_namespace_isolated = staff_base._p1p7r_namespace_isolated
client = staff_base.client
db = staff_base.db
org_scope = staff_base.org_scope


@pytest.fixture(autouse=True)
def _handoff_lifecycle_outbound_disabled(monkeypatch):
    # Even if a developer's .env enabled alerts, the test must not be able to
    # send or stage them. Existing production notification code is untouched.
    from app.services import handoff_notification_service

    monkeypatch.setattr(
        handoff_notification_service.settings,
        "handoff_notifications_enabled",
        False,
    )


async def _create_http_widget_session(db, client, org):
    config = await staff_base._get_or_create_config(db, org)
    public_key = f"pk_live_{secrets.token_hex(20)}"
    config.public_widget_key_hash = hash_digest(public_key)
    await db.commit()
    response = await client.post(
        "/public/chat/sessions",
        json={"public_widget_key": public_key},
        headers={"X-Embedding-Origin": staff_base.ALLOWED_ORIGIN},
    )
    assert response.status_code == 200, response.text
    token = response.json()["session"]["token"]
    result = await db.execute(
        select(PublicChatSession)
        .where(
            PublicChatSession.organization_id == org.id,
            PublicChatSession.token_hash == hash_digest(token),
        )
        .execution_options(populate_existing=True)
    )
    row = result.scalar_one()
    assert row.status == "ai_active"
    return {"token": token, "session_id": row.id, "ticket_id": row.ticket_id, "conversation_id": row.conversation_id}


def _widget_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Embedding-Origin": staff_base.ALLOWED_ORIGIN,
    }


async def _fresh(db, model, id_):
    return await staff_base._fresh_row(db, model, id_)


@pytest.mark.asyncio
async def test_complete_customer_ai_handoff_assign_release_resolve_journey(
    db, client, org_scope, monkeypatch
):
    """Public create/message -> human -> assigned -> released -> assigned -> resolved."""
    org = await org_scope()
    tenant_id = org.id
    agent_subject = f"staff-{uuid.uuid4().hex[:10]}"
    viewer_subject = f"viewer-{uuid.uuid4().hex[:10]}"
    await staff_base._add_membership(db, agent_subject, tenant_id, role=OrganizationRole.AGENT)
    await staff_base._add_membership(db, viewer_subject, tenant_id, role=OrganizationRole.VIEWER)
    staff_headers = staff_base._staff_headers(agent_subject, tenant_id)
    viewer_headers = staff_base._staff_headers(viewer_subject, tenant_id)

    # Real session creation and first turn, but deliberately fake the decision
    # model and knowledge search. No external model call may occur.
    widget_base._stub_agent(monkeypatch, widget_base._ai_decision(action="no_action"))
    widget = await _create_http_widget_session(db, client, org)
    sid = widget["session_id"]
    token = widget["token"]
    first = await client.post(
        "/public/chat/messages",
        json={
            "client_message_id": "phase1ud-first",
            "text": "I have a question about my order.",
        },
        headers=_widget_headers(token),
    )
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "ai_active"
    assert first.json()["handoff"] is False
    assert first.json()["reply"]
    first_id = first.json()["message_id"]

    # A real visitor explicitly requests help. Repeating the HTTP request is
    # idempotent; the queue is now the authority.
    for _ in range(2):
        requested = await client.post(
            "/public/chat/sessions/human",
            headers=_widget_headers(token),
        )
        assert requested.status_code == 200, requested.text
        assert requested.json()["status"] == "human_requested"

    queue = await client.get("/staff/public-chat/handoff", headers=staff_headers)
    assert queue.status_code == 200, queue.text
    waiting_ids = [row["session_id"] for row in queue.json()["sessions"]]
    assert waiting_ids.count(sid) == 1

    # Read-only staff can see the queue but cannot operate it.
    listing = await client.get("/staff/public-chat/handoff", headers=viewer_headers)
    assert listing.status_code == 200
    denied = await client.post(
        f"/staff/public-chat/sessions/{sid}/assign", headers=viewer_headers
    )
    assert denied.status_code == 403
    assert (await _fresh(db, PublicChatSession, sid)).status == "human_requested"

    assigned = await client.post(
        f"/staff/public-chat/sessions/{sid}/assign", headers=staff_headers
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["status"] == "human_assigned"
    assert assigned.json()["assigned_to_subject"] == agent_subject
    assigned_at = assigned.json()["assigned_at"]
    assert assigned_at is not None
    denied_retry = await client.post(
        f"/staff/public-chat/sessions/{sid}/assign", headers=staff_headers
    )
    assert denied_retry.status_code == 409

    # The visitor keeps speaking to staff. Any attempt to call AI/RAG/tools
    # after handoff must fail this test, rather than incur model costs.
    async def forbid_ai(*_args, **_kwargs):
        raise AssertionError("A handed-off message attempted another AI turn")

    monkeypatch.setattr(PublicChatService, "_ai_reply_turn", classmethod(forbid_ai))
    followup = {
        "client_message_id": "phase1ud-followup",
        "text": "Please let me know once a person can help.",
    }
    received = await client.post(
        "/public/chat/messages", json=followup, headers=_widget_headers(token)
    )
    assert received.status_code == 200, received.text
    assert received.json()["status"] == "human_assigned"
    assert received.json()["reply"] is None
    followup_id = received.json()["message_id"]
    repeat = await client.post(
        "/public/chat/messages", json=followup, headers=_widget_headers(token)
    )
    assert repeat.status_code == 200, repeat.text
    assert repeat.json()["message_id"] == followup_id
    assert repeat.json()["reply"] is None
    state = await client.get(
        "/public/chat/sessions/state", headers=_widget_headers(token)
    )
    assert state.status_code == 200
    assert state.json()["status"] == "human_assigned"
    assert any(m["body"] == followup["text"] for m in state.json()["messages"])

    released = await client.post(
        f"/staff/public-chat/sessions/{sid}/release", headers=staff_headers
    )
    assert released.status_code == 200, released.text
    assert released.json()["status"] == "human_requested"
    assert released.json()["released_at"] is not None
    assert released.json()["assigned_to_subject"] is None
    repeated_release = await client.post(
        f"/staff/public-chat/sessions/{sid}/release", headers=staff_headers
    )
    assert repeated_release.status_code == 409
    queue_after_release = await client.get(
        "/staff/public-chat/handoff", headers=staff_headers
    )
    assert queue_after_release.status_code == 200
    assert sid in [row["session_id"] for row in queue_after_release.json()["sessions"]]

    reassigned = await client.post(
        f"/staff/public-chat/sessions/{sid}/assign", headers=staff_headers
    )
    assert reassigned.status_code == 200
    assert reassigned.json()["status"] == "human_assigned"

    resolved = await client.post(
        f"/staff/public-chat/sessions/{sid}/resolve", headers=staff_headers
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "closed"
    closed_at = resolved.json()["updated_at"]
    repeated_resolve = await client.post(
        f"/staff/public-chat/sessions/{sid}/resolve", headers=staff_headers
    )
    assert repeated_resolve.status_code == 200, repeated_resolve.text
    assert repeated_resolve.json()["status"] == "closed"
    assert repeated_resolve.json()["updated_at"] == closed_at

    session_row = await _fresh(db, PublicChatSession, sid)
    ticket = await _fresh(db, Ticket, widget["ticket_id"])
    conversation = await _fresh(db, Conversation, widget["conversation_id"])
    assert session_row.status == "closed" and session_row.closed_at is not None
    assert session_row.assigned_to_subject == agent_subject
    assert session_row.assigned_at is not None
    assert ticket.status == "solved" and ticket.resolved_at is not None
    assert conversation.status == "closed"
    saved = (
        await db.execute(
            select(ConversationMessage).where(
                ConversationMessage.organization_id == tenant_id,
                ConversationMessage.conversation_id == widget["conversation_id"],
                ConversationMessage.direction == "inbound",
            )
        )
    ).scalars().all()
    assert sum(m.id == first_id for m in saved) == 1
    assert sum(m.id == followup_id for m in saved) == 1
    assert [m.body for m in saved].count(followup["text"]) == 1
    no_more = await client.post(
        "/public/chat/sessions/human", headers=_widget_headers(token)
    )
    assert no_more.status_code == 409
    queue_after_close = await client.get(
        "/staff/public-chat/handoff", headers=staff_headers
    )
    assert sid not in [row["session_id"] for row in queue_after_close.json()["sessions"]]
    # OFF means no notification outbox or live email/Telegram, not merely no send.
    notifications = await db.scalar(
        select(func.count(IntegrationJob.id)).where(
            IntegrationJob.organization_id == tenant_id,
            IntegrationJob.job_type == "handoff.notify",
        )
    )
    assert notifications == 0


@pytest.mark.asyncio
async def test_foreign_staff_cannot_operate_or_read_a_queued_session(
    db, client, org_scope
):
    first_org = await org_scope()
    other_org = await org_scope()
    owner_a = f"owner-a-{uuid.uuid4().hex[:10]}"
    owner_b = f"owner-b-{uuid.uuid4().hex[:10]}"
    await staff_base._add_membership(db, owner_a, first_org.id)
    await staff_base._add_membership(db, owner_b, other_org.id)
    widget = await _create_http_widget_session(db, client, first_org)
    sid = widget["session_id"]
    requested = await client.post(
        "/public/chat/sessions/human", headers=_widget_headers(widget["token"])
    )
    assert requested.status_code == 200
    foreign_headers = staff_base._staff_headers(owner_b, other_org.id)
    for operation in ("assign", "release", "resolve"):
        denied = await client.post(
            f"/staff/public-chat/sessions/{sid}/{operation}",
            headers=foreign_headers,
        )
        assert denied.status_code == 404, denied.text
    foreign_actions = await client.get(
        f"/staff/public-chat/sessions/{sid}/business-actions",
        headers=foreign_headers,
    )
    assert foreign_actions.status_code == 404
    list_other = await client.get(
        "/staff/public-chat/handoff", headers=foreign_headers
    )
    assert list_other.status_code == 200
    assert sid not in [row["session_id"] for row in list_other.json()["sessions"]]
    forged_selector = await client.get(
        "/staff/public-chat/handoff",
        headers=staff_base._staff_headers(owner_b, first_org.id),
    )
    assert forged_selector.status_code == 403
    owner_queue = await client.get(
        "/staff/public-chat/handoff",
        headers=staff_base._staff_headers(owner_a, first_org.id),
    )
    assert owner_queue.status_code == 200
    assert sid in [row["session_id"] for row in owner_queue.json()["sessions"]]
    unchanged = await _fresh(db, PublicChatSession, sid)
    assert unchanged.status == "human_requested" and unchanged.assigned_to_subject is None


@pytest.mark.asyncio
async def test_concurrent_staff_claims_have_one_winner(db, client, org_scope):
    org = await org_scope()
    a = f"agent-a-{uuid.uuid4().hex[:10]}"
    b = f"agent-b-{uuid.uuid4().hex[:10]}"
    await staff_base._add_membership(db, a, org.id, role=OrganizationRole.AGENT)
    await staff_base._add_membership(db, b, org.id, role=OrganizationRole.AGENT)
    widget = await _create_http_widget_session(db, client, org)
    sid = widget["session_id"]
    initial = await client.post(
        "/public/chat/sessions/human", headers=_widget_headers(widget["token"])
    )
    assert initial.status_code == 200
    url = f"/staff/public-chat/sessions/{sid}/assign"
    results = await asyncio.gather(
        client.post(url, headers=staff_base._staff_headers(a, org.id)),
        client.post(url, headers=staff_base._staff_headers(b, org.id)),
    )
    assert sorted(resp.status_code for resp in results) == [200, 409]
    winner = next(resp for resp in results if resp.status_code == 200)
    session = await _fresh(db, PublicChatSession, sid)
    assert session.status == "human_assigned"
    assert session.assigned_to_subject == winner.json()["assigned_to_subject"]
    assert session.assigned_to_subject in {a, b}


@pytest.mark.asyncio
async def test_handoff_rejects_wrong_origin_and_invalid_token_without_changing_state(
    db, client, org_scope
):
    org = await org_scope()
    widget = await _create_http_widget_session(db, client, org)
    sid = widget["session_id"]
    invalid = await client.post(
        "/public/chat/sessions/human",
        headers={"Authorization": "Bearer synthetic-invalid-session-token"},
    )
    assert invalid.status_code == 401
    wrong_origin = await client.post(
        "/public/chat/messages",
        json={"client_message_id": "phase1ud-invalid-origin", "text": "Hello"},
        headers={
            "Authorization": f"Bearer {widget['token']}",
            "X-Embedding-Origin": "https://not-approved.invalid",
        },
    )
    assert wrong_origin.status_code == 403
    session = await _fresh(db, PublicChatSession, sid)
    assert session.status == "ai_active"
    count = await db.scalar(
        select(func.count(ConversationMessage.id)).where(
            ConversationMessage.conversation_id == widget["conversation_id"],
            ConversationMessage.organization_id == org.id,
        )
    )
    assert count == 0
