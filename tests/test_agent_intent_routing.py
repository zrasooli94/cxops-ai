"""Intent-aware routing tests (Phase 1K.2A + 1K.2B).

``_route_after_coordinator`` reads the Coordinator's ``intent`` and maps it:
``none`` → action, ``information``/``mixed`` → knowledge, ``action`` →
action (Phase 1K.2B: pure action requests skip the Knowledge Specialist),
and missing/unknown intent falls back to the legacy ``needs_knowledge``
router. Intent never gates new LLM calls and never selects a tenant.

These are pure unit tests plus REAL compiled-workflow runs with faked
DB/LLM/knowledge-search — no OpenAI, no database.
"""

import os
from types import SimpleNamespace
from typing import Any

import pytest

os.environ["ENVIRONMENT"] = "development"

from app.services.agent_workflow_service import (
    AgentWorkflowService,
    agent_workflow_service,
)
from app.services.knowledge_search_service import KnowledgeSearchService
from app.services.tool_authorization_service import ToolAuthorizationService

TENANT_A = 111

_FAKE_DB = object()


def _state(
    *,
    needs_knowledge: bool,
    fast_path_action: str | None = None,
    intent: Any = None,
) -> dict:
    state: dict[str, Any] = {
        "needs_knowledge": needs_knowledge,
        "fast_path_action": fast_path_action,
        "organization_id": TENANT_A,
    }
    if intent is not None:
        state["intent"] = intent
    return state


def _legacy(state: dict) -> str:
    return AgentWorkflowService._route_after_assessment(state)


def _coordinator(state: dict) -> str:
    return AgentWorkflowService._route_after_coordinator(state)


# Representative states derived from the real Coordinator output space plus
# legacy pre-Phase 1K states. Each row: (label, state, coordinator_expected,
# legacy_expected). Since Phase 1K.2B ``action`` + needs_knowledge diverges
# intentionally: new → ``decide_action``, legacy → ``retrieve_knowledge``.
_CASES: list[tuple[str, dict, str, str]] = [
    (
        "acknowledgement fast path",
        _state(needs_knowledge=False, fast_path_action="no_action", intent="none"),
        "decide_action",
        "decide_action",
    ),
    (
        "record-only fast path",
        _state(
            needs_knowledge=False,
            fast_path_action="internal_note",
            intent="none",
        ),
        "decide_action",
        "decide_action",
    ),
    (
        "information intent",
        _state(needs_knowledge=True, intent="information"),
        "retrieve_knowledge",
        "retrieve_knowledge",
    ),
    (
        "mixed intent",
        _state(needs_knowledge=True, intent="mixed"),
        "retrieve_knowledge",
        "retrieve_knowledge",
    ),
    (
        "action intent + needs_knowledge (2B change)",
        _state(needs_knowledge=True, intent="action"),
        "decide_action",
        "retrieve_knowledge",
    ),
    (
        "action intent - no knowledge",
        _state(needs_knowledge=False, intent="action"),
        "decide_action",
        "decide_action",
    ),
    (
        "missing intent + needs_knowledge",
        _state(needs_knowledge=True),
        "retrieve_knowledge",
        "retrieve_knowledge",
    ),
    (
        "missing intent + no knowledge",
        _state(needs_knowledge=False),
        "decide_action",
        "decide_action",
    ),
    (
        "legacy fast path, no intent",
        _state(needs_knowledge=False, fast_path_action="no_action"),
        "decide_action",
        "decide_action",
    ),
    (
        "invalid intent string",
        _state(needs_knowledge=True, intent="banana"),
        "retrieve_knowledge",
        "retrieve_knowledge",
    ),
    (
        "None intent key present",
        _state(needs_knowledge=False, intent=None),
        "decide_action",
        "decide_action",
    ),
]


@pytest.mark.parametrize(
    "label,state,coord_expected,legacy_expected",
    _CASES,
    ids=[c[0] for c in _CASES],
)
def test_intent_router_behavior_matrix(label, state, coord_expected, legacy_expected):
    assert _coordinator(state) == coord_expected
    assert _legacy(state) == legacy_expected
    # Behavior invariant holds everywhere except the intentional 1K.2B change
    # (action + needs_knowledge), which is covered separately below.
    if label != "action intent + needs_knowledge (2B change)":
        assert _coordinator(state) == _legacy(state)


@pytest.mark.asyncio
async def test_none_intent_preserves_old_fast_path_route():
    state = _state(needs_knowledge=False, fast_path_action="no_action", intent="none")
    assert _coordinator(state) == _legacy(state) == "decide_action"


@pytest.mark.asyncio
async def test_information_intent_routes_to_knowledge():
    state = _state(needs_knowledge=True, intent="information")
    assert _coordinator(state) == "retrieve_knowledge"


@pytest.mark.asyncio
async def test_mixed_intent_routes_to_knowledge():
    state = _state(needs_knowledge=True, intent="mixed")
    assert _coordinator(state) == "retrieve_knowledge"


