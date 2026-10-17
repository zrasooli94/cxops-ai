"""Controlled Phase 1Y synthetic-ticket workflow for isolated staging ONLY.

Production never executes this route. Staging enables it only after separate
security review and provisioning an enrolled Alpha test *agent*. Existing
synthetic viewers remain read-only. No real Zendesk, LLM or notification
service is called and no general ticket-update endpoint is opened.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentPrincipal, CurrentTenant, RequireCapability
from app.core.config import settings
from app.core.database import get_db
from app.core.logging import get_logger
from app.core.rbac import AuthorizationContext, Capability, OrganizationRole
from app.models.customer import Customer
from app.models.organization import Organization
from app.models.organization_membership import OrganizationMembership
from app.models.ticket import Ticket

router = APIRouter(prefix="/staging", tags=["Isolated Staging"], include_in_schema=False)
log = get_logger(__name__)

DatabaseSession = Annotated[AsyncSession, Depends(get_db)]
WriteAuth = Annotated[
    AuthorizationContext,
    Depends(RequireCapability(Capability.TICKET_WRITE)),
]


class StagingTicketAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ticket_id: int = Field(ge=1, le=2_147_483_647)
    action: Literal["claim", "release", "resolve"]
    # JSON has no native datetime type, so this one field must parse an ISO-8601
    # string even though the rest of the command model remains strict.
    expected_version: datetime = Field(strict=False)


class StagingTicketActionResult(BaseModel):
    id: int
    external_id: str
    status: str
    assigned_to_you: bool
    resolved_at: datetime | None
    version: datetime


def _staging_actions_enabled() -> bool:
    """A private, server-side switch; false on all existing deployments."""
    return (
        settings.environment == "staging"
        and settings.auth_mode == "neon_staging"
        and os.environ.get("CXOPS_STAGING_TICKET_ACTIONS_ENABLED") == "true"
    )


def _check_actor(principal: CurrentPrincipal, tenant: CurrentTenant, authz: WriteAuth) -> None:
    if settings.environment != "staging" or settings.auth_mode != "neon_staging":
        raise HTTPException(status_code=404, detail="Not found")
    if not _staging_actions_enabled():
        raise HTTPException(status_code=503, detail="Staging ticket actions are disabled")
    # Exact subject plus durable capability and organization membership. This
    # subject is provisioned only in the isolated Neon Auth directory; no
    # production Nhost JWT or synthetic VIEWER can acquire this privilege.
    allowed_subject = os.environ.get("CXOPS_STAGING_TICKET_AGENT_SUBJECT", "")
    if (
        not allowed_subject
        or principal.auth_method != "neon_staging_ed25519"
        or principal.subject != allowed_subject
        or authz.subject != principal.subject
        or authz.organization_id != tenant.organization_id
        or authz.role != OrganizationRole.AGENT
    ):
        raise HTTPException(status_code=403, detail="Staging agent access required")


@router.post("/ticket-action", response_model=StagingTicketActionResult)
async def synthetic_ticket_action(
    request: StagingTicketAction,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: WriteAuth,
    db: DatabaseSession,
):
    """Atomic, tenant-scoped claim/release/resolve on fictional Alpha tickets.

    This deliberately does not call the general production TicketService:
    that path may trigger SLA/conversation synchronization. Only the minimal
    existing Ticket columns change in the isolated test database.
    """
    _check_actor(principal, tenant, authz)

    # Deterministic authorization-fence lock order: organization, membership,
    # then ticket. Revocation/demotion/suspension cannot commit between this
    # fence and the synthetic mutation.
    org = (
        await db.execute(
            select(Organization)
            .where(Organization.id == tenant.organization_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if org is None or org.name != "Synthetic Alpha Workshop":
        raise HTTPException(status_code=404, detail="Synthetic ticket not found")
    membership = (
        await db.execute(
            select(OrganizationMembership)
            .where(
                OrganizationMembership.organization_id == tenant.organization_id,
                OrganizationMembership.subject == principal.subject,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    try:
        current_role = OrganizationRole(membership.role) if membership is not None else None
    except (ValueError, AttributeError):
        current_role = None
    current_authz = AuthorizationContext(
        organization_id=tenant.organization_id,
        subject=principal.subject,
        role=current_role or OrganizationRole.VIEWER,
        organization_status=org.lifecycle_status,
        knowledge_setup_enabled=settings.onboarding_knowledge_setup_enabled,
    )
    if (
        membership is None
        or current_role != OrganizationRole.AGENT
        or not current_authz.has(Capability.TICKET_WRITE)
    ):
        raise HTTPException(status_code=403, detail="Staging agent access required")

    result = await db.execute(
        select(Ticket)
        .where(
            Ticket.id == request.ticket_id,
            Ticket.organization_id == tenant.organization_id,
        )
        .with_for_update()
    )
    ticket = result.scalar_one_or_none()
    if (
        ticket is None
        or ticket.source != "synthetic_staging"
        or not (ticket.external_id or "").startswith("PHASE1X-ALPHA-")
        or not ticket.subject.startswith("Synthetic: ")
    ):
        raise HTTPException(status_code=404, detail="Synthetic ticket not found")
    if ticket.updated_at != request.expected_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ticket action conflicts with its current version",
        )

    # A valid synthetic ticket may still have been linked to a mistakenly
    # imported customer. Only explicitly fictional Alpha customers are safe
    # to include in this one-record staging action. Refuse a non-synthetic
    # linkage instead of silently mutating it.
    if ticket.customer_id is not None:
        customer = (
            await db.execute(
                select(Customer).where(
                    Customer.id == ticket.customer_id,
                    Customer.organization_id == tenant.organization_id,
                )
            )
        ).scalar_one_or_none()
        if (
            customer is None
            or not (customer.external_id or "").startswith("PHASE1X-ALPHA-C")
            or not customer.name.startswith("Synthetic ")
            or not customer.email.endswith("@example.test")
        ):
            raise HTTPException(status_code=404, detail="Synthetic ticket not found")

    active = ticket.status in ("new", "open", "pending")
    if request.action == "claim":
        allowed = active and ticket.assigned_subject is None
        if allowed:
            ticket.assigned_subject = principal.subject
    elif request.action == "release":
        allowed = active and ticket.assigned_subject == principal.subject
        if allowed:
            ticket.assigned_subject = None
    else:
        allowed = active and ticket.assigned_subject == principal.subject
        if allowed:
            ticket.status = "solved"
            ticket.resolved_at = datetime.now(UTC)

    if not allowed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ticket action conflicts with its current state")

    # There is NO background job, webhook, email, external provider or model
    # call. Commit the already row-locked single ticket as one operation.
    await db.commit()
    await db.refresh(ticket)
    # Structured metadata only. Do not log a bearer, email, name or body.
    log.info(
        "staging_synthetic_ticket_action_committed",
        organization_id=tenant.organization_id,
        ticket_id=ticket.id,
        action=request.action,
    )
    return StagingTicketActionResult(
        id=ticket.id,
        external_id=ticket.external_id or "",
        status=ticket.status,
        assigned_to_you=ticket.assigned_subject == principal.subject,
        resolved_at=ticket.resolved_at,
        version=ticket.updated_at,
    )
