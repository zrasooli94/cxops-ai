"""Phase 1K.3 failure-safety + specialist-path tests.

Covers the bounded specialist-failure metric, the fail-closed invariants of
the three specialists in the REAL compiled workflow (a failed Coordinator /
Knowledge / Action specialist must stop downstream work), the truthful
``knowledge → action`` handoff (recorded only after successful retrieval),
and the pure ``derive_specialist_path`` / ``validate_specialist_path``
helpers used by observability and Phase 1J evaluation. No OpenAI and no
database: the ticket is injected and the LLM / knowledge search are fakes.
"""

import os
from types import SimpleNamespace
from typing import Any

import pytest

os.environ["ENVIRONMENT"] = "development"

from app.schemas.agent import AgentAnalysisResponse
from app.services.agent_evaluation_service import AgentEvaluationService
from app.services.agent_workflow_service import (
    agent_workflow_service,
    derive_specialist_path,
    validate_specialist_path,
)
from app.services.knowledge_search_service import KnowledgeSearchService
from app.services.tool_authorization_service import ToolAuthorizationService

TENANT_A = 111

_FAKE_DB = object()

_ALLOWED_SPECIALISTS = {"coordinator", "knowledge", "action"}


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


class _RaisingLLM:
    async def ainvoke(self, messages: list[Any], **_kwargs) -> dict:
        raise RuntimeError("simulated decision model failure")


