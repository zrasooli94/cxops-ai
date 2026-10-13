"""Business-owner workspace overview (Phase 1Q).

One read-only, tenant-scoped summary for the customer portal. Each section is
computed only when the caller's *effective* capabilities allow it; otherwise
the section is ``{"available": false, "reason": ...}`` - the portal shows that
the information exists but is not available to this role, instead of
silently showing zeros. Every query is bound to the resolved organization id;
no section accepts an organization id from the client.

Sections carry a ``basis`` so the UI can label figures honestly:
``current`` (state right now), ``recent`` (latest activity), ``month_to_date``
or ``last_24h``. No message text is returned here - conversation content stays
behind the existing inbox endpoints.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import AuthorizationContext, Capability
from app.models.commercial import INVITATION_STATUS_PENDING, OrganizationInvitation
from app.models.knowledge_document import KnowledgeDocument
from app.models.organization import Organization
from app.models.organization_membership import OrganizationMembership
from app.models.public_chat import PublicChatSession
from app.services.tenant_ai_allowance_service import TenantUsageService
from app.tenant_onboarding.planner import resolve_canonical_public_chat_config

OPEN_HANDOFF_STATUSES = ("human_requested", "human_assigned")

ACTION_CAPABILITIES: dict[str, Capability] = {
    "configure_chatbot": Capability.INTEGRATION_MANAGE,
    "manage_knowledge": Capability.KNOWLEDGE_MANAGE,
    "reply_to_conversations": Capability.TICKET_WRITE,
    "manage_team": Capability.MEMBER_MANAGE,
    "edit_business_profile": Capability.ORGANIZATION_MANAGE,
    "view_usage": Capability.OBSERVABILITY_READ,
}


def _unavailable(reason: str) -> dict[str, Any]:
    return {"available": False, "reason": reason}


ROLE_REASON = "Not available for your role."


class WorkspaceService:
    @staticmethod
    async def overview(db: AsyncSession, *, authz: AuthorizationContext) -> dict[str, Any]:
        organization_id = authz.organization_id
        organization = await db.get(Organization, organization_id)
        if organization is None:  # pragma: no cover - FK guarantees it
            raise LookupError("organization not found")
        caps = authz.capabilities
        now = datetime.now(UTC)

        config, _rows = await resolve_canonical_public_chat_config(db, organization_id)

        chatbot: dict[str, Any]
        if caps & {Capability.INTEGRATION_READ, Capability.ORGANIZATION_READ}:
            last_session_at = None
            if config is not None:
                last_session_at = await db.scalar(
                    select(func.max(PublicChatSession.created_at)).where(
                        PublicChatSession.organization_id == organization_id,
                        PublicChatSession.configuration_id == config.id,
                    )
                )
            live = (
                config is not None
                and config.enabled
                and organization.lifecycle_status == "active"
                and bool(config.allowed_origins)
            )
            chatbot = {
                "available": True,
                "basis": "current",
                "configured": config is not None,
                "display_name": config.display_name if config else None,
                "enabled": bool(config and config.enabled),
                "live": live,
                "allowed_origins": list(config.allowed_origins or []) if config else [],
                "has_widget_key": bool(config and config.public_widget_key_hash),
                "installation": {
                    "basis": "recent",
                    # A session can only be created from an allowed origin, so
                    # the newest session is evidence the embed is installed.
                    "detected": last_session_at is not None,
                    "last_session_at": last_session_at.isoformat() if last_session_at else None,
                },
            }
        else:
            chatbot = _unavailable(ROLE_REASON)

        knowledge: dict[str, Any]
        if Capability.KNOWLEDGE_READ in authz.role_capabilities:
            documents = int(
                await db.scalar(
                    select(func.count())
                    .select_from(KnowledgeDocument)
                    .where(KnowledgeDocument.organization_id == organization_id)
                )
                or 0
            )
            latest = await db.scalar(
                select(func.max(KnowledgeDocument.created_at)).where(
                    KnowledgeDocument.organization_id == organization_id
                )
            )
            knowledge = {
                "available": True,
                "basis": "current",
                "documents": documents,
                "last_added_at": latest.isoformat() if latest else None,
                "ready": documents > 0,
            }
        else:
            knowledge = _unavailable(ROLE_REASON)

        conversations: dict[str, Any]
        handoff: dict[str, Any]
        if Capability.TICKET_READ in caps:
            recent = (
                await db.execute(
                    select(PublicChatSession)
                    .where(PublicChatSession.organization_id == organization_id)
                    .order_by(PublicChatSession.created_at.desc(), PublicChatSession.id.desc())
                    .limit(8)
                )
            ).scalars()
            since = now - timedelta(days=7)
            week = int(
                await db.scalar(
                    select(func.count())
                    .select_from(PublicChatSession)
                    .where(
                        PublicChatSession.organization_id == organization_id,
                        PublicChatSession.created_at >= since,
                    )
                )
                or 0
            )
            conversations = {
                "available": True,
                "basis": "recent",
                "last_7_days": week,
                "recent": [
                    {
                        "session_id": row.id,
                        "status": row.status,
                        "started_at": row.created_at.isoformat() if row.created_at else None,
                        "assigned": row.assigned_to_subject is not None,
                    }
                    for row in recent
                ],
            }
            queue = dict(
                (
                    await db.execute(
                        select(PublicChatSession.status, func.count())
                        .where(
                            PublicChatSession.organization_id == organization_id,
                            PublicChatSession.status.in_(OPEN_HANDOFF_STATUSES),
                            PublicChatSession.expires_at > now,
                        )
                        .group_by(PublicChatSession.status)
                    )
                ).all()
            )
            handoff = {
                "available": True,
                "basis": "current",
                "waiting": int(queue.get("human_requested", 0)),
                "assigned": int(queue.get("human_assigned", 0)),
            }
        else:
            conversations = _unavailable(ROLE_REASON)
            handoff = _unavailable(ROLE_REASON)

        usage: dict[str, Any]
        if Capability.OBSERVABILITY_READ in caps:
            report = await TenantUsageService.report(db, organization_id=organization_id)
            allowance = report["allowance"]
            remaining_turns = max(0, allowance["turn_limit"] - allowance["turns_used"])
            remaining_budget = max(
                0.0,
                allowance["budget_usd"]
                - allowance["estimated_cost_used_usd"]
                - allowance["estimated_outstanding_usd"],
            )
            usage = {
                "available": True,
                "basis": "month_to_date",
                "period_start": allowance["period_start"],
                "turns_used": allowance["turns_used"],
                "turn_limit": allowance["turn_limit"],
                "remaining_turns": remaining_turns,
                "estimated_cost_usd": allowance["estimated_cost_used_usd"],
                "budget_usd": allowance["budget_usd"],
                "remaining_budget_usd": round(remaining_budget, 4),
                "limit_reached": not allowance["allowed"],
                "limit_reason": None if allowance["allowed"] else allowance["reason"],
                "cost_basis": "estimated",
                "chat_turns_24h": report["chat_turns_24h"],
            }
        else:
            usage = _unavailable(ROLE_REASON)

        team: dict[str, Any]
        if Capability.MEMBER_READ in caps:
            members = int(
                await db.scalar(
                    select(func.count())
                    .select_from(OrganizationMembership)
                    .where(OrganizationMembership.organization_id == organization_id)
                )
                or 0
            )
            pending = int(
                await db.scalar(
                    select(func.count())
                    .select_from(OrganizationInvitation)
                    .where(
                        OrganizationInvitation.organization_id == organization_id,
                        OrganizationInvitation.status == INVITATION_STATUS_PENDING,
                        OrganizationInvitation.expires_at > now,
                    )
                )
                or 0
            )
            team = {
                "available": True,
                "basis": "current",
                "members": members,
                "pending_invitations": pending,
            }
        else:
            team = _unavailable(ROLE_REASON)

        return {
            "organization": {
                "id": organization.id,
                "name": organization.name,
                "industry": organization.industry,
                "website_url": organization.website_url,
                "lifecycle_status": organization.lifecycle_status,
                "plan_code": organization.plan_code,
            },
            "your_role": authz.role.value,
            "chatbot": chatbot,
            "knowledge": knowledge,
            "conversations": conversations,
            "handoff": handoff,
            "usage": usage,
            "team": team,
            "actions": sorted(
                name for name, capability in ACTION_CAPABILITIES.items() if capability in caps
            ),
        }
