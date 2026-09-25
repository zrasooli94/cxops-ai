"""Knowledge Specialist boundary tests (Phase 1K.1B).

The retrieval graph node delegates to ``_run_knowledge_specialist``, a pure
extraction of the pre-existing ``_retrieve_knowledge`` node. It must:

- call the existing ``KnowledgeSearchService.search`` with the trusted
  organization_id from state,
- return the exact same ``sources`` shape (via ``filter_and_format_sources``),
- fail closed on a NULL-org ticket (no global fallback),
- never call an LLM,
- never write anything besides its knowledge outputs.

These are pure unit tests — no database, no OpenAI.
"""

import os
from typing import Any

import pytest

os.environ["ENVIRONMENT"] = "development"

from app.services.agent_workflow_service import (
    AgentState,
    AgentWorkflowService,
    agent_workflow_service,
)
from app.services.knowledge_search_service import KnowledgeSearchService
from app.services.rag_helpers import filter_and_format_sources

TENANT_A = 111
TENANT_B = 222

_FAKE_DB = object()


def _ticket(subject: str, description: str, *, organization_id: int = TENANT_A) -> dict:
    return {
        "subject": subject,
        "description": description,
        "status": "open",
        "priority": "normal",
        "organization_id": organization_id,
    }


def _state(
    ticket: dict,
    *,
    intent: str = "mixed",
    path: list[str] | None = None,
) -> AgentState:
    return {
        "ticket_id": 99,
        "organization_id": ticket["organization_id"],
        "ticket": ticket,
        "intent": intent,
        "workflow_path": path or ["load_ticket", "assess_knowledge_need"],
    }


def _matches() -> list[dict]:
    return [
        {
            "chunk_id": 101,
            "document_id": 1,
            "content": "Refunds are processed within 5 business days.",
            "distance": 0.1,
            "similarity": 0.9,
            "metadata": {"title": "Refund policy"},
        },
        {
            "chunk_id": 102,
            "document_id": 1,
            "content": "Eligibility requires a verified identity.",
            "distance": 0.2,
            "similarity": 0.8,
            "metadata": {"title": "Refund policy"},
        },
    ]


async def _run_specialist(
    state: AgentState,
) -> dict:
    return await agent_workflow_service._run_knowledge_specialist(
        state,
        db=_FAKE_DB,
    )


@pytest.mark.asyncio
async def test_specialist_calls_existing_search_service(monkeypatch):
    captured: dict[str, Any] = {}

    async def _fake_search(db, *, organization_id, query, limit):
        captured["organization_id"] = organization_id
        captured["query"] = query
        captured["limit"] = limit
        return _matches()

    from app.core.config import settings

    monkeypatch.setattr(KnowledgeSearchService, "search", _fake_search)

    ticket = _ticket("Refund request", "I want a refund.")
    await _run_specialist(_state(ticket))

    assert captured["organization_id"] == TENANT_A
    assert captured["query"] == "Refund request\n\nI want a refund."
    assert captured["limit"] == settings.rag_top_k


@pytest.mark.asyncio
async def test_trusted_organization_id_passed_unchanged(monkeypatch):
    received: list[int | None] = []

    async def _fake_search(db, *, organization_id, query, limit):
        received.append(organization_id)
        return _matches()

    monkeypatch.setattr(KnowledgeSearchService, "search", _fake_search)

    await _run_specialist(
        _state(_ticket("Refund request", "I want a refund.", organization_id=TENANT_B))
    )
    assert received == [TENANT_B]


@pytest.mark.asyncio
async def test_sources_returned_in_same_shape_as_before(monkeypatch):
    async def _fake_search(db, *, organization_id, query, limit):
        return _matches()

    monkeypatch.setattr(KnowledgeSearchService, "search", _fake_search)

    result = await _run_specialist(
        _state(_ticket("Refund request", "I want a refund."))
    )
    assert result["sources"] == filter_and_format_sources(_matches())


@pytest.mark.asyncio
async def test_empty_retrieval_behaves_exactly_as_before(monkeypatch):
    async def _fake_search(db, *, organization_id, query, limit):
        return []

    monkeypatch.setattr(KnowledgeSearchService, "search", _fake_search)

    result = await _run_specialist(
        _state(_ticket("Refund request", "I want a refund."))
    )
    assert result["sources"] == []