@pytest.mark.asyncio
async def test_action_intent_routes_to_action_even_with_knowledge_need():
    """Phase 1K.2B: an action intent always skips knowledge — the change."""
    state = _state(needs_knowledge=True, intent="action")
    assert _coordinator(state) == "decide_action"


@pytest.mark.asyncio
async def test_action_intent_without_knowledge_need_routes_to_action():
    state = _state(needs_knowledge=False, intent="action")
    assert _coordinator(state) == "decide_action"


def test_missing_intent_falls_back_to_needs_knowledge():
    assert _coordinator(_state(needs_knowledge=True)) == "retrieve_knowledge"
    assert _coordinator(_state(needs_knowledge=False)) == "decide_action"


def test_invalid_intent_falls_back_safely():
    bad = _state(needs_knowledge=True, intent="banana")
    assert _coordinator(bad) == _legacy(bad) == "retrieve_knowledge"


def test_old_router_compatibility_alias_unchanged():
    assert AgentWorkflowService._route_after_assessment(
        _state(needs_knowledge=True)
    ) == "retrieve_knowledge"
    assert AgentWorkflowService._route_after_assessment(
        _state(needs_knowledge=False)
    ) == "decide_action"


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


class _FakeStructuredLLM:
    def __init__(self) -> None:
        self.calls: list[list[Any]] = []

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
            "parsed": {
                "action": "respond",
                "reason": "Draft a safe customer-facing refund reply.",
                "recommended_team": None,
                "recommended_priority": None,
                "response_draft": "Thanks for contacting us.",
                "requires_human_approval": True,
            },
            "parsing_error": None,
        }


async def _run_flow(
    monkeypatch,
    *,
    ticket: dict,
    fake_llm: Any = None,
    on_search: Any = None,
) -> dict:
    async def _load(state: dict, *, db: Any) -> dict:
        return {
            "ticket": ticket,
            "workflow_path": [*state["workflow_path"], "load_ticket"],
        }

    monkeypatch.setattr(agent_workflow_service, "_load_ticket", _load)

    if fake_llm is not None:
        monkeypatch.setattr(agent_workflow_service, "decision_llm", fake_llm)

    async def _search(db: Any, *, organization_id, query, limit) -> list[dict]:
        if on_search is not None:
            return await on_search(
                db,
                organization_id=organization_id,
                query=query,
                limit=limit,
            )
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


@pytest.mark.asyncio
async def test_graph_conditional_edge_uses_intent_router(monkeypatch):
    calls: list[Any] = []
    original = AgentWorkflowService._route_after_coordinator

    def wrapped(state: dict, _config=None) -> str:
        calls.append(state.get("intent"))
        return original(state)

    monkeypatch.setattr(agent_workflow_service, "_route_after_coordinator", wrapped)

    ticket = _ticket("Refund request", "I want a refund.")
    result = await _run_flow(
        monkeypatch,
        ticket=ticket,
        fake_llm=_FakeStructuredLLM(),
    )

    # The coordinator node ran, then the new router dispatched exactly once.
    assert len(calls) == 1
    assert calls[0] == "mixed"
    assert "retrieve_knowledge" in result["workflow_path"]


@pytest.mark.asyncio
async def test_fast_path_zero_llm_and_no_search(monkeypatch):
    fake_llm = _FakeStructuredLLM()

    async def _search(db: Any, *, organization_id, query, limit):
        raise AssertionError("Fast path must never hit knowledge search")

    monkeypatch.setattr(KnowledgeSearchService, "search", _search)

    ticket = _ticket("Thank you", "All good now.")
    result = await _run_flow(monkeypatch, ticket=ticket, fake_llm=fake_llm)

    assert fake_llm.calls == []
    assert result["decision"]["action"] == "no_action"
    assert "retrieve_knowledge" not in result["workflow_path"]
    assert "knowledge_specialist" not in result["workflow_path"]


@pytest.mark.asyncio
async def test_normal_path_still_one_llm_call(monkeypatch):
    fake_llm = _FakeStructuredLLM()
    ticket = _ticket("Refund request", "I want a refund.")
    result = await _run_flow(monkeypatch, ticket=ticket, fake_llm=fake_llm)

    assert len(fake_llm.calls) == 1
    assert result["decision"]["action"] == "respond"


@pytest.mark.asyncio
async def test_tenant_state_unchanged_after_routing(monkeypatch):
    ticket = _ticket("Refund request", "I want a refund.")
    result = await _run_flow(
        monkeypatch,
        ticket=ticket,
        fake_llm=_FakeStructuredLLM(),
    )

    assert result["organization_id"] == TENANT_A
    assert result["ticket"]["organization_id"] == TENANT_A
    assert result["intent"] == "mixed"


