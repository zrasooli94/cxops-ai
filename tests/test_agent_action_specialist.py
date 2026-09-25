"""Action Specialist boundary tests (Phase 1K.1C).

The graph ``decide_action`` node delegates to ``_run_action_specialist``, a
pure extraction of the pre-existing decision logic. It must:

- keep the EXACT single-LangChain-call path (one ``decision_llm.ainvoke`` on
  the normal path, zero on deterministic fast paths),
- return the existing decision shape, response draft, and observability,
- never mark tools authorized itself,
- leave ``ToolAuthorizationService`` as the authorizer,
- never touch tenant / intent / sources,
- keep the ``decide_action`` workflow_path compatibility label plus the
  ``action_specialist`` marker.

``_decide_action`` remains as a backward-compatible delegating alias.

These are pure unit tests — no database, no OpenAI.
"""

import os
from types import SimpleNamespace
from typing import Any

import pytest

os.environ["ENVIRONMENT"] = "development"

from app.services.agent_workflow_service import (
    AgentWorkflowService,
    TicketNotFoundError,
    agent_workflow_service,
)
from app.services.tool_authorization_service import ToolAuthorizationService

TENANT_A = 111


def _ticket(**overrides: Any) -> dict:
    base = {
        "id": 1,
        "subject": "Refund request",
        "description": "I want a refund.",
        "status": "open",
        "priority": "normal",
        "category": "billing",
        "assigned_team": None,
    }
    base.update(overrides)
    return base


def _state(
    ticket: dict,
    *,
    fast_path_action: str | None = None,
    intent: str = "mixed",
    path: list[str] | None = None,
) -> dict:
    return {
        "ticket_id": 1,
        "organization_id": TENANT_A,
        "ticket": ticket,
        "fast_path_action": fast_path_action,
        "customer_context": {"name": "Ada"},
        "conversation_context": {"messages": ["hello"]},
        "needs_knowledge": True,
        "intent": intent,
        "sources": [
            {
                "source_id": "S1",
                "title": "Refund policy",
                "content": "Refunds are processed within 5 business days.",
                "similarity": 0.9,
            }
        ],
        "workflow_path": path or [
            "load_ticket",
            "assess_knowledge_need",
            "retrieve_knowledge",
            "knowledge_specialist",
        ],
    }


def _respond_parsed() -> dict:
    return {
        "action": "respond",
        "reason": "Draft a safe customer-facing refund reply.",
        "recommended_team": None,
        "recommended_priority": None,
        "response_draft": (
            "Thanks for contacting us. A member of our team will review "
            "your request."
        ),
        "requires_human_approval": True,
    }


class _FakeStructuredLLM:
    """Records every ainvoke and returns a fake structured-output result."""

    def __init__(self, parsed: dict, *, usage: dict | None = None) -> None:
        self.calls: list[list[Any]] = []
        self.parsed = parsed
        self.parsing_error = None
        self.usage = usage or {
            "input_tokens": 10,
            "output_tokens": 20,
            "total_tokens": 30,
        }

    async def ainvoke(self, messages: list[Any], **_kwargs) -> dict:
        self.calls.append(messages)
        return {
            "raw": SimpleNamespace(usage_metadata=self.usage),
            "parsed": self.parsed,
            "parsing_error": self.parsing_error,
        }


async def _run_specialist(
    state: dict,
    fake_llm: _FakeStructuredLLM,
    monkeypatch,
) -> dict:
    monkeypatch.setattr(agent_workflow_service, "decision_llm", fake_llm)
    return await agent_workflow_service._run_action_specialist(state)


@pytest.mark.asyncio
async def test_specialist_uses_existing_decision_path(monkeypatch):
    fake_llm = _FakeStructuredLLM(_respond_parsed())
    result = await _run_specialist(_state(_ticket()), fake_llm, monkeypatch)

    assert result["decision"]["action"] == "respond"
    assert result["decision_observability"]["llm_called"] is True
    assert result["decision_observability"]["model"] is not None
    assert result["decision_observability"]["grounded"] is True