@pytest.mark.asyncio
async def test_null_org_ticket_fails_closed_without_search(monkeypatch):
    called = False

    async def _fake_search(db, *, organization_id, query, limit):
        nonlocal called
        called = True
        return _matches()

    monkeypatch.setattr(KnowledgeSearchService, "search", _fake_search)

    ticket = dict(_ticket("Legacy", "No org"))
    ticket["organization_id"] = None
    result = await _run_specialist(_state(ticket))

    assert called is False
    assert result["sources"] == []


@pytest.mark.asyncio
async def test_specialist_makes_no_llm_call(monkeypatch):
    class _ExplodingLLM:
        async def ainvoke(self, *_args, **_kwargs):
            raise AssertionError("Knowledge Specialist must never call an LLM")

    monkeypatch.setattr(agent_workflow_service, "decision_llm", _ExplodingLLM())

    async def _fake_search(db, *, organization_id, query, limit):
        return _matches()

    monkeypatch.setattr(KnowledgeSearchService, "search", _fake_search)

    result = await _run_specialist(
        _state(_ticket("Refund request", "I want a refund."))
    )
    assert result["sources"]


@pytest.mark.asyncio
async def test_specialist_does_not_change_intent(monkeypatch):
    async def _fake_search(db, *, organization_id, query, limit):
        return _matches()

    monkeypatch.setattr(KnowledgeSearchService, "search", _fake_search)

    state = _state(_ticket("Refund request", "I want a refund."))
    result = await _run_specialist(state)
    assert "intent" not in result


@pytest.mark.asyncio
async def test_specialist_does_not_change_organization_id(monkeypatch):
    async def _fake_search(db, *, organization_id, query, limit):
        return _matches()

    monkeypatch.setattr(KnowledgeSearchService, "search", _fake_search)

    result = await _run_specialist(
        _state(_ticket("Refund request", "I want a refund.", organization_id=TENANT_A))
    )
    assert "organization_id" not in result


@pytest.mark.asyncio
async def test_specialist_does_not_create_tool_plan(monkeypatch):
    async def _fake_search(db, *, organization_id, query, limit):
        return _matches()

    monkeypatch.setattr(KnowledgeSearchService, "search", _fake_search)

    result = await _run_specialist(
        _state(_ticket("Refund request", "I want a refund."))
    )
    assert "tool_plan" not in result
    assert "authorization" not in result
    assert "approval" not in result


@pytest.mark.asyncio
async def test_graph_routes_through_knowledge_for_same_cases():
    knowledge_case = await agent_workflow_service._assess_knowledge_need(
        _state(_ticket("Refund request", "I want a refund."))
    )
    assert knowledge_case["needs_knowledge"] is True
    assert (
        AgentWorkflowService._route_after_assessment(knowledge_case)
        == "retrieve_knowledge"
    )

    fast_path = await agent_workflow_service._assess_knowledge_need(
        _state(_ticket("Thank you", "All good now."))
    )
    assert fast_path["needs_knowledge"] is False
    assert AgentWorkflowService._route_after_assessment(fast_path) == "decide_action"


@pytest.mark.asyncio
async def test_workflow_path_records_compat_label_and_specialist_marker(monkeypatch):
    async def _fake_search(db, *, organization_id, query, limit):
        return _matches()

    monkeypatch.setattr(KnowledgeSearchService, "search", _fake_search)

    result = await _run_specialist(
        _state(
            _ticket("Refund request", "I want a refund."),
            path=["load_ticket", "assess_knowledge_need"],
        )
    )
    assert result["workflow_path"] == [
        "load_ticket",
        "assess_knowledge_need",
        "retrieve_knowledge",
        "knowledge_specialist",
    ]
    # Phase 1J compatibility: the historical label is still present.
    assert "retrieve_knowledge" in result["workflow_path"]


@pytest.mark.asyncio
async def test_backward_compatible_alias_delegates_to_specialist(monkeypatch):
    async def _fake_search(db, *, organization_id, query, limit):
        return _matches()

    monkeypatch.setattr(KnowledgeSearchService, "search", _fake_search)

    state = _state(_ticket("Refund request", "I want a refund."))
    specialist = await agent_workflow_service._run_knowledge_specialist(
        state, db=_FAKE_DB
    )
    alias = await agent_workflow_service._retrieve_knowledge(state, db=_FAKE_DB)
    assert alias == specialist