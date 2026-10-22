"""Offline contract adapter for a future approved OpenAI Responses evaluation.

This module deliberately has no OpenAI client and reads no credentials.  It makes
the future wire contract testable with an injected, provider-disconnected
transport while failing closed because local text tokenization cannot certify
the full structured Responses request.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from app.services.model_eval_controller import (
    ConfirmedNoDispatchError,
    EvaluationBlockedError,
    EvaluationTransportResult,
)

HANDED_OFF_STATUSES = frozenset({"human_requested", "human_assigned"})


class GenerationSuppressed(EvaluationBlockedError):
    """The persisted session state forbids autonomous generation."""


class DisconnectedResponsesTransport(Protocol):
    provider_disconnected: bool

    async def create_response(self, payload: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class GroundedTurn:
    role: str
    text: str


@dataclass(frozen=True)
class ParsedGroundedResponse:
    text: str
    citations: tuple[str, ...]
    refused: bool
    input_tokens: int
    output_tokens: int


class TiktokenExactModelCounter:
    """Text-only counter that is never valid for full-request admission."""

    counts_full_request = False

    def __init__(self, model: str) -> None:
        import tiktoken

        try:
            self._encoding = tiktoken.encoding_for_model(model)
        except (KeyError, ConnectionError) as exc:
            raise EvaluationBlockedError(
                f"installed tiktoken has no verified encoding for {model}"
            ) from exc
        self.model = model

    def count(self, serialized_input: str) -> int:
        return len(self._encoding.encode(serialized_input))


class ProviderDisconnectedResponsesAdapter:
    """Build and parse the fixed evaluation contract without provider access."""

    MODEL = "gpt-6-luna"
    MAX_OUTPUT_TOKENS = 500
    MAX_HISTORY_TURNS = 12
    MAX_KNOWLEDGE_ITEMS = 8

    def __init__(
        self,
        *,
        transport: DisconnectedResponsesTransport,
        explicitly_approved: bool,
        model: str = MODEL,
        service_tier: str = "default",
        cache_retention: str = "30m",
        development_provider_source_enabled: bool = False,
        environment: str = "development",
    ) -> None:
        if not explicitly_approved:
            raise EvaluationBlockedError("evaluation approval is required")
        if not transport.provider_disconnected and not (
            development_provider_source_enabled and environment == "development"
        ):
            raise EvaluationBlockedError("real provider transport is not enabled")
        if (model, service_tier, cache_retention) != (self.MODEL, "default", "30m"):
            raise EvaluationBlockedError("unapproved provider configuration")
        self.transport = transport

    @classmethod
    def serialize_grounded_input(
        cls,
        *,
        session_status: str,
        assistant_name: str,
        language: str,
        history: list[GroundedTurn],
        knowledge: list[tuple[str, str]],
        customer_message: str,
    ) -> str:
        if session_status in HANDED_OFF_STATUSES:
            raise GenerationSuppressed("human handoff suppresses autonomous generation")
        if session_status != "ai_active":
            raise GenerationSuppressed("session state does not allow autonomous generation")
        if not customer_message.strip() or not knowledge:
            raise EvaluationBlockedError("customer message and grounded knowledge are required")
        if len(history) > cls.MAX_HISTORY_TURNS or len(knowledge) > cls.MAX_KNOWLEDGE_ITEMS:
            raise EvaluationBlockedError("evaluation context exceeds fixed item limits")
        if any(turn.role not in {"customer", "assistant"} for turn in history):
            raise EvaluationBlockedError("unsupported history role")

        contract = {
            "assistant": {"name": assistant_name, "language": language},
            "rules": [
                "Answer only from supplied business knowledge.",
                "Use conversation history only for context, never as factual evidence.",
                "If evidence is insufficient or conflicting, return a refusal.",
                "Return citation ids for every factual answer.",
            ],
            "business_knowledge": [
                {"citation_id": citation_id, "text": text}
                for citation_id, text in knowledge
            ],
            "conversation_history": [
                {"role": turn.role, "text": turn.text} for turn in history
            ],
            "customer_message": customer_message,
            "response_schema": {
                "type": "object",
                "required": ["answer", "citation_ids", "refused"],
                "additionalProperties": False,
            },
        }
        return json.dumps(contract, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    @classmethod
    def request_payload(cls, serialized_input: str) -> dict[str, Any]:
        return {
            "model": cls.MODEL,
            "input": serialized_input,
            "reasoning": {"effort": "none"},
            "max_output_tokens": cls.MAX_OUTPUT_TOKENS,
            "service_tier": "default",
            # Explicit mode with no breakpoints disables cache reads/writes.
            "prompt_cache_options": {"mode": "explicit", "ttl": "30m"},
            "store": False,
            "stream": False,
            "truncation": "disabled",
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "grounded_chat_answer",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "answer": {"type": "string"},
                            "citation_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "refused": {"type": "boolean"},
                        },
                        "required": ["answer", "citation_ids", "refused"],
                        "additionalProperties": False,
                    },
                }
            },
        }

    async def generate(self, serialized_input: str) -> ParsedGroundedResponse:
        try:
            raw = await self.transport.create_response(self.request_payload(serialized_input))
        except ConfirmedNoDispatchError:
            raise
        except Exception:  # noqa: BLE001 - every post-dispatch failure is ambiguous
            raise EvaluationBlockedError(
                "provider transport outcome is ambiguous and requires reconciliation"
            ) from None
        parsed = self.parse_response(raw)
        allowed = {
            item["citation_id"]
            for item in json.loads(serialized_input)["business_knowledge"]
        }
        if not set(parsed.citations).issubset(allowed):
            raise EvaluationBlockedError("provider returned an unknown citation id")
        return parsed

    @staticmethod
    def parse_response(raw: dict[str, Any]) -> ParsedGroundedResponse:
        if raw.get("status") != "completed":
            reason = (raw.get("incomplete_details") or {}).get("reason", "unknown")
            raise EvaluationBlockedError(f"provider response incomplete: {reason}")
        outputs = raw.get("output")
        if not isinstance(outputs, list) or len(outputs) != 1:
            raise EvaluationBlockedError("unexpected provider output envelope")
        content = outputs[0].get("content") if isinstance(outputs[0], dict) else None
        if not isinstance(content, list) or len(content) != 1:
            raise EvaluationBlockedError("unexpected provider content envelope")
        item = content[0]
        if not isinstance(item, dict):
            raise EvaluationBlockedError("unexpected provider content item")
        if item.get("type") == "refusal":
            refusal = item.get("refusal")
            if not isinstance(refusal, str) or not refusal.strip():
                raise EvaluationBlockedError("empty provider refusal")
            input_tokens, output_tokens = ProviderDisconnectedResponsesAdapter._parse_usage(raw)
            return ParsedGroundedResponse(refusal.strip(), (), True, input_tokens, output_tokens)
        if item.get("type") != "output_text" or not isinstance(item.get("text"), str):
            raise EvaluationBlockedError("unexpected provider content type")
        try:
            parsed = json.loads(item["text"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise EvaluationBlockedError("provider returned invalid structured JSON") from exc
        if set(parsed) != {"answer", "citation_ids", "refused"}:
            raise EvaluationBlockedError("provider response schema mismatch")
        answer, citations, refused = parsed["answer"], parsed["citation_ids"], parsed["refused"]
        if not isinstance(answer, str) or not isinstance(refused, bool):
            raise EvaluationBlockedError("provider response field types are invalid")
        if not isinstance(citations, list) or not all(isinstance(value, str) for value in citations):
            raise EvaluationBlockedError("provider citations are invalid")
        if refused and citations:
            raise EvaluationBlockedError("refusal cannot claim supporting citations")
        if not refused and (not answer.strip() or not citations):
            raise EvaluationBlockedError("grounded answer requires text and citations")
        input_tokens, output_tokens = ProviderDisconnectedResponsesAdapter._parse_usage(raw)
        return ParsedGroundedResponse(
            answer.strip(), tuple(citations), refused, input_tokens, output_tokens
        )

    @staticmethod
    def _parse_usage(raw: dict[str, Any]) -> tuple[int, int]:
        usage = raw.get("usage")
        if not isinstance(usage, dict):
            raise EvaluationBlockedError("provider usage is missing")
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        if (
            not isinstance(input_tokens, int)
            or isinstance(input_tokens, bool)
            or not isinstance(output_tokens, int)
            or isinstance(output_tokens, bool)
            or input_tokens <= 0
            or output_tokens <= 0
        ):
            raise EvaluationBlockedError("provider usage is malformed")
        return input_tokens, output_tokens


class DisconnectedResponsesEvaluationTransport:
    """Bridge the parsed Responses contract into ``ModelEvaluationController``.

    It remains disconnected because the wrapped adapter enforces a disconnected
    injected transport. No OpenAI client or credential is constructed here.
    """

    def __init__(self, adapter: ProviderDisconnectedResponsesAdapter) -> None:
        self.adapter = adapter
        self.provider_disconnected = adapter.transport.provider_disconnected

    async def send(
        self,
        *,
        model: str,
        serialized_input: str,
        max_output_tokens: int,
        service_tier: str,
        cache_retention: str,
        sdk_max_retries: int,
        request_payload: dict[str, Any] | None = None,
    ) -> EvaluationTransportResult:
        if (
            model != self.adapter.MODEL
            or max_output_tokens != self.adapter.MAX_OUTPUT_TOKENS
            or service_tier != "default"
            or cache_retention != "30m"
            or sdk_max_retries != 0
            or (
                request_payload is not None
                and request_payload != self.adapter.request_payload(serialized_input)
            )
        ):
            raise EvaluationBlockedError("controller and Responses policy do not match")
        result = await self.adapter.generate(serialized_input)
        return EvaluationTransportResult(text=json.dumps({
            "answer": result.text,
            "citation_ids": list(result.citations),
            "refused": result.refused,
        }, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
