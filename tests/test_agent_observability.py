"""Phase 1K.2 observability tests.

Covers the workflow-version bump contract, the Coordinator-intent
observability surfaced on ``decision_observability``, and the specialist /
handoff metrics recorded in the REAL compiled workflow. No OpenAI and no
database: the ticket is injected and the LLM / knowledge search are fakes.
"""

import os
from types import SimpleNamespace
from typing import Any

import pytest

os.environ["ENVIRONMENT"] = "development"

from app.schemas.agent import AgentAnalysisResponse
from app.services.agent_workflow_service import (
    AGENT_WORKFLOW_VERSION,
    agent_workflow_service,
)
from app.services.knowledge_search_service import KnowledgeSearchService

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
            # "respond" + a tool plan is built downstream by real code, so the
            # reply draft alone is enough for a normal-path run.
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


async def _run_flow(monkeypatch, *, ticket: dict) -> dict:
    async def _load(state: dict, *, db: Any) -> dict:
        return {
            "ticket": ticket,
            "workflow_path": [*state["workflow_path"], "load_ticket"],
        }

    monkeypatch.setattr(agent_workflow_service, "_load_ticket", _load)
    monkeypatch.setattr(agent_workflow_service, "decision_llm", _FakeStructuredLLM())

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


# ---------------------------------------------------------------------------
# Workflow version contract


def test_workflow_version_bumped_for_2b_routing():
    """Phase 1K.2B changed real routing, so v1 analyses must not be reused."""
    assert AGENT_WORKFLOW_VERSION == "2"


def test_analysis_response_exposes_coordinator_intent():
    payload = AgentAnalysisResponse(
        run_id="r-1",
        ticket_id=1,
        decision={"action": "respond", "reason": "test"},
        sources=[],
        workflow_path=[],
        tool_plan=[],
        auto_queued=False,
        coordinator_intent="action",
    )
    assert payload.coordinator_intent == "action"

    omitted = AgentAnalysisResponse(
        run_id="r-2",
        ticket_id=2,
        decision={"action": "no_action", "reason": "test"},
        sources=[],
        workflow_path=[],
        tool_plan=[],
    )
    assert omitted.coordinator_intent is None


# ---------------------------------------------------------------------------
# Coordinator-intent observability on decision_observability


@pytest.mark.asyncio
async def test_fast_path_observability_records_intent_none(monkeypatch):
    result = await _run_flow(monkeypatch, ticket=_ticket("Thank you", "All good now."))

    assert result["decision"]["action"] == "no_action"
    ob = result["decision_observability"]
    assert ob["coordinator_intent"] == "none"
    assert ob["llm_called"] is False


@pytest.mark.asyncio
async def test_action_flow_observability_records_action_intent(monkeypatch):
    result = await _run_flow(
        monkeypatch,
        ticket=_ticket("Repair checkout", "Please repair the checkout flow on my store."),
    )

    assert result["intent"] == "action"
    ob = result["decision_observability"]
    assert ob["coordinator_intent"] == "action"
    assert "retrieve_knowledge" not in result["workflow_path"]


@pytest.mark.asyncio
async def test_information_flow_observability_records_information_intent(monkeypatch):
    result = await _run_flow(
        monkeypatch,
        ticket=_ticket("Refund question", "What is the refund policy?"),
    )

    assert result["intent"] == "information"
    ob = result["decision_observability"]
    assert ob["coordinator_intent"] == "information"
    assert "retrieve_knowledge" in result["workflow_path"]
    assert ob["llm_called"] is True


# ---------------------------------------------------------------------------
# Specialist + handoff metrics in the real compiled flow


async def _run_flow_with_metric_capture(monkeypatch, *, ticket: dict) -> dict:
    import app.services.agent_workflow_service as service

    captured = {
        "specialists": [],
        "handoffs": [],
    }

    def _capture_specialist(*, specialist: str) -> None:
        captured["specialists"].append(specialist)

    def _capture_handoff(*, from_specialist: str, to_specialist: str) -> None:
        captured["handoffs"].append((from_specialist, to_specialist))

    monkeypatch.setattr(service, "record_agent_specialist_selected", _capture_specialist)
    monkeypatch.setattr(service, "record_agent_handoff", _capture_handoff)

    result = await _run_flow(monkeypatch, ticket=ticket)

    result["_captured_specialists"] = captured["specialists"]
    result["_captured_handoffs"] = captured["handoffs"]
    return result


@pytest.mark.asyncio
async def test_information_flow_metrics_full_chain(monkeypatch):
    result = await _run_flow_with_metric_capture(
        monkeypatch,
        ticket=_ticket("Refund question", "What is the refund policy?"),
    )

    assert result["_captured_specialists"] == [
        "coordinator",
        "knowledge",
        "action",
    ]
    assert result["_captured_handoffs"] == [
        ("coordinator", "knowledge"),
        ("knowledge", "action"),
    ]


@pytest.mark.asyncio
async def test_action_flow_metrics_skip_knowledge(monkeypatch):
    result = await _run_flow_with_metric_capture(
        monkeypatch,
        ticket=_ticket("Repair checkout", "Please repair the checkout flow on my store."),
    )

    # Phase 1K.2B: pure action → coordinator hands off straight to action.
    assert result["_captured_specialists"] == [
        "coordinator",
        "action",
    ]
    assert result["_captured_handoffs"] == [
        ("coordinator", "action"),
    ]


@pytest.mark.asyncio
async def test_fast_path_metrics_no_knowledge(monkeypatch):
    result = await _run_flow_with_metric_capture(
        monkeypatch,
        ticket=_ticket("Thank you", "All good now."),
    )

    assert result["_captured_specialists"] == [
        "coordinator",
        "action",
    ]
    assert result["_captured_handoffs"] == [
        ("coordinator", "action"),
    ]
    assert ("coordinator", "knowledge") not in result["_captured_handoffs"]


@pytest.mark.asyncio
async def test_router_pure_function_unaffected_by_metrics(monkeypatch):
    """The router itself never emits metrics — only the real flow wrapper does."""
    import app.services.agent_workflow_service as service

    calls = []
    monkeypatch.setattr(
        service,
        "record_agent_handoff",
        lambda **kw: calls.append(kw),
    )
    service.AgentWorkflowService._route_after_coordinator(
        {"intent": "action", "needs_knowledge": True}
    )
    assert calls == []