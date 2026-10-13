"""Per-tenant and platform AI allowances (Phase 1P.9, hardened in 1P.9R).

Every model call writes a tenant-attributed ``ai_request_logs`` row with tokens
and an *estimated* cost. Phase 1P.9 only *read* month-to-date usage before a
turn, so N turns admitted at the same moment could all pass a nearly-exhausted
check and overspend by N turns. Phase 1P.9R makes admission a **reservation**:

* ``reserve`` runs under one transaction-scoped advisory lock (held until the
  reservation commits), so admissions are serialised across every API
  instance and worker that shares the database;
* it counts what is already spent (logged costs + a fixed allowance for
  unmetered embeddings per settled turn) plus every still-open reservation's
  estimate, and admits the turn only if its own estimate also fits - for the
  tenant AND for the platform-wide ceiling;
* reservations are keyed by the turn's idempotency key, so a retried or
  duplicated turn reuses its reservation instead of reserving twice;
* a crashed turn's reservation stops counting after
  ``public_chat_reservation_ttl_seconds``; any model call it made is logged.

Exhausting an allowance never drops a message: the turn hands the conversation
to a human without a model call. All figures are estimates; provider billing
(and the provider-side hard limit) is the authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.ai_request_log import AIRequestLog
from app.models.ai_usage_reservation import AIUsageReservation
from app.models.public_chat import PublicChatConfiguration

# Kept for reporting: one agent decision log row per AI turn.
TURN_FEATURE = "agent_decision"
# One fixed advisory lock serialises all AI admissions (tenant + platform
# checks must see each other's reservations). Held for a few short queries.
AI_BUDGET_LOCK_KEY = 0x4149_4255_4447_4554  # "AIBUDGET"
# Embeddings are not metered per call (the client returns no usage); this
# fixed estimate per settled turn keeps them inside the budget arithmetic.
UNMETERED_COST_PER_TURN_USD = 0.0001

STATUS_RESERVED = "reserved"
STATUS_SETTLED = "settled"


@dataclass(frozen=True)
class AllowanceDecision:
    allowed: bool
    # "within_allowance" | "already_reserved" | "turn_limit_reached"
    # | "budget_reached" | "platform_budget_reached" | "organization_not_active"
    reason: str
    turns_used: int
    turn_limit: int
    cost_used_usd: float
    budget_usd: float
    period_start: datetime
    outstanding_usd: float = 0.0

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "turns_used": self.turns_used,
            "turn_limit": self.turn_limit,
            "estimated_cost_used_usd": round(self.cost_used_usd, 4),
            "estimated_outstanding_usd": round(self.outstanding_usd, 4),
            "budget_usd": round(self.budget_usd, 2),
            "period_start": self.period_start.isoformat(),
            "cost_basis": "estimated",
        }


def month_start(now: datetime | None = None) -> datetime:
    moment = now or datetime.now(UTC)
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def limits_for(configuration: PublicChatConfiguration | None) -> tuple[int, float]:
    """(turn_limit, budget_usd): the tenant override, else platform defaults."""
    turn_limit = settings.public_chat_default_monthly_ai_turns
    budget = settings.public_chat_default_monthly_ai_budget_usd
    if configuration is not None:
        if configuration.monthly_ai_turn_limit is not None:
            turn_limit = int(configuration.monthly_ai_turn_limit)
        if configuration.monthly_ai_budget_usd is not None:
            budget = float(configuration.monthly_ai_budget_usd)
    return turn_limit, budget


def reservation_key(*, organization_id: int, session_id: int, client_message_id: str) -> str:
    return f"ai-turn:{organization_id}:{session_id}:{client_message_id}"


@dataclass(frozen=True)
class _Usage:
    turns: int
    spent_usd: float  # logged + unmetered allowance for settled turns
    outstanding_usd: float  # open, unexpired reservations


class TenantAIAllowanceService:
    @staticmethod
    async def _usage(
        db: AsyncSession,
        *,
        organization_id: int | None,
        period_start: datetime,
        now: datetime,
    ) -> _Usage:
        """Month-to-date usage for one tenant, or the platform when ``None``."""
        log_query = select(
            func.coalesce(func.sum(AIRequestLog.estimated_cost_usd), 0.0)
        ).where(AIRequestLog.created_at >= period_start)
        reservation_query = select(
            func.count(),
            func.count().filter(AIUsageReservation.status == STATUS_SETTLED),
            func.coalesce(
                func.sum(AIUsageReservation.estimated_cost_usd).filter(
                    AIUsageReservation.status == STATUS_RESERVED,
                    AIUsageReservation.expires_at > now,
                ),
                0,
            ),
        ).where(AIUsageReservation.period_start == period_start)
        if organization_id is not None:
            log_query = log_query.where(AIRequestLog.organization_id == organization_id)
            reservation_query = reservation_query.where(
                AIUsageReservation.organization_id == organization_id
            )
        logged = float((await db.execute(log_query)).scalar_one() or 0.0)
        turns, settled, outstanding = (await db.execute(reservation_query)).one()
        return _Usage(
            turns=int(turns or 0),
            spent_usd=logged + int(settled or 0) * UNMETERED_COST_PER_TURN_USD,
            outstanding_usd=float(outstanding or 0.0),
        )

    @classmethod
    async def reserve(
        cls,
        db: AsyncSession,
        *,
        organization_id: int,
        configuration: PublicChatConfiguration | None,
        key: str,
        now: datetime | None = None,
    ) -> AllowanceDecision:
        """Admit (and reserve) one AI turn, or refuse it. Commits.

        Must be called with no uncommitted work on ``db``: it commits to make
        the reservation visible to every other instance and release the lock.
        """
        moment = now or datetime.now(UTC)
        period_start = month_start(moment)
        turn_limit, budget = limits_for(configuration)
        estimate = settings.public_chat_turn_reserved_cost_usd

        await db.execute(select(func.pg_advisory_xact_lock(AI_BUDGET_LOCK_KEY)))
        try:
            # Phase 1Q: only an operator-approved (``active``) organization may
            # spend AI allowance. Checked here - the one chokepoint before every
            # public-chat model call - so a turn accepted just before a tenant
            # was suspended still cannot reach the model.
            from app.models.organization import LIFECYCLE_ACTIVE, Organization

            organization_status = await db.scalar(
                select(Organization.lifecycle_status).where(
                    Organization.id == organization_id
                )
            )
            if organization_status != LIFECYCLE_ACTIVE:
                usage = await cls._usage(
                    db, organization_id=organization_id, period_start=period_start, now=moment
                )
                await db.commit()
                return AllowanceDecision(
                    allowed=False,
                    reason="organization_not_active",
                    turns_used=usage.turns,
                    turn_limit=turn_limit,
                    cost_used_usd=usage.spent_usd,
                    budget_usd=budget,
                    period_start=period_start,
                    outstanding_usd=usage.outstanding_usd,
                )
            existing = (
                await db.execute(
                    select(AIUsageReservation).where(
                        AIUsageReservation.reservation_key == key
                    )
                )
            ).scalar_one_or_none()
            tenant = await cls._usage(
                db, organization_id=organization_id, period_start=period_start, now=moment
            )
            if existing is not None:
                reason, allowed = "already_reserved", True
            elif tenant.turns >= turn_limit:
                reason, allowed = "turn_limit_reached", False
            elif tenant.spent_usd + tenant.outstanding_usd + estimate > budget:
                reason, allowed = "budget_reached", False
            else:
                platform = await cls._usage(
                    db, organization_id=None, period_start=period_start, now=moment
                )
                if (
                    platform.spent_usd + platform.outstanding_usd + estimate
                    > settings.platform_monthly_ai_budget_usd
                ):
                    reason, allowed = "platform_budget_reached", False
                else:
                    reason, allowed = "within_allowance", True
                    db.add(
                        AIUsageReservation(
                            organization_id=organization_id,
                            reservation_key=key,
                            period_start=period_start,
                            status=STATUS_RESERVED,
                            estimated_cost_usd=estimate,
                            expires_at=moment
                            + timedelta(seconds=settings.public_chat_reservation_ttl_seconds),
                        )
                    )
            await db.commit()
        except BaseException:
            await db.rollback()
            raise
        return AllowanceDecision(
            allowed=allowed,
            reason=reason,
            turns_used=tenant.turns + (1 if reason == "within_allowance" else 0),
            turn_limit=turn_limit,
            cost_used_usd=tenant.spent_usd,
            budget_usd=budget,
            period_start=period_start,
            outstanding_usd=tenant.outstanding_usd
            + (estimate if reason == "within_allowance" else 0.0),
        )

    @staticmethod
    async def settle(db: AsyncSession, *, key: str, now: datetime | None = None) -> None:
        """The turn finished (reply, handoff or terminal failure): its real,
        logged cost replaces the reservation estimate. Idempotent. Commits."""
        await db.execute(
            update(AIUsageReservation)
            .where(
                AIUsageReservation.reservation_key == key,
                AIUsageReservation.status == STATUS_RESERVED,
            )
            .values(status=STATUS_SETTLED, settled_at=now or datetime.now(UTC))
        )
        await db.commit()

    @classmethod
    async def check(
        cls,
        db: AsyncSession,
        *,
        organization_id: int,
        configuration: PublicChatConfiguration | None,
        now: datetime | None = None,
    ) -> AllowanceDecision:
        """Read-only view of the allowance (reporting; never admits a turn)."""
        moment = now or datetime.now(UTC)
        period_start = month_start(moment)
        usage = await cls._usage(
            db, organization_id=organization_id, period_start=period_start, now=moment
        )
        turn_limit, budget = limits_for(configuration)
        if usage.turns >= turn_limit:
            reason, allowed = "turn_limit_reached", False
        elif usage.spent_usd + usage.outstanding_usd >= budget:
            reason, allowed = "budget_reached", False
        else:
            reason, allowed = "within_allowance", True
        return AllowanceDecision(
            allowed=allowed,
            reason=reason,
            turns_used=usage.turns,
            turn_limit=turn_limit,
            cost_used_usd=usage.spent_usd,
            budget_usd=budget,
            period_start=period_start,
            outstanding_usd=usage.outstanding_usd,
        )


async def staff_ai_spend_refusal(
    db: AsyncSession, *, organization_id: int, now: datetime | None = None
) -> str | None:
    """Phase 1Q-R: gate for staff-triggered AI calls (agent analysis,
    knowledge answer/search/ingestion, evaluations).

    Public-chat turns reserve against the allowance; staff calls did not, so
    an active tenant's staff could spend without limit. Their cost is logged
    to the same tenant ledger, so the same month-to-date figures apply: the
    call is refused once the tenant's estimated budget or the platform
    ceiling is used up. Returns a refusal reason, or None when allowed.
    """
    moment = now or datetime.now(UTC)
    period_start = month_start(moment)
    configuration = (
        await db.execute(
            select(PublicChatConfiguration)
            .where(PublicChatConfiguration.organization_id == organization_id)
            .order_by(PublicChatConfiguration.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    _turns, budget = limits_for(configuration)
    tenant = await TenantAIAllowanceService._usage(
        db, organization_id=organization_id, period_start=period_start, now=moment
    )
    if tenant.spent_usd + tenant.outstanding_usd >= budget:
        return "budget_reached"
    platform = await TenantAIAllowanceService._usage(
        db, organization_id=None, period_start=period_start, now=moment
    )
    if platform.spent_usd + platform.outstanding_usd >= settings.platform_monthly_ai_budget_usd:
        return "platform_budget_reached"
    return None


class TenantUsageService:
    """Tenant-scoped usage and service-health summary for staff (Phase 1P.9)."""

    @staticmethod
    async def report(db: AsyncSession, *, organization_id: int) -> dict:
        from app.models.integration_job import IntegrationJob

        configuration = (
            await db.execute(
                select(PublicChatConfiguration)
                .where(PublicChatConfiguration.organization_id == organization_id)
                .order_by(PublicChatConfiguration.id)
                .limit(1)
            )
        ).scalar_one_or_none()
        allowance = await TenantAIAllowanceService.check(
            db, organization_id=organization_id, configuration=configuration
        )

        since = datetime.now(UTC) - timedelta(hours=24)
        turn_type = "public_chat.turn"
        latency = (IntegrationJob.completed_at - IntegrationJob.created_at)
        seconds = func.extract("epoch", latency)
        row = (
            await db.execute(
                select(
                    func.count().filter(IntegrationJob.status == "completed"),
                    func.count().filter(IntegrationJob.status == "failed"),
                    func.count().filter(
                        IntegrationJob.status.in_(("pending", "retry", "processing"))
                    ),
                    func.percentile_cont(0.5)
                    .within_group(seconds)
                    .filter(IntegrationJob.status == "completed"),
                    func.percentile_cont(0.95)
                    .within_group(seconds)
                    .filter(IntegrationJob.status == "completed"),
                ).where(
                    IntegrationJob.organization_id == organization_id,
                    IntegrationJob.job_type == turn_type,
                    IntegrationJob.created_at >= since,
                )
            )
        ).one()
        return {
            "allowance": allowance.as_dict(),
            "chat_turns_24h": {
                "completed": int(row[0] or 0),
                "failed": int(row[1] or 0),
                "open": int(row[2] or 0),
                "p50_seconds": round(float(row[3]), 2) if row[3] is not None else None,
                "p95_seconds": round(float(row[4]), 2) if row[4] is not None else None,
            },
        }