@pytest.mark.asyncio
async def test_action_intent_skips_knowledge_but_runs_full_action_chain(monkeypatch):
    """Phase 1K.2B end-to-end: pure action → coordinator → Action Specialist.

    The Knowledge Specialist is skipped entirely (no KnowledgeSearchService
    call, no ``retrieve_knowledge``/``knowledge_specialist`` workflow markers)
    while the Action Specialist, tool plan, and tenant state all survive.
    """
    fake_llm = _FakeStructuredLLM()
    search_calls: list[str] = []

    async def _no_search(db, *, organization_id, query, limit):
        search_calls.append(query)
        raise AssertionError("Action intent must skip KnowledgeSearchService")

    ticket = _ticket("Call me back", "Can you please call me back?")
    result = await _run_flow(
        monkeypatch,
        ticket=ticket,
        fake_llm=fake_llm,
        on_search=_no_search,
    )

    assert result["intent"] == "action"
    assert search_calls == []

    for node in (
        "assess_knowledge_need",
        "coordinator",
        "decide_action",
        "action_specialist",
        "build_tool_plan",
    ):
        assert node in result["workflow_path"]
    assert "retrieve_knowledge" not in result["workflow_path"]
    assert "knowledge_specialist" not in result["workflow_path"]

    # The Action Specialist still ran, with exactly its one existing LLM call.
    assert len(fake_llm.calls) == 1
    assert result["decision"]["action"] == "respond"

    # Tool safety is NOT skipped: the plan is built and authorized.
    assert result["tool_plan"] != []
    assert all("authorized" in tool for tool in result["tool_plan"])
    assert "zendesk.send_reply" in [tool["tool"] for tool in result["tool_plan"]]

    # No retrieval artifacts; routing never touches the tenant.
    assert result["sources"] == []
    assert result["organization_id"] == TENANT_A
    assert result["ticket"]["organization_id"] == TENANT_A


@pytest.mark.asyncio
async def test_action_flow_still_runs_tool_authorization(monkeypatch):
    original = ToolAuthorizationService.authorize_plan.__func__
    calls: list[int] = []

    def wrapped(cls, tools):
        calls.append(len(tools))
        return original(cls, tools)

    monkeypatch.setattr(
        ToolAuthorizationService,
        "authorize_plan",
        classmethod(wrapped),
    )

    ticket = _ticket("Call me back", "Can you please call me back?")
    result = await _run_flow(
        monkeypatch,
        ticket=ticket,
        fake_llm=_FakeStructuredLLM(),
    )

    assert len(calls) == 1
    assert result["tool_plan"] != []


@pytest.mark.asyncio
async def test_information_intent_still_retrieves_knowledge(monkeypatch):
    fake_llm = _FakeStructuredLLM()
    search_calls: list[tuple[int, str]] = []

    async def _spy_search(db, *, organization_id, query, limit):
        search_calls.append((organization_id, query))
        return _matches()

    ticket = _ticket("Refund policy", "What is the refund policy?")
    result = await _run_flow(
        monkeypatch,
        ticket=ticket,
        fake_llm=fake_llm,
        on_search=_spy_search,
    )

    assert result["intent"] == "information"
    assert len(search_calls) == 1
    assert search_calls[0][0] == TENANT_A
    assert "retrieve_knowledge" in result["workflow_path"]
    assert "knowledge_specialist" in result["workflow_path"]
    assert result["sources"] != []
    assert len(fake_llm.calls) == 1
    assert result["decision"]["action"] == "respond"


@pytest.mark.asyncio
async def test_mixed_intent_still_retrieves_knowledge(monkeypatch):
    fake_llm = _FakeStructuredLLM()
    search_calls: list[str] = []

    async def _spy_search(db, *, organization_id, query, limit):
        search_calls.append(query)
        return _matches()

    ticket = _ticket("Refund request", "I want a refund.")
    result = await _run_flow(
        monkeypatch,
        ticket=ticket,
        fake_llm=fake_llm,
        on_search=_spy_search,
    )

    assert result["intent"] == "mixed"
    assert len(search_calls) == 1
    assert "retrieve_knowledge" in result["workflow_path"]
    assert "knowledge_specialist" in result["workflow_path"]
    assert result["sources"] != []
    assert len(fake_llm.calls) == 1


@pytest.mark.asyncio
async def test_none_intent_fast_path_skips_search_and_llm(monkeypatch):
    fake_llm = _FakeStructuredLLM()

    async def _no_search(db, *, organization_id, query, limit):
        raise AssertionError("Fast path must never hit knowledge search")

    ticket = _ticket("Thank you", "All good now.")
    result = await _run_flow(
        monkeypatch,
        ticket=ticket,
        fake_llm=fake_llm,
        on_search=_no_search,
    )

    assert result["intent"] == "none"
    assert fake_llm.calls == []
    assert result["decision"]["action"] == "no_action"
    assert "retrieve_knowledge" not in result["workflow_path"]
    assert "knowledge_specialist" not in result["workflow_path"]