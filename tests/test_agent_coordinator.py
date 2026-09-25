"""Deterministic coordinator intent classification tests (Phase 1K.1A).

The coordinator step (``_assess_knowledge_need``) labels every request with a
deterministic ``intent`` in {information, action, mixed, none}. The label is
advisory metadata only: routing still branches on ``needs_knowledge`` exactly
as before Phase 1K, so fast paths, retrieval, and final decisions are
byte-for-byte unchanged. No LLM is consulted anywhere in the coordinator.

These tests are pure unit tests — no database, no OpenAI.
"""

import os
from typing import get_args, get_origin

import pytest

os.environ["ENVIRONMENT"] = "development"

from app.services.agent_workflow_service import (
    AGENT_WORKFLOW_VERSION,
    AgentState,
    AgentWorkflowService,
    agent_workflow_service,
)

TENANT_A = 111
TENANT_B = 222


def _ticket(subject: str, description: str, *, organization_id: int = TENANT_A) -> dict:
    return {
        "subject": subject,
        "description": description,
        "status": "open",
        "priority": "normal",
        "organization_id": organization_id,
    }


def _state(ticket: dict, *, path: list[str] | None = None) -> AgentState:
    return {
        "ticket_id": 99,
        "organization_id": ticket["organization_id"],
        "ticket": ticket,
        "workflow_path": path or ["load_ticket"],
    }


async def _assess(ticket: dict, *, path: list[str] | None = None) -> dict:
    return await agent_workflow_service._assess_knowledge_need(_state(ticket, path=path))


def _classify(text: str, *, fast_path_action: str | None = None, policy: bool = False) -> str:
    return AgentWorkflowService._classify_intent(
        text=text,
        fast_path_action=fast_path_action,
        has_policy_signal=policy,
    )


# -------------------------------------------------
# Intention classification
# -------------------------------------------------


@pytest.mark.asyncio
async def test_acknowledgement_is_none():
    result = await _assess(_ticket("Thank you", "Issue is resolved, all good now."))
    assert result["intent"] == "none"
    assert result["needs_knowledge"] is False
    assert result["fast_path_action"] == "no_action"


@pytest.mark.asyncio
async def test_record_only_is_none_even_with_action_phrasing():
    # "please" and "changed" are action markers, but the fast path wins.
    result = await _assess(_ticket("Please note that", "I changed my phone number."))
    assert result["intent"] == "none"
    assert result["needs_knowledge"] is False
    assert result["fast_path_action"] == "internal_note"


@pytest.mark.asyncio
async def test_knowledge_information_request_is_information():
    result = await _assess(_ticket("Refund policy question", "What is your refund policy?"))
    assert result["intent"] == "information"
    assert result["needs_knowledge"] is True


@pytest.mark.asyncio
async def test_pure_action_without_knowledge_signal_is_action():
    result = await _assess(_ticket("Callback request", "Can you please call me back?"))
    assert result["intent"] == "action"


@pytest.mark.asyncio
async def test_action_requiring_knowledge_is_mixed():
    result = await _assess(_ticket("Refund request", "I want a refund."))
    assert result["intent"] == "mixed"
    assert result["needs_knowledge"] is True


@pytest.mark.asyncio
async def test_unknown_band_defaults_to_mixed():
    result = await _assess(_ticket("Friendly note", "The weather is nice today."))
    assert result["intent"] == "mixed"


# -------------------------------------------------
# Routing is unchanged: intent never drives the graph
# -------------------------------------------------


@pytest.mark.asyncio
async def test_routing_ignores_intent_and_follows_needs_knowledge():
    knowledge_case = await _assess(_ticket("Refund request", "I want a refund."))
    assert (
        AgentWorkflowService._route_after_assessment(knowledge_case)
        == "retrieve_knowledge"
    )
    for label in ("information", "action", "mixed", "none"):
        variant = {**knowledge_case, "intent": label}
        assert (
            AgentWorkflowService._route_after_assessment(variant)
            == "retrieve_knowledge"
        ), f"intent {label!r} must not change routing"


@pytest.mark.asyncio
async def test_fast_path_routing_unchanged():
    ack = await _assess(_ticket("Thank you", "All good now."))
    assert ack["intent"] == "none"
    assert AgentWorkflowService._route_after_assessment(ack) == "decide_action"
    assert (
        AgentWorkflowService._route_after_assessment({**ack, "intent": "information"})
        == "decide_action"
    )


