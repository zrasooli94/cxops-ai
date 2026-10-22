"""Offline contract tests for the future Responses adapter; no provider calls."""

import json

import pytest

from app.services.model_eval_controller import EvaluationBlockedError
from app.services.model_eval_openai_adapter import (
    GenerationSuppressed,
    GroundedTurn,
    ProviderDisconnectedResponsesAdapter,
    TiktokenExactModelCounter,
)


class _DisconnectedTransport:
    provider_disconnected = True

    def __init__(self, response: dict | None = None) -> None:
        self.response = response or {
            "status": "completed",
            "output": [{"content": [{
                "type": "output_text",
                "text": json.dumps({
                    "answer": "تعمیرگاه تا ساعت ۷:۱۵ باز است.",
                    "citation_ids": ["shop-hours"],
                    "refused": False,
                }),
            }]}],
        }
        self.payloads: list[dict] = []

    async def create_response(self, payload):
        self.payloads.append(payload)
        return self.response


def _serialize(status="ai_active"):
    return ProviderDisconnectedResponsesAdapter.serialize_grounded_input(
        session_status=status,
        assistant_name="دوچرخه‌یار",
        language="fa",
        history=[
            GroundedTurn("customer", "What time does the workshop close?"),
            GroundedTurn("assistant", "Which day do you mean?"),
            GroundedTurn("customer", "جمعه چطور؟"),
        ],
        knowledge=[
            ("shop-hours", "Friday workshop hours are 10:00–19:15."),
            ("returns", "Unused helmets may be returned within 14 days."),
        ],
        customer_message="پس قبل از بسته شدن می‌توانم دوچرخه را بیاورم؟",
    )


def test_full_bilingual_context_and_knowledge_are_serialized_not_hardcoded():
    serialized = _serialize()
    data = json.loads(serialized)

    assert data["customer_message"].startswith("پس قبل")
    assert data["conversation_history"][0]["text"].startswith("What time")
    assert data["conversation_history"][2]["text"] == "جمعه چطور؟"
    assert data["business_knowledge"][0] == {
        "citation_id": "shop-hours",
        "text": "Friday workshop hours are 10:00–19:15.",
    }


@pytest.mark.parametrize("restored_status", ["human_requested", "human_assigned"])
def test_restored_handoff_state_suppresses_generation(restored_status):
    with pytest.raises(GenerationSuppressed, match="handoff"):
        _serialize(restored_status)


def test_adapter_refuses_unapproved_or_connected_transport():
    transport = _DisconnectedTransport()
    with pytest.raises(EvaluationBlockedError, match="approval"):
        ProviderDisconnectedResponsesAdapter(
            transport=transport, explicitly_approved=False,
        )
    transport.provider_disconnected = False
    with pytest.raises(EvaluationBlockedError, match="real provider"):
        ProviderDisconnectedResponsesAdapter(
            transport=transport, explicitly_approved=True,
        )


def test_installed_tokenizer_fails_closed_for_unmapped_exact_model():
    with pytest.raises(EvaluationBlockedError, match="no verified encoding"):
        TiktokenExactModelCounter("gpt-6-luna")


@pytest.mark.asyncio
async def test_fixed_request_contract_and_grounded_response_parsing():
    transport = _DisconnectedTransport()
    adapter = ProviderDisconnectedResponsesAdapter(
        transport=transport, explicitly_approved=True,
    )

    result = await adapter.generate(_serialize())

    assert result.citations == ("shop-hours",)
    assert result.refused is False
    payload = transport.payloads[0]
    assert payload["model"] == "gpt-6-luna"
    assert payload["reasoning"] == {"effort": "none"}
    assert payload["max_output_tokens"] == 500
    assert payload["prompt_cache_options"] == {"ttl": "30m"}
    assert payload["store"] is False and payload["stream"] is False
    assert payload["text"]["format"]["strict"] is True


@pytest.mark.asyncio
async def test_refusal_incomplete_truncation_and_bad_citations_fail_safely():
    refusal = _DisconnectedTransport({
        "status": "completed",
        "output": [{"content": [{"type": "refusal", "refusal": "Cannot answer safely."}]}],
    })
    result = await ProviderDisconnectedResponsesAdapter(
        transport=refusal, explicitly_approved=True,
    ).generate(_serialize())
    assert result.refused is True and result.citations == ()

    incomplete = _DisconnectedTransport({
        "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}
    })
    with pytest.raises(EvaluationBlockedError, match="max_output_tokens"):
        await ProviderDisconnectedResponsesAdapter(
            transport=incomplete, explicitly_approved=True,
        ).generate(_serialize())

    bad = _DisconnectedTransport({
        "status": "completed",
        "output": [{"content": [{"type": "output_text", "text": json.dumps({
            "answer": "Unsupported", "citation_ids": [], "refused": False,
        })}]}],
    })
    with pytest.raises(EvaluationBlockedError, match="requires text and citations"):
        await ProviderDisconnectedResponsesAdapter(
            transport=bad, explicitly_approved=True,
        ).generate(_serialize())

    unknown_citation = _DisconnectedTransport({
        "status": "completed",
        "output": [{"content": [{"type": "output_text", "text": json.dumps({
            "answer": "Invented", "citation_ids": ["not-retrieved"], "refused": False,
        })}]}],
    })
    with pytest.raises(EvaluationBlockedError, match="unknown citation"):
        await ProviderDisconnectedResponsesAdapter(
            transport=unknown_citation, explicitly_approved=True,
        ).generate(_serialize())


@pytest.mark.asyncio
async def test_timeout_is_treated_as_ambiguous_not_retried():
    transport = _DisconnectedTransport()

    async def timeout(_payload):
        raise TimeoutError("synthetic timeout")

    transport.create_response = timeout
    adapter = ProviderDisconnectedResponsesAdapter(
        transport=transport, explicitly_approved=True,
    )
    with pytest.raises(EvaluationBlockedError, match="outcome is ambiguous"):
        await adapter.generate(_serialize())
