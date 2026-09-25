"""Explicit specialist graph wiring tests (Phase 1K.1D).

The compiled workflow (``_build_workflow``) binds each persisted graph node to
its explicit specialist boundary:

* ``assess_knowledge_need`` → ``_run_coordinator``
* ``retrieve_knowledge`` → ``_run_knowledge_specialist``
* ``decide_action`` → ``_run_action_specialist``

Node names, routing, and the specialist order are byte-for-byte unchanged from
Phase 1K.1A–1K.1C. These tests drive the REAL compiled LangGraph workflow with
faked DB/LLM/knowledge-search so the hidden handoff order, markers, LLM call
count, tenant continuity, tool plan, and authorization boundary are asserted
end-to-end.

No real OpenAI call and no database.
"""

import os
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import pytest

os.environ["ENVIRONMENT"] = "development"

from app.services.agent_workflow_service import (
    agent_workflow_service,
)
from app.services.knowledge_search_service import KnowledgeSearchService
from app.services.rag_helpers import filter_and_format_sources

TENANT_A = 111

_FAKE_DB = object()


def _ticket(subject: str, description: str, *, organization_id: int = TENANT_A) -> dict:
    return {
        "id": 1,
        "organization_id": organization_id,
        "subject": subject,
        "description": description,
        "status": "open",
        "priority": "normal",
        "category": "billing",
        "assigned_team": None,
        "requester_email": "ada@example.com",
        "source": "email",
        "customer_id": 5,
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
        }
    ]


def _respond_parsed() -> dict:
    return {
        "action": "respond",
        "reason": "Draft a safe customer-facing refund reply.",
        "recommended_team": None,
        "recommended_priority": None,
        "response_draft": "Thanks for contacting us. We will review your request.",
        "requires_human_approval": True,
    }


class _FakeStructuredLLM:
    """Records every ainvoke; returns a fake structured-output result."""

    def __init__(self, parsed: dict) -> None:
        self.calls: list[list[Any]] = []
        self.parsed = parsed

    async def ainvoke(self, messages: list[Any], **_kwargs) -> dict:
        self.calls.append(messages)
        return {
            "raw": SimpleNamespace(
                usage_metadata={
                    "input_tokens": 7,
                    "output_tokens": 9,
                    "total_tokens": 16,
                }
            ),
            "parsed": self.parsed,
            "parsing_error": None,
        }


async def _run_flow(
    monkeypatch,
    *,
    ticket: dict,
    fake_llm: _FakeStructuredLLM | None,
    fake_search: Callable[..., Awaitable[list[dict]]] | None = None,
) -> dict:
    async def _load(state: dict, *, db: Any) -> dict:
        return {
            "ticket": ticket,
            "workflow_path": [*state["workflow_path"], "load_ticket"],
        }

    monkeypatch.setattr(agent_workflow_service, "_load_ticket", _load)

    if fake_llm is not None:
        monkeypatch.setattr(agent_workflow_service, "decision_llm", fake_llm)

    if fake_search is not None:
        monkeypatch.setattr(KnowledgeSearchService, "search", fake_search)
    else:
        async def _search(db: Any, *, organization_id, query, limit) -> list[dict]:
            return _matches()

        monkeypatch.setattr(KnowledgeSearchService, "search", _search)

    workflow = agent_workflow_service._build_workflow(_FAKE_DB)

    return await workflow.ainvoke(
        {
            "ticket_id": ticket["id"],
            "organization_id": ticket["organization_id"],
            "workflow_path": [],
            "sources": [],
            "tool_plan": [],
            "customer_context": None,
            "conversation_context": None,
        }
    )


async def _spy_on(monkeypatch, method_name: str, **spy_kwargs: Any) -> dict:
    counter: dict[str, int] = {"n": 0}
    original = getattr(agent_workflow_service, method_name)
    saved_kwargs = spy_kwargs

    async def wrapped(state: dict, **kwargs: Any) -> dict:
        counter["n"] += 1
        return await original(state, **saved_kwargs, **kwargs)

    monkeypatch.setattr(agent_workflow_service, method_name, wrapped)
    return counter


@pytest.mark.asyncio
async def test_coordinator_node_calls_coordinator_boundary(monkeypatch):
    counter = await _spy_on(monkeypatch, "_run_coordinator")
    ticket = _ticket("Thank you", "All good now.")
    result = await _run_flow(monkeypatch, ticket=ticket, fake_llm=None)

    assert counter["n"] == 1
    assert result["workflow_path"][1:] == [
        "assess_knowledge_need",
        "coordinator",
        "decide_action",
        "action_specialist",
        "build_tool_plan",
    ]


