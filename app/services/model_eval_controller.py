"""Provider-disconnected safety controller for a future approved model evaluation.

The controller has no OpenAI client and cannot create credentials. Tests supply an
injected transport and synthetic full-request count. The HTTPS source remains
uncomposed and disabled until the owner separately approves a run and configures
a restricted key.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Any, Protocol

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_usage_reservation import AIUsageReservation
from app.models.public_chat import PublicChatSession
from app.services.tenant_ai_allowance_service import (
    AI_BUDGET_LOCK_KEY,
    STATUS_RESERVED,
    STATUS_SETTLED,
    month_start,
)


class EvaluationBlockedError(RuntimeError):
    pass


class AmbiguousModelOutcome(EvaluationBlockedError):
    pass


class ConfirmedNoDispatchError(RuntimeError):
    """Transport proves the request was rejected before provider dispatch."""


class EvaluationTransport(Protocol):
    provider_disconnected: bool

    async def send(
        self,
        *,
        model: str,
        serialized_input: str,
        max_output_tokens: int,
        service_tier: str,
        cache_retention: str,
        sdk_max_retries: int,
        request_payload: dict[str, Any] | None,
    ) -> EvaluationTransportResult: ...


@dataclass(frozen=True)
class EvaluationTransportResult:
    text: str
    input_tokens: int
    output_tokens: int


class ExactTokenCounter(Protocol):
    model: str
    counts_full_request: bool

    def count(self, serialized_input: str) -> int: ...


class ExactInputTokenCountTransport(Protocol):
    provider_disconnected: bool

    async def count(
        self,
        *,
        model: str,
        request_payload: dict[str, Any],
        sdk_max_retries: int,
    ) -> ExactInputTokenCountResult: ...


@dataclass(frozen=True)
class ExactInputTokenCountResult:
    input_tokens: int
    request_sha256: str


@dataclass(frozen=True)
class InputTokenCountPolicy:
    """Operator-supplied billing policy; never inferred from missing pricing."""

    endpoint: str
    model: str
    region: str
    reservation_per_attempt_usd: float
    evidence_id: str
    evidence_expires_at: datetime
    billing_status: str = "billable"
    independently_verified: bool = False
    explicitly_approved: bool = False

    def validate(self, *, expected_model: str) -> None:
        if (
            not self.independently_verified
            or not self.explicitly_approved
            or self.endpoint != "https://api.openai.com/v1/responses/input_tokens"
            or self.model != expected_model
            or self.region != "standard"
            or not self.evidence_id.strip()
            or self.evidence_expires_at.tzinfo is None
            or self.evidence_expires_at.astimezone(UTC) <= datetime.now(UTC)
            or self.billing_status not in {"billable", "no_charge"}
            or not isfinite(self.reservation_per_attempt_usd)
            or (
                self.billing_status == "billable"
                and self.reservation_per_attempt_usd <= 0
            )
            or (
                self.billing_status == "no_charge"
                and self.reservation_per_attempt_usd != 0
            )
        ):
            raise EvaluationBlockedError(
                "input-token count billing policy is missing, expired, or unapproved"
            )


class SessionDispatchGuard(Protocol):
    async def assert_generation_allowed(
        self,
        db: AsyncSession,
        *,
        organization_id: int,
        session_id: int,
    ) -> None: ...


class PersistedPublicChatSessionGuard:
    """Linearize mocked dispatch against staff handoff on the session row.

    The row lock remains owned by the caller's transaction until the controller
    settles or rolls back after transport. A handoff that wins first is observed;
    a handoff that starts later waits until this bounded dispatch completes.
    """

    async def assert_generation_allowed(
        self,
        db: AsyncSession,
        *,
        organization_id: int,
        session_id: int,
    ) -> None:
        status = await db.scalar(
            select(PublicChatSession.status)
            .where(
                PublicChatSession.id == session_id,
                PublicChatSession.organization_id == organization_id,
            )
            .with_for_update()
        )
        if status != "ai_active":
            raise EvaluationBlockedError(
                "persisted session state suppresses autonomous generation"
            )


@dataclass(frozen=True)
class EvaluationPolicy:
    model: str = "gpt-6-luna"
    service_tier: str = "default"
    cache_retention: str = "30m"
    max_turns: int = 50
    max_attempts: int = 100
    max_input_tokens: int = 3000
    max_output_tokens: int = 500
    input_rate_per_million: float = 0.10
    output_rate_per_million: float = 0.50
    price_multiplier: float = 1.10
    additional_all_in_reserve_usd: float = 0.0
    cost_ceiling_usd: float = 0.10

    @property
    def reserved_cost_per_attempt(self) -> float:
        return (
            self.price_multiplier
            * (
                self.max_input_tokens * self.input_rate_per_million
                + self.max_output_tokens * self.output_rate_per_million
            )
            / 1_000_000
        )

    def validate(self) -> None:
        if self.model != "gpt-6-luna" or self.service_tier != "default":
            raise EvaluationBlockedError("unapproved model or service tier")
        if self.cache_retention != "30m":
            raise EvaluationBlockedError(
                "only the approved 30m cache policy is allowed"
            )
        if (
            self.input_rate_per_million != 0.10
            or self.output_rate_per_million != 0.50
            or self.price_multiplier != 1.10
            or not isfinite(self.additional_all_in_reserve_usd)
            or self.additional_all_in_reserve_usd < 0
            or self.cost_ceiling_usd != 0.10
            or self.max_input_tokens != 3000
            or self.max_output_tokens != 500
        ):
            raise EvaluationBlockedError("the approved pricing and limits are required")
        worst_case = (
            self.max_attempts * self.reserved_cost_per_attempt
            + self.additional_all_in_reserve_usd
        )
        if (
            self.max_turns != 50
            or self.max_attempts != 100
            or worst_case >= self.cost_ceiling_usd
        ):
            raise EvaluationBlockedError("evaluation requires positive cost headroom")


class ModelEvaluationController:
    PREFIX = "model-eval:"
    COUNT_PREFIX = "model-eval-count:"
    DISCONNECTED_TRANSPORT_TIMEOUT_SECONDS = 30

    def __init__(
        self,
        *,
        policy: EvaluationPolicy,
        exact_token_counter: ExactTokenCounter | None,
        transport: EvaluationTransport,
        explicitly_approved: bool,
        approved_run_id: str,
        approved_organization_id: int,
        session_guard: SessionDispatchGuard,
        count_policy: InputTokenCountPolicy | None = None,
        count_transport: ExactInputTokenCountTransport | None = None,
        development_provider_source_enabled: bool = False,
        environment: str = "development",
    ) -> None:
        policy.validate()
        if not explicitly_approved:
            raise EvaluationBlockedError("evaluation approval is required")
        uses_count_transport = count_policy is not None or count_transport is not None
        if uses_count_transport:
            if (
                exact_token_counter is not None
                or count_policy is None
                or count_transport is None
            ):
                raise EvaluationBlockedError(
                    "exactly one full-request counting path is required"
                )
            count_policy.validate(expected_model=policy.model)
            combined_worst_case = (
                policy.additional_all_in_reserve_usd
                + policy.max_attempts
                * (
                    policy.reserved_cost_per_attempt
                    + count_policy.reservation_per_attempt_usd
                )
            )
            if combined_worst_case >= policy.cost_ceiling_usd:
                raise EvaluationBlockedError(
                    "count and generation reservations lack positive budget headroom"
                )
            if not count_transport.provider_disconnected and not (
                development_provider_source_enabled and environment == "development"
            ):
                raise EvaluationBlockedError(
                    "live input-token counting remains disabled"
                )
        elif (
            exact_token_counter is None
            or exact_token_counter.model != policy.model
            or getattr(exact_token_counter, "counts_full_request", False) is not True
        ):
            raise EvaluationBlockedError(
                "an exact full Responses request token count is required"
            )
        if not transport.provider_disconnected and not (
            development_provider_source_enabled and environment == "development"
        ):
            raise EvaluationBlockedError(
                "real provider transport requires the separate development runtime gate"
            )
        if re.fullmatch(r"[A-Za-z0-9-]{1,64}", approved_run_id) is None:
            raise EvaluationBlockedError("invalid approved evaluation id")
        self.policy = policy
        self.count_tokens = exact_token_counter.count if exact_token_counter else None
        self.count_policy = count_policy
        self.count_transport = count_transport
        self.transport = transport
        self.approved_run_id = approved_run_id
        self.approved_organization_id = approved_organization_id
        self.session_guard = session_guard

    async def attempt(
        self,
        db: AsyncSession,
        *,
        organization_id: int,
        run_id: str,
        turn_number: int,
        retry_number: int,
        serialized_input: str,
        public_chat_session_id: int,
        canonical_request_payload: dict[str, Any] | None = None,
    ) -> str:
        if (
            run_id != self.approved_run_id
            or organization_id != self.approved_organization_id
        ):
            raise EvaluationBlockedError("evaluation run is not approved")
        if turn_number < 1 or turn_number > self.policy.max_turns:
            raise EvaluationBlockedError("turn cap exceeded")
        if retry_number not in {0, 1}:
            raise EvaluationBlockedError("one retry per turn is allowed")
        await self.session_guard.assert_generation_allowed(
            db,
            organization_id=organization_id,
            session_id=public_chat_session_id,
        )
        attempt_suffix = (
            f"session-{public_chat_session_id}:turn-{turn_number}:attempt-{retry_number + 1}"
        )
        admitted_input_tokens = await self._admit_and_count(
            db,
            organization_id=organization_id,
            run_id=run_id,
            attempt_suffix=attempt_suffix,
            serialized_input=serialized_input,
            canonical_request_payload=canonical_request_payload,
        )
        if admitted_input_tokens > self.policy.max_input_tokens:
            raise EvaluationBlockedError("full Responses request exceeds token cap")

        prefix = f"{self.PREFIX}{organization_id}:{run_id}:"
        count_prefix = f"{self.COUNT_PREFIX}{organization_id}:{run_id}:"
        await db.execute(select(func.pg_advisory_xact_lock(AI_BUDGET_LOCK_KEY)))
        unresolved = await db.scalar(
            select(func.count())
            .select_from(AIUsageReservation)
            .where(
                AIUsageReservation.reservation_key.like(f"{prefix}%"),
                AIUsageReservation.status == STATUS_RESERVED,
            )
        )
        if unresolved:
            await db.rollback()
            raise EvaluationBlockedError(
                "an unresolved attempt requires reconciliation"
            )
        used = int(
            await db.scalar(
                select(func.count())
                .select_from(AIUsageReservation)
                .where(AIUsageReservation.reservation_key.like(f"{prefix}%"))
            )
            or 0
        )
        if used >= self.policy.max_attempts:
            await db.rollback()
            raise EvaluationBlockedError("attempt cap exceeded")
        previously_reserved = float(
            await db.scalar(
                select(
                    func.coalesce(func.sum(AIUsageReservation.estimated_cost_usd), 0)
                ).where(
                    AIUsageReservation.organization_id == organization_id,
                    (
                        AIUsageReservation.reservation_key.like(f"{prefix}%")
                        | AIUsageReservation.reservation_key.like(f"{count_prefix}%")
                    ),
                )
            )
            or 0
        )
        reserved_total = (
            self.policy.additional_all_in_reserve_usd
            + previously_reserved
            + self.policy.reserved_cost_per_attempt
        )
        if reserved_total >= self.policy.cost_ceiling_usd:
            await db.rollback()
            raise EvaluationBlockedError("cost ceiling requires positive headroom")

        key = f"{prefix}{attempt_suffix}"
        duplicate = await db.scalar(
            select(AIUsageReservation.id).where(
                AIUsageReservation.reservation_key == key
            )
        )
        if duplicate is not None:
            await db.rollback()
            raise EvaluationBlockedError("evaluation attempt already exists")
        db.add(
            AIUsageReservation(
                organization_id=organization_id,
                reservation_key=key,
                period_start=month_start(),
                status=STATUS_RESERVED,
                estimated_cost_usd=self.policy.reserved_cost_per_attempt,
                expires_at=datetime.now(UTC) + timedelta(days=30),
            )
        )
        await db.commit()  # durable before transport dispatch
        try:
            await self.session_guard.assert_generation_allowed(
                db,
                organization_id=organization_id,
                session_id=public_chat_session_id,
            )
        except EvaluationBlockedError:
            await db.execute(
                update(AIUsageReservation)
                .where(AIUsageReservation.reservation_key == key)
                .values(status=STATUS_SETTLED, settled_at=datetime.now(UTC))
            )
            await db.commit()
            raise

        try:
            async with asyncio.timeout(self.DISCONNECTED_TRANSPORT_TIMEOUT_SECONDS):
                result = await self.transport.send(
                    model=self.policy.model,
                    serialized_input=serialized_input,
                    max_output_tokens=self.policy.max_output_tokens,
                    service_tier=self.policy.service_tier,
                    cache_retention=self.policy.cache_retention,
                    sdk_max_retries=0,
                    request_payload=canonical_request_payload,
                )
        except ConfirmedNoDispatchError:
            await db.execute(
                update(AIUsageReservation)
                .where(AIUsageReservation.reservation_key == key)
                .values(status=STATUS_SETTLED, settled_at=datetime.now(UTC))
            )
            await db.commit()
            raise
        except Exception:  # noqa: BLE001 - preserve reservation for every unknown outcome
            await (
                db.rollback()
            )  # release the session row lock; reservation stays durable
            raise AmbiguousModelOutcome(
                "unconfirmed provider outcome retained for reconciliation"
            ) from None
        if (
            not isinstance(result, EvaluationTransportResult)
            or isinstance(result.input_tokens, bool)
            or isinstance(result.output_tokens, bool)
            or result.input_tokens <= 0
            or result.output_tokens <= 0
            or result.input_tokens > self.policy.max_input_tokens
            or result.output_tokens > self.policy.max_output_tokens
            or result.input_tokens != admitted_input_tokens
        ):
            await db.rollback()
            raise AmbiguousModelOutcome(
                "provider usage is missing, malformed, or exceeds the approved caps"
            )
        await db.execute(
            update(AIUsageReservation)
            .where(AIUsageReservation.reservation_key == key)
            .values(
                status=STATUS_SETTLED,
                settled_at=datetime.now(UTC),
            )
        )
        await db.commit()
        return result.text

    async def _admit_and_count(
        self,
        db: AsyncSession,
        *,
        organization_id: int,
        run_id: str,
        attempt_suffix: str,
        serialized_input: str,
        canonical_request_payload: dict[str, Any] | None,
    ) -> int:
        if self.count_transport is None or self.count_policy is None:
            assert self.count_tokens is not None
            return self.count_tokens(serialized_input)

        if not isinstance(canonical_request_payload, dict):
            raise EvaluationBlockedError(
                "the canonical full Responses request is required"
            )
        if (
            canonical_request_payload.get("model") != self.policy.model
            or canonical_request_payload.get("input") != serialized_input
            or canonical_request_payload.get("service_tier") != self.policy.service_tier
            or canonical_request_payload.get("max_output_tokens")
            != self.policy.max_output_tokens
        ):
            raise EvaluationBlockedError(
                "canonical Responses request does not match policy"
            )
        request_sha256 = hashlib.sha256(
            json.dumps(
                canonical_request_payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

        prefix = f"{self.COUNT_PREFIX}{organization_id}:{run_id}:"
        generation_prefix = f"{self.PREFIX}{organization_id}:{run_id}:"
        key = f"{prefix}{attempt_suffix}"
        await db.execute(select(func.pg_advisory_xact_lock(AI_BUDGET_LOCK_KEY)))
        unresolved = await db.scalar(
            select(func.count())
            .select_from(AIUsageReservation)
            .where(
                AIUsageReservation.organization_id == organization_id,
                (
                    AIUsageReservation.reservation_key.like(f"{prefix}%")
                    | AIUsageReservation.reservation_key.like(f"{generation_prefix}%")
                ),
                AIUsageReservation.status == STATUS_RESERVED,
            )
        )
        if unresolved:
            await db.rollback()
            raise EvaluationBlockedError(
                "an unresolved attempt requires reconciliation"
            )
        used = int(
            await db.scalar(
                select(func.count())
                .select_from(AIUsageReservation)
                .where(
                    AIUsageReservation.organization_id == organization_id,
                    AIUsageReservation.reservation_key.like(f"{prefix}%"),
                )
            )
            or 0
        )
        if used >= self.policy.max_attempts:
            await db.rollback()
            raise EvaluationBlockedError("count attempt cap exceeded")
        duplicate = await db.scalar(
            select(AIUsageReservation.id).where(
                AIUsageReservation.reservation_key == key
            )
        )
        if duplicate is not None:
            await db.rollback()
            raise EvaluationBlockedError("evaluation count attempt already exists")
        previously_reserved = float(
            await db.scalar(
                select(
                    func.coalesce(func.sum(AIUsageReservation.estimated_cost_usd), 0)
                ).where(
                    AIUsageReservation.organization_id == organization_id,
                    (
                        AIUsageReservation.reservation_key.like(f"{prefix}%")
                        | AIUsageReservation.reservation_key.like(
                            f"{generation_prefix}%"
                        )
                    ),
                )
            )
            or 0
        )
        combined = (
            self.policy.additional_all_in_reserve_usd
            + previously_reserved
            + self.count_policy.reservation_per_attempt_usd
        )
        if combined >= self.policy.cost_ceiling_usd:
            await db.rollback()
            raise EvaluationBlockedError(
                "count reservation lacks positive budget headroom"
            )
        db.add(
            AIUsageReservation(
                organization_id=organization_id,
                reservation_key=key,
                period_start=month_start(),
                status=STATUS_RESERVED,
                estimated_cost_usd=self.count_policy.reservation_per_attempt_usd,
                expires_at=datetime.now(UTC) + timedelta(days=30),
            )
        )
        await db.commit()
        try:
            async with asyncio.timeout(self.DISCONNECTED_TRANSPORT_TIMEOUT_SECONDS):
                count_result = await self.count_transport.count(
                    model=self.policy.model,
                    request_payload=canonical_request_payload,
                    sdk_max_retries=0,
                )
        except ConfirmedNoDispatchError:
            await db.execute(
                update(AIUsageReservation)
                .where(AIUsageReservation.reservation_key == key)
                .values(status=STATUS_SETTLED, settled_at=datetime.now(UTC))
            )
            await db.commit()
            raise
        except Exception:  # noqa: BLE001 - retain reservation on unknown count outcome
            await db.rollback()
            raise AmbiguousModelOutcome(
                "unconfirmed input-token count outcome retained for reconciliation"
            ) from None
        if (
            not isinstance(count_result, ExactInputTokenCountResult)
            or not isinstance(count_result.input_tokens, int)
            or isinstance(count_result.input_tokens, bool)
            or count_result.input_tokens <= 0
            or count_result.request_sha256 != request_sha256
        ):
            await db.rollback()
            raise AmbiguousModelOutcome(
                "input-token count result is missing or malformed"
            )
        await db.execute(
            update(AIUsageReservation)
            .where(AIUsageReservation.reservation_key == key)
            .values(status=STATUS_SETTLED, settled_at=datetime.now(UTC))
        )
        await db.commit()
        return count_result.input_tokens