@pytest.mark.asyncio
async def test_normal_path_makes_exactly_one_llm_call(monkeypatch):
    fake_llm = _FakeStructuredLLM(_respond_parsed())
    await _run_specialist(_state(_ticket()), fake_llm, monkeypatch)

    assert len(fake_llm.calls) == 1
    (messages,) = fake_llm.calls
    assert len(messages) == 2  # system prompt + user prompt


@pytest.mark.asyncio
async def test_fast_path_no_action_uses_zero_llm_calls(monkeypatch):
    fake_llm = _FakeStructuredLLM(_respond_parsed())
    state = _state(_ticket(), fast_path_action="no_action")
    result = await _run_specialist(state, fake_llm, monkeypatch)

    assert fake_llm.calls == []
    assert result["decision"]["action"] == "no_action"
    assert result["decision_observability"]["llm_called"] is False
    assert result["decision_observability"]["model"] == "deterministic-fast-path"


@pytest.mark.asyncio
async def test_fast_path_internal_note_uses_zero_llm_calls(monkeypatch):
    fake_llm = _FakeStructuredLLM(_respond_parsed())
    state = _state(_ticket(), fast_path_action="internal_note")
    result = await _run_specialist(state, fake_llm, monkeypatch)

    assert fake_llm.calls == []
    assert result["decision"]["action"] == "internal_note"
    assert result["decision_observability"]["llm_called"] is False


@pytest.mark.asyncio
async def test_output_decision_shape_unchanged(monkeypatch):
    fake_llm = _FakeStructuredLLM(_respond_parsed())
    result = await _run_specialist(_state(_ticket()), fake_llm, monkeypatch)

    assert set(result["decision"].keys()) == {
        "action",
        "reason",
        "recommended_team",
        "recommended_priority",
        "response_draft",
        "requires_human_approval",
    }


@pytest.mark.asyncio
async def test_response_draft_flows_through_unchanged(monkeypatch):
    draft = "This exact draft must survive the specialist untouched."
    fake_llm = _FakeStructuredLLM(_respond_parsed())
    fake_llm.parsed["response_draft"] = draft

    result = await _run_specialist(_state(_ticket()), fake_llm, monkeypatch)
    assert result["decision"]["response_draft"] == draft


@pytest.mark.asyncio
async def test_deterministic_tool_plan_unchanged(monkeypatch):
    fake_llm = _FakeStructuredLLM(_respond_parsed())
    result = await _run_specialist(_state(_ticket()), fake_llm, monkeypatch)

    plan_state = {
        **result,
        "workflow_path": result["workflow_path"] + ["action_specialist"],
    }
    plan = await AgentWorkflowService._build_tool_plan(plan_state)

    assert [tool["tool"] for tool in plan["tool_plan"]] == ["zendesk.send_reply"]
    assert (
        plan["tool_plan"][0]["arguments"]["body"]
        == result["decision"]["response_draft"]
    )
    assert plan["workflow_path"] == result["workflow_path"] + [
        "action_specialist",
        "build_tool_plan",
    ]


@pytest.mark.asyncio
async def test_specialist_never_marks_tools_authorized(monkeypatch):
    fake_llm = _FakeStructuredLLM(_respond_parsed())
    result = await _run_specialist(_state(_ticket()), fake_llm, monkeypatch)

    assert "tool_plan" not in result
    assert "authorized" not in result
    assert "authorization" not in result
    assert "approval" not in result


@pytest.mark.asyncio
async def test_tool_authorization_service_remains_authorizer(monkeypatch):
    captured: dict[str, Any] = {}

    def _fake_authorize_plan(tool_plan: list[dict]) -> list[dict]:
        captured["tool_plan"] = tool_plan
        return [
            dict(tool, authorized=True, risk_level="high", requires_approval=True)
            for tool in tool_plan
        ]

    monkeypatch.setattr(
        ToolAuthorizationService,
        "authorize_plan",
        _fake_authorize_plan,
    )

    state = {
        "decision": {
            "action": "respond",
            "reason": "reason",
            "recommended_team": None,
            "recommended_priority": None,
            "response_draft": "draft",
        },
        "workflow_path": ["decide_action", "action_specialist"],
    }
    plan = await AgentWorkflowService._build_tool_plan(state)

    assert captured["tool_plan"][0]["tool"] == "zendesk.send_reply"
    assert "authorized" not in captured["tool_plan"][0]
    assert plan["tool_plan"][0]["authorized"] is True