@pytest.mark.asyncio
async def test_knowledge_node_calls_knowledge_specialist(monkeypatch):
    counter = await _spy_on(monkeypatch, "_run_knowledge_specialist")
    search_calls: list[dict[str, Any]] = []

    async def _search(db: Any, *, organization_id, query, limit) -> list[dict]:
        search_calls.append(
            {"organization_id": organization_id, "query": query, "limit": limit}
        )
        return _matches()

    ticket = _ticket("Refund request", "I want a refund.")
    await _run_flow(
        monkeypatch,
        ticket=ticket,
        fake_llm=_FakeStructuredLLM(_respond_parsed()),
        fake_search=_search,
    )

    assert counter["n"] == 1
    assert len(search_calls) == 1
    assert search_calls[0]["organization_id"] == TENANT_A
    assert search_calls[0]["query"] == "Refund request\n\nI want a refund."


@pytest.mark.asyncio
async def test_action_node_calls_action_specialist(monkeypatch):
    counter = await _spy_on(monkeypatch, "_run_action_specialist")
    ticket = _ticket("Refund request", "I want a refund.")
    await _run_flow(
        monkeypatch,
        ticket=ticket,
        fake_llm=_FakeStructuredLLM(_respond_parsed()),
    )

    assert counter["n"] == 1


@pytest.mark.asyncio
async def test_knowledge_needed_flow_has_specialist_order(monkeypatch):
    ticket = _ticket("Refund request", "I want a refund.")
    result = await _run_flow(
        monkeypatch,
        ticket=ticket,
        fake_llm=_FakeStructuredLLM(_respond_parsed()),
    )

    assert result["workflow_path"] == [
        "load_ticket",
        "assess_knowledge_need",
        "coordinator",
        "retrieve_knowledge",
        "knowledge_specialist",
        "decide_action",
        "action_specialist",
        "build_tool_plan",
    ]


@pytest.mark.asyncio
async def test_no_knowledge_flow_skips_knowledge_specialist(monkeypatch):
    search_called = False

    async def _search(db: Any, *, organization_id, query, limit) -> list[dict]:
        nonlocal search_called
        search_called = True
        return _matches()

    ticket = _ticket("Thank you", "All good now.")
    result = await _run_flow(
        monkeypatch,
        ticket=ticket,
        fake_llm=None,
        fake_search=_search,
    )

    assert search_called is False
    assert "retrieve_knowledge" not in result["workflow_path"]
    assert "knowledge_specialist" not in result["workflow_path"]
    assert result["workflow_path"] == [
        "load_ticket",
        "assess_knowledge_need",
        "coordinator",
        "decide_action",
        "action_specialist",
        "build_tool_plan",
    ]


@pytest.mark.asyncio
async def test_fast_path_behavior_unchanged(monkeypatch):
    fake_llm = _FakeStructuredLLM(_respond_parsed())
    ticket = _ticket("Thank you", "All good now.")
    result = await _run_flow(monkeypatch, ticket=ticket, fake_llm=fake_llm)

    # Fast path: zero LLM calls, deterministic no_action decision.
    assert fake_llm.calls == []
    assert result["decision"]["action"] == "no_action"
    assert result["decision"]["response_draft"] is None
    assert result["tool_plan"][0]["tool"] == "none"
    assert result["tool_plan"][0]["arguments"] == {}


@pytest.mark.asyncio
async def test_normal_path_exactly_one_llm_call(monkeypatch):
    fake_llm = _FakeStructuredLLM(_respond_parsed())
    ticket = _ticket("Refund request", "I want a refund.")
    result = await _run_flow(monkeypatch, ticket=ticket, fake_llm=fake_llm)

    assert len(fake_llm.calls) == 1
    assert result["decision"]["action"] == "respond"
    assert result["decision_observability"]["llm_called"] is True


@pytest.mark.asyncio
async def test_specialist_markers_in_workflow_path(monkeypatch):
    ticket = _ticket("Refund request", "I want a refund.")
    result = await _run_flow(
        monkeypatch,
        ticket=ticket,
        fake_llm=_FakeStructuredLLM(_respond_parsed()),
    )

    assert "coordinator" in result["workflow_path"]
    assert "knowledge_specialist" in result["workflow_path"]
    assert "action_specialist" in result["workflow_path"]