async def _build_run(monkeypatch, *, ticket: dict) -> list[dict]:
    """Compile the real workflow with spies installed; returns the call log."""

    import app.services.agent_workflow_service as service

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

    calls: dict[str, list[Any]] = {
        "specialists": [],
        "handoffs": [],
        "failures": [],
        "authors": [],
    }
    original = {
        "coordinator": agent_workflow_service._run_coordinator,
        "knowledge": agent_workflow_service._run_knowledge_specialist,
        "action": agent_workflow_service._run_action_specialist,
        "build_tool_plan": agent_workflow_service._build_tool_plan,
    }

    async def coordinator(state: dict) -> dict:
        calls["specialists"].append("coordinator")
        return await original["coordinator"](state)

    async def knowledge(state: dict, *, db: Any) -> dict:
        calls["specialists"].append("knowledge")
        return await original["knowledge"](state, db=db)

    async def action(state: dict) -> dict:
        calls["specialists"].append("action")
        return await original["action"](state)

    async def build_tool_plan(state: dict) -> dict:
        calls["specialists"].append("build_tool_plan")
        return await original["build_tool_plan"](state)

    def _capture_specialist(*, specialist: str) -> None:
        calls["specialists"].append(f"selected:{specialist}")

    def _capture_handoff(*, from_specialist: str, to_specialist: str) -> None:
        calls["handoffs"].append((from_specialist, to_specialist))

    def _capture_failure(*, specialist: str) -> None:
        calls["failures"].append(specialist)

    def _count_authorize(tools: list[dict]) -> list[dict]:
        calls["authors"].append(tools)
        return tools

    monkeypatch.setattr(agent_workflow_service, "_run_coordinator", coordinator)
    monkeypatch.setattr(
        agent_workflow_service,
        "_run_knowledge_specialist",
        knowledge,
    )
    monkeypatch.setattr(agent_workflow_service, "_run_action_specialist", action)
    monkeypatch.setattr(agent_workflow_service, "_build_tool_plan", build_tool_plan)
    monkeypatch.setattr(service, "record_agent_specialist_selected", _capture_specialist)
    monkeypatch.setattr(service, "record_agent_handoff", _capture_handoff)
    monkeypatch.setattr(service, "record_agent_specialist_failure", _capture_failure)
    monkeypatch.setattr(ToolAuthorizationService, "authorize_plan", _count_authorize)

    workflow = agent_workflow_service._build_workflow(_FAKE_DB)

    result = await workflow.ainvoke(
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

    calls["result"] = [result]
    return calls


# ---------------------------------------------------------------------------
# derive_specialist_path — pure helper

_MARKED_ACTION = [
    "assess_knowledge_need",
    "coordinator",
    "decide_action",
    "action_specialist",
    "build_tool_plan",
]

_MARKED_CORO = [
    "assess_knowledge_need",
    "coordinator",
    "retrieve_knowledge",
    "knowledge_specialist",
    "decide_action",
    "action_specialist",
    "build_tool_plan",
]


def test_derive_specialist_path_action_flow():
    assert derive_specialist_path(_MARKED_ACTION) == [
        "coordinator",
        "action",
    ]


def test_derive_specialist_path_knowledge_flow():
    assert derive_specialist_path(_MARKED_CORO) == [
        "coordinator",
        "knowledge",
        "action",
    ]


def test_derive_specialist_path_collapses_consecutive_duplicates():
    assert derive_specialist_path(
        [
            "coordinator",
            "coordinator",
            "decide_action",
            "action_specialist",
            "action_specialist",
        ]
    ) == [
        "coordinator",
        "action",
    ]


def test_derive_specialist_path_ignores_non_specialist_steps():
    assert derive_specialist_path(["load_ticket", "assess_knowledge_need", "build_tool_plan"]) == []


def test_derive_specialist_path_never_invents_specialists():
    assert derive_specialist_path(["coordinator", "consultant", "decide_action"]) == [
        "coordinator",
        "action",
    ]


# ---------------------------------------------------------------------------
# validate_specialist_path — pure helper


def test_validate_information_path_valid():
    valid, issues = validate_specialist_path(_MARKED_CORO, intent="mixed")
    assert valid is True
    assert issues == []


def test_validate_action_path_valid():
    valid, issues = validate_specialist_path(_MARKED_ACTION, intent="action")
    assert valid is True
    assert issues == []


def test_validate_fast_path_valid_with_none_intent():
    valid, issues = validate_specialist_path(_MARKED_ACTION, intent="none")
    assert valid is True
    assert issues == []


def test_validate_legacy_action_path_with_knowledge_is_allowed():
    "action containing knowledge is NOT flagged: indistinguishable from "
    "an information run once the coordinator read is gone."

    legacy = [
        "coordinator",
        "retrieve_knowledge",
        "decide_action",
        "action_specialist",
    ]
    valid, issues = validate_specialist_path(legacy, intent="action")
    assert valid is True
    assert issues == []


def test_validate_empty_path_invalid():
    valid, issues = validate_specialist_path([])
    assert valid is False
    assert issues == ["no specialist markers recorded for a successful run"]


def test_validate_missing_action_invalid():
    valid, issues = validate_specialist_path(
        [
            "coordinator",
            "retrieve_knowledge",
            "knowledge_specialist",
        ]
    )
    assert valid is False
    assert "the run never reached the action specialist" in issues


def test_validate_information_without_knowledge_invalid():
    valid, issues = validate_specialist_path(_MARKED_ACTION, intent="information")
    assert valid is False
    assert "information/mixed request completed without the knowledge specialist" in issues


def test_validate_knowledge_after_action_invalid():
    valid, issues = validate_specialist_path(
        [
            "coordinator",
            "decide_action",
            "action_specialist",
            "knowledge_specialist",
        ]
    )
    assert valid is False
    assert "knowledge specialist is marked after the action specialist" in issues


def test_validate_duplicate_specialist_invalid():
    valid, issues = validate_specialist_path(
        [
            "coordinator",
            "retrieve_knowledge",
            "knowledge_specialist",
            "decide_action",
            "action_specialist",
            "knowledge_specialist",
        ]
    )
    assert valid is False
    assert "duplicate specialist marker: knowledge" in issues


# ---------------------------------------------------------------------------
# AgentAnalysisResponse specialist-path fields


def test_analysis_response_exposes_specialist_path():
    payload = AgentAnalysisResponse(
        run_id="r-1",
        ticket_id=1,
        decision={"action": "respond", "reason": "test"},
        sources=[],
        workflow_path=[],
        tool_plan=[],
        auto_queued=False,
        specialist_path=["coordinator", "action"],
        specialist_path_valid=True,
        specialist_path_issues=[],
    )
    assert payload.specialist_path == ["coordinator", "action"]
    assert payload.specialist_path_valid is True

    omitted = AgentAnalysisResponse(
        run_id="r-2",
        ticket_id=2,
        decision={"action": "no_action", "reason": "test"},
        sources=[],
        workflow_path=[],
        tool_plan=[],
    )
    assert omitted.specialist_path == []
    assert omitted.specialist_path_valid is True
    assert omitted.specialist_path_issues == []


# ---------------------------------------------------------------------------
# Failure safety — success baselines


@pytest.mark.asyncio
async def test_successful_information_flow_records_one_handoff_after_retrieval(monkeypatch):
    calls = await _build_run(
        monkeypatch,
        ticket=_ticket("Refund question", "What is the refund policy?"),
    )

    assert calls["failures"] == []
    assert calls["handoffs"] == [
        ("coordinator", "knowledge"),
        ("knowledge", "action"),
    ]
    assert calls["authors"] != []
    assert calls["specialists"].count("coordinator") == 1
    assert calls["specialists"].count("knowledge") == 1
    assert calls["specialists"].count("action") == 1


@pytest.mark.asyncio
async def test_empty_knowledge_result_is_not_a_failure(monkeypatch):
    import app.services.agent_workflow_service as service

    async def _empty_search(db: Any, *, organization_id, query, limit) -> list[dict]:
        return []

    async def _load(state: dict, *, db: Any) -> dict:
        return {
            "ticket": _ticket("Refund question", "What is the refund policy?"),
            "workflow_path": [*state["workflow_path"], "load_ticket"],
        }

    monkeypatch.setattr(agent_workflow_service, "_load_ticket", _load)
    monkeypatch.setattr(agent_workflow_service, "decision_llm", _FakeStructuredLLM())
    monkeypatch.setattr(KnowledgeSearchService, "search", _empty_search)
    monkeypatch.setattr(service, "record_agent_specialist_failure", lambda **_: None)

    workflow = agent_workflow_service._build_workflow(_FAKE_DB)
    result = await workflow.ainvoke(
        {
            "ticket_id": 1,
            "organization_id": TENANT_A,
            "workflow_path": [],
            "sources": [],
            "tool_plan": [],
            "customer_context": None,
            "conversation_context": None,
        }
    )
    assert result["decision"]["action"] == "respond"
    assert result["sources"] == []


# ---------------------------------------------------------------------------
# Failure safety — Knowledge Specialist

_KNOWLEDGE_ACTION_TICKET = _ticket("Refund question", "What is the refund policy?")


@pytest.mark.asyncio
async def test_knowledge_failure_stops_workflow_and_records_metric(monkeypatch):
    import app.services.agent_workflow_service as service

    async def _raises(db: Any, *, organization_id, query, limit) -> list[dict]:
        raise RuntimeError("simulated knowledge retrieval failure")

    async def _action(state: dict) -> dict:
        pytest.fail("Action Specialist must not run after a knowledge failure")
        return state  # pragma: no cover

    async def _load(state: dict, *, db: Any) -> dict:
        return {
            "ticket": _KNOWLEDGE_ACTION_TICKET,
            "workflow_path": [*state["workflow_path"], "load_ticket"],
        }

    failures: list[str] = []
    monkeypatch.setattr(agent_workflow_service, "_load_ticket", _load)
    monkeypatch.setattr(agent_workflow_service, "decision_llm", _FakeStructuredLLM())
    monkeypatch.setattr(KnowledgeSearchService, "search", _raises)
    monkeypatch.setattr(agent_workflow_service, "_run_action_specialist", _action)
    monkeypatch.setattr(
        service,
        "record_agent_specialist_failure",
        lambda **kw: failures.append(kw["specialist"]),
    )

    workflow = agent_workflow_service._build_workflow(_FAKE_DB)

    with pytest.raises(RuntimeError, match="simulated knowledge retrieval failure"):
        await workflow.ainvoke(
            {
                "ticket_id": 1,
                "organization_id": TENANT_A,
                "workflow_path": [],
                "sources": [],
                "tool_plan": [],
                "customer_context": None,
                "conversation_context": None,
            }
        )

    assert failures == ["knowledge"]
    assert all(f in _ALLOWED_SPECIALISTS for f in failures)


@pytest.mark.asyncio
async def test_knowledge_failure_records_no_knowledge_action_handoff(monkeypatch):
    import app.services.agent_workflow_service as service

    async def _raises(db: Any, *, organization_id, query, limit) -> list[dict]:
        raise RuntimeError("simulated knowledge retrieval failure")

    async def _load(state: dict, *, db: Any) -> dict:
        return {
            "ticket": _KNOWLEDGE_ACTION_TICKET,
            "workflow_path": [*state["workflow_path"], "load_ticket"],
        }

    handoffs: list[tuple[str, str]] = []
    monkeypatch.setattr(agent_workflow_service, "_load_ticket", _load)
    monkeypatch.setattr(KnowledgeSearchService, "search", _raises)
    monkeypatch.setattr(
        service,
        "record_agent_handoff",
        lambda **kw: handoffs.append((kw["from_specialist"], kw["to_specialist"])),
    )

    workflow = agent_workflow_service._build_workflow(_FAKE_DB)

    with pytest.raises(RuntimeError):
        await workflow.ainvoke(
            {
                "ticket_id": 1,
                "organization_id": TENANT_A,
                "workflow_path": [],
                "sources": [],
                "tool_plan": [],
                "customer_context": None,
                "conversation_context": None,
            }
        )

    # The router DID decide coordinator → knowledge; only the handoff that
    # never actually happened (knowledge → action) is absent.
    assert handoffs == [("coordinator", "knowledge")]


# ---------------------------------------------------------------------------
# Failure safety — Action Specialist


@pytest.mark.asyncio
async def test_action_failure_stops_tool_plan_and_records_metric(monkeypatch):
    import app.services.agent_workflow_service as service

    async def _load(state: dict, *, db: Any) -> dict:
        return {
            "ticket": _ticket("Repair checkout", "Please repair the checkout flow on my store."),
            "workflow_path": [*state["workflow_path"], "load_ticket"],
        }

    authorized: list[list[dict]] = []
    failures: list[str] = []

    monkeypatch.setattr(agent_workflow_service, "_load_ticket", _load)
    monkeypatch.setattr(agent_workflow_service, "decision_llm", _RaisingLLM())
    monkeypatch.setattr(
        service,
        "record_agent_specialist_failure",
        lambda **kw: failures.append(kw["specialist"]),
    )
    monkeypatch.setattr(
        ToolAuthorizationService,
        "authorize_plan",
        lambda tools: authorized.append(tools) or tools,
    )

    workflow = agent_workflow_service._build_workflow(_FAKE_DB)

    with pytest.raises(RuntimeError, match="simulated decision model failure"):
        await workflow.ainvoke(
            {
                "ticket_id": 1,
                "organization_id": TENANT_A,
                "workflow_path": [],
                "sources": [],
                "tool_plan": [],
                "customer_context": None,
                "conversation_context": None,
            }
        )

    assert failures == ["action"]
    assert authorized == []


@pytest.mark.asyncio
async def test_action_failure_still_records_coordinator_action_handoff(monkeypatch):
    import app.services.agent_workflow_service as service

    async def _load(state: dict, *, db: Any) -> dict:
        return {
            "ticket": _ticket("Repair checkout", "Please repair the checkout flow on my store."),
            "workflow_path": [*state["workflow_path"], "load_ticket"],
        }

    handoffs: list[tuple[str, str]] = []
    monkeypatch.setattr(agent_workflow_service, "_load_ticket", _load)
    monkeypatch.setattr(agent_workflow_service, "decision_llm", _RaisingLLM())
    monkeypatch.setattr(
        service,
        "record_agent_handoff",
        lambda **kw: handoffs.append((kw["from_specialist"], kw["to_specialist"])),
    )
    monkeypatch.setattr(service, "record_agent_specialist_failure", lambda **_: None)

    workflow = agent_workflow_service._build_workflow(_FAKE_DB)

    with pytest.raises(RuntimeError):
        await workflow.ainvoke(
            {
                "ticket_id": 1,
                "organization_id": TENANT_A,
                "workflow_path": [],
                "sources": [],
                "tool_plan": [],
                "customer_context": None,
                "conversation_context": None,
            }
        )

    assert handoffs == [("coordinator", "action")]


@pytest.mark.asyncio
async def test_action_failure_no_execution_or_approval_path(monkeypatch):
    """A failed Action Specialist must never reach execution/approval code.

    The compiled workflow raises before returning, so the post-workflow
    section of ``_analyze_critical_section`` (run persist, auto-approval,
    execution queue) is unreachable. The smallest boundary that proves this
    without a database is: the graph raises, the tool-plan authorizer is
    never consulted, and no decision is produced.
    """

    import app.services.agent_workflow_service as service

    async def _load(state: dict, *, db: Any) -> dict:
        return {
            "ticket": _ticket("Repair checkout", "Please repair the checkout flow on my store."),
            "workflow_path": [*state["workflow_path"], "load_ticket"],
        }

    monkeypatch.setattr(agent_workflow_service, "_load_ticket", _load)
    monkeypatch.setattr(agent_workflow_service, "decision_llm", _RaisingLLM())
    monkeypatch.setattr(service, "record_agent_specialist_failure", lambda **_: None)
    monkeypatch.setattr(
        ToolAuthorizationService,
        "authorize_plan",
        lambda tools: pytest.fail("authorize_plan must not run after an action failure"),
    )

    workflow = agent_workflow_service._build_workflow(_FAKE_DB)

    with pytest.raises(RuntimeError, match="simulated decision model failure"):
        await workflow.ainvoke(
            {
                "ticket_id": 1,
                "organization_id": TENANT_A,
                "workflow_path": [],
                "sources": [],
                "tool_plan": [],
                "customer_context": None,
                "conversation_context": None,
            }
        )


# ---------------------------------------------------------------------------
# Failure safety — Coordinator Specialist


@pytest.mark.asyncio
async def test_coordinator_failure_stops_downstream_specialists(monkeypatch):
    import app.services.agent_workflow_service as service

    async def _raises(state: dict) -> dict:
        raise RuntimeError("simulated coordinator failure")

    async def _knowledge(state: dict, *, db: Any) -> dict:
        pytest.fail("Knowledge Specialist must not run after a coordinator failure")
        return state  # pragma: no cover

    async def _action(state: dict) -> dict:
        pytest.fail("Action Specialist must not run after a coordinator failure")
        return state  # pragma: no cover

    async def _load(state: dict, *, db: Any) -> dict:
        return {
            "ticket": _KNOWLEDGE_ACTION_TICKET,
            "workflow_path": [*state["workflow_path"], "load_ticket"],
        }

    failures: list[str] = []
    monkeypatch.setattr(agent_workflow_service, "_load_ticket", _load)
    monkeypatch.setattr(agent_workflow_service, "_assess_knowledge_need", _raises)
    monkeypatch.setattr(agent_workflow_service, "_run_knowledge_specialist", _knowledge)
    monkeypatch.setattr(agent_workflow_service, "_run_action_specialist", _action)
    monkeypatch.setattr(
        service,
        "record_agent_specialist_failure",
        lambda **kw: failures.append(kw["specialist"]),
    )

    workflow = agent_workflow_service._build_workflow(_FAKE_DB)

    with pytest.raises(RuntimeError, match="simulated coordinator failure"):
        await workflow.ainvoke(
            {
                "ticket_id": 1,
                "organization_id": TENANT_A,
                "workflow_path": [],
                "sources": [],
                "tool_plan": [],
                "customer_context": None,
                "conversation_context": None,
            }
        )

    assert failures == ["coordinator"]
    assert all(f in _ALLOWED_SPECIALISTS for f in failures)


# ---------------------------------------------------------------------------
# Evaluation layer — actual specialist path derived from the REAL workflow


def _fake_analyze(workflow_path: list[str]):
    async def analyze(db, **kwargs: Any) -> dict:
        return {
            "decision": {"action": "respond", "reason": "x"},
            "workflow_path": workflow_path,
            "tool_plan": [],
            "coordinator_intent": "information",
        }

    return analyze


@pytest.mark.asyncio
async def test_evaluate_case_derives_specialists_from_real_workflow_path(monkeypatch):
    monkeypatch.setattr(
        agent_workflow_service,
        "analyze",
        _fake_analyze(_MARKED_CORO),
    )

    matches = await AgentEvaluationService.evaluate_case(
        _FAKE_DB,
        ticket_id=1,
        organization_id=TENANT_A,
        expected_action="respond",
        expected_retrieval=True,
        expected_tool="zendesk.send_reply",
        expected_auto_execute=False,
        expected_specialists=["coordinator", "knowledge", "action"],
    )
    assert matches["actual_specialists"] == ["coordinator", "knowledge", "action"]
    assert matches["specialist_path_pass"] is True

    mismatch = await AgentEvaluationService.evaluate_case(
        _FAKE_DB,
        ticket_id=1,
        organization_id=TENANT_A,
        expected_action="respond",
        expected_retrieval=True,
        expected_tool="zendesk.send_reply",
        expected_auto_execute=False,
        expected_specialists=["coordinator", "action"],
    )
    assert mismatch["actual_specialists"] == ["coordinator", "knowledge", "action"]
    assert mismatch["specialist_path_pass"] is False


@pytest.mark.asyncio
async def test_evaluate_case_specialists_never_reclassify(monkeypatch):
    """The actual path is derived from workflow_path, never from the decision."""
    monkeypatch.setattr(
        agent_workflow_service,
        "analyze",
        _fake_analyze(_MARKED_ACTION),
    )

    result = await AgentEvaluationService.evaluate_case(
        _FAKE_DB,
        ticket_id=1,
        organization_id=TENANT_A,
        expected_action="respond",
        expected_retrieval=True,
        expected_tool="zendesk.send_reply",
        expected_auto_execute=False,
        expected_specialists=["coordinator", "action"],
    )
    assert result["workflow_path"] == _MARKED_ACTION
    assert result["actual_specialists"] == ["coordinator", "action"]
    assert result["specialist_path_pass"] is True


@pytest.mark.asyncio
async def test_evaluate_case_without_expectation_not_scored(monkeypatch):
    monkeypatch.setattr(
        agent_workflow_service,
        "analyze",
        _fake_analyze(_MARKED_CORO),
    )

    result = await AgentEvaluationService.evaluate_case(
        _FAKE_DB,
        ticket_id=1,
        organization_id=TENANT_A,
        expected_action="respond",
        expected_retrieval=True,
        expected_tool="zendesk.send_reply",
        expected_auto_execute=False,
        expected_specialists=None,
    )
    assert result["specialist_path_pass"] is None
    assert result["actual_specialists"] == ["coordinator", "knowledge", "action"]