@pytest.mark.asyncio
async def test_fast_path_semantics_byte_identical():
    # Guard the exact reason strings and flags against drift.
    ack = await _assess(_ticket("Thank you", "All good now."))
    assert ack["fast_path_action"] == "no_action"
    assert ack["knowledge_reason"] == (
        "The message is a greeting, "
        "acknowledgement, or resolved-case "
        "confirmation with no policy question."
    )
    record = await _assess(_ticket("Please record this", "No reply needed."))
    assert record["fast_path_action"] == "internal_note"
    assert record["knowledge_reason"] == (
        "The message only asks CXOps "
        "to record information and does "
        "not require company-policy guidance."
    )
    uncertain = await _assess(_ticket("Update", "I want a refund."))
    assert uncertain["fast_path_action"] is None
    assert uncertain["knowledge_reason"] == (
        "The ticket may depend on company "
        "policy or operational guidance, so "
        "knowledge retrieval is required."
    )


# -------------------------------------------------
# Coordinator safety
# -------------------------------------------------


@pytest.mark.asyncio
async def test_coordinator_makes_no_llm_call(monkeypatch):
    class _ExplodingLLM:
        async def ainvoke(self, *_args, **_kwargs):
            raise AssertionError("coordinator must never call an LLM")

    monkeypatch.setattr(agent_workflow_service, "decision_llm", _ExplodingLLM())
    result = await _assess(_ticket("Refund request", "I want a refund."))
    assert result["intent"] == "mixed"


@pytest.mark.asyncio
async def test_coordinator_never_reads_tenant():
    output_a = await _assess(
        _ticket("Refund request", "I want a refund.", organization_id=TENANT_A)
    )
    output_b = await _assess(
        _ticket("Refund request", "I want a refund.", organization_id=TENANT_B)
    )
    assert output_a == output_b
    assert "organization_id" not in output_a


@pytest.mark.asyncio
async def test_workflow_path_records_coordinator_step():
    result = await _assess(
        _ticket("Refund question", "What is the processing time?"),
        path=["load_ticket"],
    )
    assert result["workflow_path"] == ["load_ticket", "assess_knowledge_need"]


# -------------------------------------------------
# Fingerprint covers the coordinator workflow version
# -------------------------------------------------


def _fingerprint_kwargs() -> dict:
    return {
        "organization_id": 1,
        "ticket_id": 2,
        "ticket": {"subject": "a", "description": "b", "status": "open", "priority": "normal"},
        "agent_decision_version": "2",
        "model": "gpt-test",
        "corpus_revision": {},
        "customer_context_digest": "digest-a",
    }


def test_fingerprint_depends_on_workflow_version():
    v1 = AgentWorkflowService._compute_fingerprint(
        **_fingerprint_kwargs(), agent_workflow_version="1"
    )
    v2 = AgentWorkflowService._compute_fingerprint(
        **_fingerprint_kwargs(), agent_workflow_version="2"
    )
    assert v1 != v2


def test_fingerprint_default_is_current_workflow_version():
    defaulted = AgentWorkflowService._compute_fingerprint(**_fingerprint_kwargs())
    explicit = AgentWorkflowService._compute_fingerprint(
        **_fingerprint_kwargs(), agent_workflow_version=AGENT_WORKFLOW_VERSION
    )
    # Bumped from "1" in Phase 1K.2B: the coordinator now routes action intent
    # straight to the Action Specialist, so v1 analyses must not be reused.
    assert AGENT_WORKFLOW_VERSION == "2"
    assert defaulted == explicit
    assert (
        AgentWorkflowService._compute_fingerprint(**_fingerprint_kwargs())
        == AgentWorkflowService._compute_fingerprint(**_fingerprint_kwargs())
    )


class TestClassifierUnit:
    def test_classifier_covers_all_four_labels(self):
        assert _classify("what is the refund policy", policy=True) == "information"
        assert _classify("please call me back") == "action"
        assert _classify("i want a refund", policy=True) == "mixed"
        assert _classify("anything at all", fast_path_action="no_action") == "none"


class TestAgentStateContract:
    def test_state_declares_intent_field(self):
        assert "intent" in AgentState.__annotations__
        intent_type = AgentState.__annotations__["intent"]
        inner = get_args(intent_type)[0] if get_origin(intent_type) is not None else intent_type
        assert set(get_args(inner)) == {"information", "action", "mixed", "none"}