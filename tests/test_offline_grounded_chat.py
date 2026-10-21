"""Offline grounded provider: no model calls, real tenant-scoped knowledge."""

import hashlib
import uuid

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.knowledge_document import KnowledgeDocument
from app.models.organization import Organization
from app.services.offline_grounded_chat import offline_grounded_chat


async def _knowledge(db, *, organization_id: int, title: str, content: str) -> None:
    document = KnowledgeDocument(
        organization_id=organization_id,
        title=title,
        source="manual",
        checksum=hashlib.sha256(f"{organization_id}:{title}".encode()).hexdigest(),
        metadata_json={},
    )
    db.add(document)
    await db.flush()
    db.add(
        KnowledgeChunk(
            organization_id=organization_id,
            document_id=document.id,
            chunk_index=0,
            content=content,
            token_count=len(content.split()),
            metadata_json={},
            embedding=[0.0] * 1536,
        )
    )
    await db.commit()


@pytest.mark.asyncio
async def test_answers_from_matching_tenant_chunk_only(db):
    suffix = uuid.uuid4().hex
    own = Organization(name=f"Offline own {suffix}")
    foreign = Organization(name=f"Offline foreign {suffix}")
    db.add_all([own, foreign])
    await db.commit()
    await _knowledge(
        db,
        organization_id=own.id,
        title="Opening hours",
        content="The workshop opens Monday to Friday from 9 AM to 6 PM.",
    )
    await _knowledge(
        db,
        organization_id=foreign.id,
        title="Opening hours",
        content="The foreign shop is open at midnight.",
    )

    answer = await offline_grounded_chat.answer(
        db,
        organization_id=own.id,
        question="What are your workshop opening hours?",
    )

    assert answer is not None
    assert "Monday to Friday" in answer.text
    assert "midnight" not in answer.text
    assert answer.title == "Opening hours"


@pytest.mark.asyncio
async def test_unsupported_and_action_requests_fail_closed(db):
    suffix = uuid.uuid4().hex
    organization = Organization(name=f"Offline guard {suffix}")
    db.add(organization)
    await db.commit()
    await _knowledge(
        db,
        organization_id=organization.id,
        title="Refund policy",
        content="Unused accessories may be returned within 14 days.",
    )

    assert await offline_grounded_chat.answer(
        db,
        organization_id=organization.id,
        question="Do you repair snowboards?",
    ) is None


@pytest.mark.asyncio
async def test_referential_followup_reuses_only_the_previous_question_topic(db):
    suffix = uuid.uuid4().hex
    organization = Organization(name=f"Offline followup {suffix}")
    db.add(organization)
    await db.commit()
    await _knowledge(
        db,
        organization_id=organization.id,
        title="Opening hours",
        content="The workshop opens Monday to Friday from 9 AM to 6 PM.",
    )

    answer = await offline_grounded_chat.answer(
        db,
        organization_id=organization.id,
        question="What about that?",
        previous_customer_question="What are your workshop opening hours?",
    )

    assert answer is not None
    assert "Monday to Friday" in answer.text


@pytest.mark.asyncio
async def test_informational_refund_question_is_not_mistaken_for_an_action(db):
    suffix = uuid.uuid4().hex
    organization = Organization(name=f"Offline policy {suffix}")
    db.add(organization)
    await db.commit()
    await _knowledge(
        db,
        organization_id=organization.id,
        title="Refund policy",
        content="Unused accessories may be returned within 14 days.",
    )

    answer = await offline_grounded_chat.answer(
        db,
        organization_id=organization.id,
        question="What is your refund policy for unused accessories?",
    )

    assert answer is not None
    assert "14 days" in answer.text
    assert await offline_grounded_chat.answer(
        db,
        organization_id=organization.id,
        question="Refund me now",
    ) is None


def test_offline_provider_cannot_be_enabled_in_production():
    with pytest.raises(ValidationError, match="cannot be enabled in production"):
        Settings(
            environment="production",
            debug=False,
            auth_mode="jwks",
            auth_jwks_url="https://auth.example/.well-known/jwks.json",
            encryption_keys="",
            public_chat_offline_provider_enabled=True,
        )