@pytest.mark.asyncio
async def test_organization_id_not_written_by_specialist(monkeypatch):
    fake_llm = _FakeStructuredLLM(_respond_parsed())
    state = _state(_ticket())
    result = await _run_specialist(state, fake_llm, monkeypatch)

    assert "organization_id" not in result
    assert "ticket_id" not in result


@pytest.mark.asyncio
async def test_intent_not_written_by_specialist(monkeypatch):
    fake_llm = _FakeStructuredLLM(_respond_parsed())
    state = _state(_ticket(), intent="action")
    result = await _run_specialist(state, fake_llm, monkeypatch)

    assert "intent" not in result


@pytest.mark.asyncio
async def test_sources_not_written_by_specialist(monkeypatch):
    fake_llm = _FakeStructuredLLM(_respond_parsed())
    state = _state(_ticket())
    result = await _run_specialist(state, fake_llm, monkeypatch)

    assert "sources" not in result
    # Observability still reports the exact state sources.
    assert result["decision_observability"]["retrieval_count"] == 1
    assert result["decision_observability"]["best_similarity"] == 0.9


@pytest.mark.asyncio
async def test_workflow_path_keeps_compat_label_and_specialist_marker(monkeypatch):
    fake_llm = _FakeStructuredLLM(_respond_parsed())
    result = await _run_specialist(_state(_ticket()), fake_llm, monkeypatch)

    assert "decide_action" in result["workflow_path"]
    assert result["workflow_path"][-2:] == ["decide_action", "action_specialist"]


@pytest.mark.asyncio
async def test_fast_path_workflow_path_keeps_compat_label_and_marker(monkeypatch):
    fake_llm = _FakeStructuredLLM(_respond_parsed())
    state = _state(_ticket(), fast_path_action="no_action")
    result = await _run_specialist(state, fake_llm, monkeypatch)

    assert result["workflow_path"][-2:] == ["decide_action", "action_specialist"]


@pytest.mark.asyncio
async def test_backward_compatible_alias_delegates_to_specialist(monkeypatch):
    fake_llm = _FakeStructuredLLM(_respond_parsed())
    monkeypatch.setattr(agent_workflow_service, "decision_llm", fake_llm)

    state = _state(_ticket())
    specialist = await agent_workflow_service._run_action_specialist(state)
    alias = await agent_workflow_service._decide_action(state)

    # latency_ms is re-measured per invocation; drop it from both sides.
    specialist["decision_observability"].pop("latency_ms", None)
    alias["decision_observability"].pop("latency_ms", None)

    assert alias == specialist


@pytest.mark.asyncio
async def test_error_behavior_missing_ticket_unchanged(monkeypatch):
    fake_llm = _FakeStructuredLLM(_respond_parsed())
    state = _state(_ticket())
    del state["ticket"]

    with pytest.raises(TicketNotFoundError):
        await _run_specialist(state, fake_llm, monkeypatch)


@pytest.mark.asyncio
async def test_error_behavior_parsing_failure_unchanged(monkeypatch):
    fake_llm = _FakeStructuredLLM(_respond_parsed())
    fake_llm.parsed = None
    fake_llm.parsing_error = "boom"

    with pytest.raises(ValueError, match="Agent decision parsing failed: boom"):
        await _run_specialist(_state(_ticket()), fake_llm, monkeypatch)


@pytest.mark.asyncio
async def test_error_behavior_null_parsed_unchanged(monkeypatch):
    fake_llm = _FakeStructuredLLM(_respond_parsed())
    fake_llm.parsed = None

    with pytest.raises(ValueError, match="returned no parsed decision"):
        await _run_specialist(_state(_ticket()), fake_llm, monkeypatch)