@pytest.mark.asyncio
async def test_old_compatibility_labels_remain(monkeypatch):
    ticket = _ticket("Refund request", "I want a refund.")
    result = await _run_flow(
        monkeypatch,
        ticket=ticket,
        fake_llm=_FakeStructuredLLM(_respond_parsed()),
    )

    assert "load_ticket" in result["workflow_path"]
    assert "assess_knowledge_need" in result["workflow_path"]
    assert "retrieve_knowledge" in result["workflow_path"]
    assert "decide_action" in result["workflow_path"]
    assert "build_tool_plan" in result["workflow_path"]


@pytest.mark.asyncio
async def test_tenant_id_unchanged_through_full_graph(monkeypatch):
    ticket = _ticket("Refund request", "I want a refund.", organization_id=TENANT_A)
    result = await _run_flow(
        monkeypatch,
        ticket=ticket,
        fake_llm=_FakeStructuredLLM(_respond_parsed()),
    )

    assert result["organization_id"] == TENANT_A
    assert result["ticket"]["organization_id"] == TENANT_A


@pytest.mark.asyncio
async def test_tool_plan_output_equivalent(monkeypatch):
    fake_llm = _FakeStructuredLLM(_respond_parsed())
    ticket = _ticket("Refund request", "I want a refund.")
    result = await _run_flow(monkeypatch, ticket=ticket, fake_llm=fake_llm)

    assert [tool["tool"] for tool in result["tool_plan"]] == ["zendesk.send_reply"]
    assert (
        result["tool_plan"][0]["arguments"]["body"]
        == result["decision"]["response_draft"]
    )


@pytest.mark.asyncio
async def test_tool_authorization_runs_after_action_specialist(monkeypatch):
    fake_llm = _FakeStructuredLLM(_respond_parsed())
    ticket = _ticket("Refund request", "I want a refund.")
    result = await _run_flow(monkeypatch, ticket=ticket, fake_llm=fake_llm)

    tool = result["tool_plan"][0]
    # ToolAuthorizationService enriched the proposed reply tool.
    assert tool["tool"] == "zendesk.send_reply"
    assert "authorized" in tool
    assert "risk_level" in tool
    assert "required_capability" in tool
    assert "requires_approval" in tool


@pytest.mark.asyncio
async def test_compatibility_aliases_still_delegate(monkeypatch):
    # _assess_knowledge_need, _retrieve_knowledge, _decide_action remain usable.
    ticket = _ticket("Refund request", "I want a refund.")

    assessment = await agent_workflow_service._assess_knowledge_need(
        {"ticket": ticket, "workflow_path": ["load_ticket"]}
    )
    assert assessment["workflow_path"] == ["load_ticket", "assess_knowledge_need"]

    async def _search(db: Any, *, organization_id, query, limit):
        return _matches()

    monkeypatch.setattr(KnowledgeSearchService, "search", _search)
    retrieved = await agent_workflow_service._retrieve_knowledge(
        {"ticket": ticket, "workflow_path": ["load_ticket"]},
        db=_FAKE_DB,
    )
    assert retrieved["workflow_path"] == [
        "load_ticket",
        "retrieve_knowledge",
        "knowledge_specialist",
    ]

    fake_llm = _FakeStructuredLLM(_respond_parsed())
    monkeypatch.setattr(agent_workflow_service, "decision_llm", fake_llm)
    decided = await agent_workflow_service._decide_action(
        {
            "ticket": ticket,
            "workflow_path": ["load_ticket", "assess_knowledge_need", "coordinator"],
            "sources": filter_and_format_sources(_matches()),
            "needs_knowledge": True,
            "fast_path_action": None,
            "customer_context": None,
            "conversation_context": None,
        }
    )
    assert decided["workflow_path"][-2:] == ["decide_action", "action_specialist"]
    assert decided["decision"]["action"] == "respond"


@pytest.mark.asyncio
async def test_coordinator_routing_source_is_still_needs_knowledge(monkeypatch):
    ticket = _ticket("Refund request", "I want a refund.")
    fake_llm = _FakeStructuredLLM(_respond_parsed())
    result = await _run_flow(monkeypatch, ticket=ticket, fake_llm=fake_llm)

    # intent is recorded metadata; the flow branched on needs_knowledge.
    assert result["intent"] is not None
    assert "retrieve_knowledge" in result["workflow_path"]