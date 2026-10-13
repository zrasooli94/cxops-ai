"""Per-tenant monthly AI allowance (Phase 1P.9).

Every model call already writes a tenant-attributed ``ai_request_logs`` row
with tokens and an estimated cost (``agent_decision`` for the agent workflow,
the RAG feature rows for knowledge answers). This service turns those rows into
a month-to-date usage figure per tenant and decides whether a new public-chat
AI turn may run.

Exhausting the allowance never drops a customer message: the message is still
accepted and the conversation is handed to a human, with no model call. This
is what bounds AI spend per business and keeps one noisy tenant from consuming
the platform's model budget.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.ai_request_log import AIRequestLog
from app.models.public_chat import PublicChatConfiguration

# Rows that represent one AI "turn" (one agent decision). RAG rows add cost but
# are part of the same turn, so only decisions are counted as turns.
TURN_FEATURE = "agent_decision"


@dataclass(frozen=True)
class AllowanceDecision:
    allowed: bool
    reason: str  # "within_allowance" | "turn_limit_reached" | "budget_reached"
    turns_used: int
    turn_limit: int
    cost_used_usd: float
    budget_usd: float
    period_start: datetime

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "turns_used": self.turns_used,
            "turn_limit": self.turn_limit,
            "cost_used_usd": round(self.cost_used_usd, 4),
            "budget_usd": round(self.budget_usd, 2),
            "period_start": self.period_start.isoformat(),
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


class TenantAIAllowanceService:
    @staticmethod
    async def usage(
        db: AsyncSession,
        *,
        organization_id: int,
        since: datetime,
    ) -> tuple[int, float]:
        """(turns, estimated_cost_usd) for one tenant since ``since``."""
        row = (
            await db.execute(
                select(
                    func.count().filter(AIRequestLog.feature == TURN_FEATURE),
                    func.coalesce(func.sum(AIRequestLog.estimated_cost_usd), 0.0),
                ).where(
                    AIRequestLog.organization_id == organization_id,
                    AIRequestLog.created_at >= since,
                )
            )
        ).one()
        return int(row[0] or 0), float(row[1] or 0.0)

    @classmethod
    async def check(
        cls,
        db: AsyncSession,
        *,
        organization_id: int,
        configuration: PublicChatConfiguration | None,
        now: datetime | None = None,
    ) -> AllowanceDecision:
        period_start = month_start(now)
        turns, cost = await cls.usage(db, organization_id=organization_id, since=period_start)
        turn_limit, budget = limits_for(configuration)
        if turns >= turn_limit:
            reason, allowed = "turn_limit_reached", False
        elif cost >= budget:
            reason, allowed = "budget_reached", False
        else:
            reason, allowed = "within_allowance", True
        return AllowanceDecision(
            allowed=allowed,
            reason=reason,
            turns_used=turns,
            turn_limit=turn_limit,
            cost_used_usd=cost,
            budget_usd=budget,
            period_start=period_start,
        )


class TenantUsageService:
    """Tenant-scoped usage and service-health summary for staff (Phase 1P.9)."""

    @staticmethod
    async def report(db: AsyncSession, *, organization_id: int) -> dict:
        from datetime import timedelta

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
