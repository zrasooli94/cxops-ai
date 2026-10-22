"""Provider-disconnected safety controller for a future approved model evaluation.

The controller has no OpenAI client and cannot create credentials. Tests supply an
in-memory transport and an exact token counter. A real adapter must remain absent
until the owner separately approves the run and configures a restricted key.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

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
        self, *, model: str, serialized_input: str, max_output_tokens: int,
        service_tier: str, cache_retention: str, sdk_max_retries: int,
    ) -> str: ...


class ExactTokenCounter(Protocol):
    model: str

    def count(self, serialized_input: str) -> int: ...


class SessionDispatchGuard(Protocol):
    async def assert_generation_allowed(
        self, db: AsyncSession, *, organization_id: int, session_id: int,
    ) -> None: ...


class PersistedPublicChatSessionGuard:
    """Linearize mocked dispatch against staff handoff on the session row.

    The row lock remains owned by the caller's transaction until the controller
    settles or rolls back after transport. A handoff that wins first is observed;
    a handoff that starts later waits until this bounded dispatch completes.
    """

    async def assert_generation_allowed(
        self, db: AsyncSession, *, organization_id: int, session_id: int,
    ) -> None:
        status = await db.scalar(
            select(PublicChatSession.status).where(
                PublicChatSession.id == session_id,
                PublicChatSession.organization_id == organization_id,
            ).with_for_update()
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
    input_rate_per_million: float = 0.125
    output_rate_per_million: float = 0.50
    cost_ceiling_usd: float = 0.10

    @property
    def reserved_cost_per_attempt(self) -> float:
        return (
            self.max_input_tokens * self.input_rate_per_million
            + self.max_output_tokens * self.output_rate_per_million
        ) / 1_000_000

    def validate(self) -> None:
        if self.model != "gpt-6-luna" or self.service_tier != "default":
            raise EvaluationBlockedError("unapproved model or service tier")
        if self.cache_retention != "30m":
            raise EvaluationBlockedError("only the approved 30m cache policy is allowed")
        if (
            self.input_rate_per_million != 0.125
            or self.output_rate_per_million != 0.50
            or self.cost_ceiling_usd != 0.10
            or self.max_input_tokens != 3000
            or self.max_output_tokens != 500
        ):
            raise EvaluationBlockedError("the approved pricing and limits are required")
        worst_case = self.max_attempts * self.reserved_cost_per_attempt
        if self.max_turns != 50 or self.max_attempts != 100 or worst_case >= self.cost_ceiling_usd:
            raise EvaluationBlockedError("evaluation requires positive cost headroom")


class ModelEvaluationController:
    PREFIX = "model-eval:"
    DISCONNECTED_TRANSPORT_TIMEOUT_SECONDS = 30

    def __init__(
        self, *, policy: EvaluationPolicy, exact_token_counter: ExactTokenCounter | None,
        transport: EvaluationTransport, explicitly_approved: bool, approved_run_id: str,
        approved_organization_id: int, session_guard: SessionDispatchGuard,
    ) -> None:
        policy.validate()
        if not explicitly_approved:
            raise EvaluationBlockedError("evaluation approval is required")
        if exact_token_counter is None or exact_token_counter.model != policy.model:
            raise EvaluationBlockedError("an exact tokenizer is required")
        if not transport.provider_disconnected:
            raise EvaluationBlockedError("real provider transport is not enabled")
        if re.fullmatch(r"[A-Za-z0-9-]{1,64}", approved_run_id) is None:
            raise EvaluationBlockedError("invalid approved evaluation id")
        self.policy = policy
        self.count_tokens = exact_token_counter.count
        self.transport = transport
        self.approved_run_id = approved_run_id
        self.approved_organization_id = approved_organization_id
        self.session_guard = session_guard

    async def attempt(
        self, db: AsyncSession, *, organization_id: int, run_id: str,
        turn_number: int, retry_number: int, serialized_input: str,
        public_chat_session_id: int,
    ) -> str:
        if run_id != self.approved_run_id or organization_id != self.approved_organization_id:
            raise EvaluationBlockedError("evaluation run is not approved")
        if turn_number < 1 or turn_number > self.policy.max_turns:
            raise EvaluationBlockedError("turn cap exceeded")
        if retry_number not in {0, 1}:
            raise EvaluationBlockedError("one retry per turn is allowed")
        if self.count_tokens(serialized_input) > self.policy.max_input_tokens:
            raise EvaluationBlockedError("full serialized input exceeds token cap")

        prefix = f"{self.PREFIX}{organization_id}:{run_id}:"
        await db.execute(select(func.pg_advisory_xact_lock(AI_BUDGET_LOCK_KEY)))
        unresolved = await db.scalar(select(func.count()).select_from(AIUsageReservation).where(
            AIUsageReservation.reservation_key.like(f"{prefix}%"),
            AIUsageReservation.status == STATUS_RESERVED,
        ))
        if unresolved:
            await db.rollback()
            raise EvaluationBlockedError("an unresolved attempt requires reconciliation")
        used = int(await db.scalar(select(func.count()).select_from(AIUsageReservation).where(
            AIUsageReservation.reservation_key.like(f"{prefix}%")
        )) or 0)
        if used >= self.policy.max_attempts:
            await db.rollback()
            raise EvaluationBlockedError("attempt cap exceeded")
        reserved_total = (used + 1) * self.policy.reserved_cost_per_attempt
        if reserved_total >= self.policy.cost_ceiling_usd:
            await db.rollback()
            raise EvaluationBlockedError("cost ceiling requires positive headroom")

        key = f"{prefix}turn-{turn_number}:attempt-{retry_number + 1}"
        db.add(AIUsageReservation(
            organization_id=organization_id,
            reservation_key=key,
            period_start=month_start(),
            status=STATUS_RESERVED,
            estimated_cost_usd=self.policy.reserved_cost_per_attempt,
            expires_at=datetime.now(UTC) + timedelta(days=30),
        ))
        await db.commit()  # durable before transport dispatch
        try:
            await self.session_guard.assert_generation_allowed(
                db,
                organization_id=organization_id,
                session_id=public_chat_session_id,
            )
        except EvaluationBlockedError:
            await db.execute(update(AIUsageReservation).where(
                AIUsageReservation.reservation_key == key
            ).values(status=STATUS_SETTLED, settled_at=datetime.now(UTC)))
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
                )
        except ConfirmedNoDispatchError:
            await db.execute(update(AIUsageReservation).where(
                AIUsageReservation.reservation_key == key
            ).values(status=STATUS_SETTLED, settled_at=datetime.now(UTC)))
            await db.commit()
            raise
        except Exception as exc:
            await db.rollback()  # release the session row lock; reservation stays durable
            raise AmbiguousModelOutcome(
                "unconfirmed provider outcome retained for reconciliation"
            ) from exc
        await db.execute(update(AIUsageReservation).where(
            AIUsageReservation.reservation_key == key
        ).values(status=STATUS_SETTLED, settled_at=datetime.now(UTC)))
        await db.commit()
        return result
