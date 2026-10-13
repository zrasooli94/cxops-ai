"""Cross-tenant administration for platform operators (Phase 1Q).

Every method here is reachable only through ``RequirePlatformOperator``.
Tenant owners never reach this module, whatever their role.

Costs reported here are the application's *estimates* (``ai_request_logs``
plus open reservations). The AI provider's invoice is the authority and is not
visible to the application; responses say so in ``cost_basis``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.ai_request_log import AIRequestLog
from app.models.ai_usage_reservation import AIUsageReservation
from app.models.organization import (
    LIFECYCLE_ACTIVE,
    LIFECYCLE_ONBOARDING,
    LIFECYCLE_PENDING_REVIEW,
    LIFECYCLE_SUSPENDED,
    Organization,
)
from app.models.organization_membership import OrganizationMembership
from app.models.public_chat import PublicChatConfiguration
from app.services.tenant_ai_allowance_service import (
    STATUS_RESERVED,
    STATUS_SETTLED,
    limits_for,
    month_start,
)

log = get_logger(__name__)

# action -> (allowed from, to)
LIFECYCLE_ACTIONS: dict[str, tuple[frozenset[str], str]] = {
    "approve": (frozenset({LIFECYCLE_ONBOARDING, LIFECYCLE_PENDING_REVIEW}), LIFECYCLE_ACTIVE),
    "request_changes": (frozenset({LIFECYCLE_PENDING_REVIEW}), LIFECYCLE_ONBOARDING),
    "suspend": (
        frozenset({LIFECYCLE_ONBOARDING, LIFECYCLE_PENDING_REVIEW, LIFECYCLE_ACTIVE}),
        LIFECYCLE_SUSPENDED,
    ),
    "reinstate": (frozenset({LIFECYCLE_SUSPENDED}), LIFECYCLE_ACTIVE),
}

MAX_TURN_LIMIT = 1_000_000
MAX_BUDGET_USD = Decimal(10000)
PLAN_CODE_MAX = 40

COST_BASIS_NOTE = (
    "Estimated by the application from logged token usage and open reservations. "
    "The AI provider's invoice is authoritative and may differ."
)


class PlatformAdminError(Exception):
    status_code = 400


class PlatformAdminNotFoundError(PlatformAdminError):
    status_code = 404


class PlatformAdminConflictError(PlatformAdminError):
    status_code = 409


async def _usage_by_tenant(
    db: AsyncSession, period_start: datetime, now: datetime
) -> tuple[dict[int, float], dict[int, int], dict[int, float]]:
    spent = dict(
        (
            await db.execute(
                select(
                    AIRequestLog.organization_id,
                    func.coalesce(func.sum(AIRequestLog.estimated_cost_usd), 0.0),
                )
                .where(AIRequestLog.created_at >= period_start)
                .group_by(AIRequestLog.organization_id)
            )
        ).all()
    )
    turns_rows = (
        await db.execute(
            select(
                AIUsageReservation.organization_id,
                func.count(),
                func.coalesce(
                    func.sum(AIUsageReservation.estimated_cost_usd).filter(
                        AIUsageReservation.status == STATUS_RESERVED,
                        AIUsageReservation.expires_at > now,
                    ),
                    0,
                ),
            )
            .where(AIUsageReservation.period_start == period_start)
            .group_by(AIUsageReservation.organization_id)
        )
    ).all()
    turns = {row[0]: int(row[1]) for row in turns_rows}
    outstanding = {row[0]: float(row[2] or 0) for row in turns_rows}
    return (
        {k: float(v or 0) for k, v in spent.items() if k is not None},
        turns,
        outstanding,
    )


class PlatformAdminService:
    @staticmethod
    async def list_organizations(db: AsyncSession) -> dict[str, Any]:
        now = datetime.now(UTC)
        period_start = month_start(now)
        organizations = list(
            (await db.execute(select(Organization).order_by(Organization.id))).scalars()
        )
        member_counts = dict(
            (
                await db.execute(
                    select(OrganizationMembership.organization_id, func.count()).group_by(
                        OrganizationMembership.organization_id
                    )
                )
            ).all()
        )
        configs = {
            row.organization_id: row
            for row in (
                await db.execute(
                    select(PublicChatConfiguration).order_by(PublicChatConfiguration.id)
                )
            ).scalars()
        }
        spent, turns, outstanding = await _usage_by_tenant(db, period_start, now)

        items = []
        for org in organizations:
            config = configs.get(org.id)
            turn_limit, budget = limits_for(config)
            items.append(
                {
                    "id": org.id,
                    "name": org.name,
                    "industry": org.industry,
                    "website_url": org.website_url,
                    "lifecycle_status": org.lifecycle_status,
                    "plan_code": org.plan_code,
                    "created_at": org.created_at.isoformat() if org.created_at else None,
                    "submitted_for_review_at": org.submitted_for_review_at.isoformat()
                    if org.submitted_for_review_at
                    else None,
                    "members": int(member_counts.get(org.id, 0)),
                    "widget": None
                    if config is None
                    else {
                        "enabled": config.enabled,
                        "origins": len(config.allowed_origins or []),
                        "allowance_override": config.monthly_ai_turn_limit is not None
                        or config.monthly_ai_budget_usd is not None,
                    },
                    "usage": {
                        "turns": turns.get(org.id, 0),
                        "turn_limit": turn_limit,
                        "estimated_cost_usd": round(spent.get(org.id, 0.0), 4),
                        "estimated_outstanding_usd": round(outstanding.get(org.id, 0.0), 4),
                        "budget_usd": round(budget, 2),
                    },
                    "allowed_actions": sorted(
                        action
                        for action, (sources, _target) in LIFECYCLE_ACTIONS.items()
                        if org.lifecycle_status in sources
                    ),
                }
            )
        return {"organizations": items, "period_start": period_start.isoformat()}

    @staticmethod
    async def change_lifecycle(
        db: AsyncSession, *, organization_id: int, action: str, operator_subject: str
    ) -> dict[str, Any]:
        if action not in LIFECYCLE_ACTIONS:
            raise PlatformAdminError(f"unknown action {action!r}")
        sources, target = LIFECYCLE_ACTIONS[action]
        organization = (
            await db.execute(
                select(Organization).where(Organization.id == organization_id).with_for_update()
            )
        ).scalar_one_or_none()
        if organization is None:
            await db.rollback()
            raise PlatformAdminNotFoundError("Organization not found.")
        previous = organization.lifecycle_status
        if previous not in sources:
            await db.rollback()
            raise PlatformAdminConflictError(f"Cannot {action} a {previous} organization.")
        now = datetime.now(UTC)
        organization.lifecycle_status = target
        organization.lifecycle_changed_at = now
        organization.lifecycle_changed_by = operator_subject
        if target == LIFECYCLE_ONBOARDING:
            organization.submitted_for_review_at = None
        await db.commit()
        log.info(
            "organization_lifecycle_changed",
            organization_id=organization_id,
            operator_subject=operator_subject,
            action=action,
            from_status=previous,
            to_status=target,
        )
        return {"id": organization_id, "lifecycle_status": target}

    @staticmethod
    async def set_entitlements(
        db: AsyncSession,
        *,
        organization_id: int,
        operator_subject: str,
        changes: dict[str, Any],
    ) -> dict[str, Any]:
        organization = await db.get(Organization, organization_id)
        if organization is None:
            raise PlatformAdminNotFoundError("Organization not found.")
        changed: list[str] = []
        if "plan_code" in changes:
            plan = (changes["plan_code"] or "").strip() or None
            if plan is not None and (
                len(plan) > PLAN_CODE_MAX
                or not all(ch.isalnum() or ch in "-_" for ch in plan)
            ):
                raise PlatformAdminError("plan_code must be letters, digits, '-' or '_'")
            organization.plan_code = plan
            changed.append("plan_code")
        allowance_fields = {"monthly_ai_turn_limit", "monthly_ai_budget_usd"} & set(changes)
        if allowance_fields:
            config = (
                await db.execute(
                    select(PublicChatConfiguration)
                    .where(PublicChatConfiguration.organization_id == organization_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if config is None:
                await db.rollback()
                raise PlatformAdminConflictError(
                    "The organization has no chatbot yet; allowances live on its configuration."
                )
            if "monthly_ai_turn_limit" in changes:
                value = changes["monthly_ai_turn_limit"]
                if value is not None and not (0 <= int(value) <= MAX_TURN_LIMIT):
                    await db.rollback()
                    raise PlatformAdminError(f"turn limit must be 0-{MAX_TURN_LIMIT}")
                config.monthly_ai_turn_limit = None if value is None else int(value)
                changed.append("monthly_ai_turn_limit")
            if "monthly_ai_budget_usd" in changes:
                value = changes["monthly_ai_budget_usd"]
                if value is not None:
                    amount = Decimal(str(value))
                    if not (Decimal(0) <= amount <= MAX_BUDGET_USD):
                        await db.rollback()
                        raise PlatformAdminError(f"budget must be 0-{MAX_BUDGET_USD}")
                    config.monthly_ai_budget_usd = float(amount.quantize(Decimal("0.01")))
                else:
                    config.monthly_ai_budget_usd = None
                changed.append("monthly_ai_budget_usd")
        await db.commit()
        log.info(
            "organization_entitlements_changed",
            organization_id=organization_id,
            operator_subject=operator_subject,
            changed_fields=sorted(changed),
        )
        return {"id": organization_id, "changed_fields": sorted(changed)}

    @staticmethod
    async def platform_usage(db: AsyncSession) -> dict[str, Any]:
        now = datetime.now(UTC)
        period_start = month_start(now)
        spent, turns, outstanding = await _usage_by_tenant(db, period_start, now)
        settled = int(
            await db.scalar(
                select(func.count())
                .select_from(AIUsageReservation)
                .where(
                    AIUsageReservation.period_start == period_start,
                    AIUsageReservation.status == STATUS_SETTLED,
                )
            )
            or 0
        )
        names = dict((await db.execute(select(Organization.id, Organization.name))).all())
        total = sum(spent.values())
        total_outstanding = sum(outstanding.values())
        ceiling = get_settings().platform_monthly_ai_budget_usd
        ranked = sorted(set(spent) | set(turns), key=lambda org: spent.get(org, 0.0), reverse=True)
        by_tenant = [
            {
                "organization_id": org_id,
                "name": names.get(org_id),
                "turns": turns.get(org_id, 0),
                "estimated_cost_usd": round(spent.get(org_id, 0.0), 4),
            }
            for org_id in ranked
        ]
        return {
            "period_start": period_start.isoformat(),
            "estimated_cost_usd": round(total, 4),
            "estimated_outstanding_usd": round(total_outstanding, 4),
            "platform_ceiling_usd": round(ceiling, 2),
            "ceiling_used_ratio": round((total + total_outstanding) / ceiling, 4)
            if ceiling > 0
            else None,
            "turns": sum(turns.values()),
            "settled_turns": settled,
            "by_tenant": by_tenant,
            "cost_basis": "estimated",
            "cost_basis_note": COST_BASIS_NOTE,
        }
