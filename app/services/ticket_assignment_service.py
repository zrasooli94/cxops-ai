from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.metrics import record_ticket_claimed
from app.core.rbac import AuthorizationContext, Capability
from app.models.service_queue import ServiceQueue
from app.models.ticket import Ticket
from app.repositories.organization_membership_repository import (
    OrganizationMembershipRepository,
)
from app.repositories.service_queue_repository import ServiceQueueRepository
from app.services.ticket_routing_service import TicketRoutingService

log = get_logger(__name__)


class TicketAssignmentService:
    """Manual queue/subject assignment and claim workflow."""

    @staticmethod
    def _require_ticket_write(authz: AuthorizationContext) -> None:
        authz.require(Capability.TICKET_WRITE)

    @staticmethod
    def _require_member_read(authz: AuthorizationContext) -> None:
        authz.require(Capability.MEMBER_READ)

    @classmethod
    async def assign_queue(
        cls,
        db: AsyncSession,
        *,
        ticket: Ticket,
        queue_id: int | None,
        authz: AuthorizationContext,
    ) -> Ticket:
        cls._require_ticket_write(authz)

        queue: ServiceQueue | None = None
        if queue_id is not None:
            queue = await ServiceQueueRepository.get_by_id_for_tenant(
                db,
                queue_id=queue_id,
                organization_id=authz.organization_id,
            )
            if queue is None:
                raise ValueError("Service queue not found")
            if not queue.active:
                raise ValueError("Service queue is inactive")

        await TicketRoutingService.apply_queue_to_ticket(
            db,
            ticket,
            queue,
            source="manual",
        )
        await db.commit()
        await db.refresh(ticket)
        return ticket

    @classmethod
    async def unassign_queue(
        cls,
        db: AsyncSession,
        *,
        ticket: Ticket,
        authz: AuthorizationContext,
    ) -> Ticket:
        cls._require_ticket_write(authz)

        await TicketRoutingService.apply_queue_to_ticket(
            db,
            ticket,
            None,
            source="manual",
        )
        await db.commit()
        await db.refresh(ticket)
        return ticket

    @classmethod
    async def assign_subject(
        cls,
        db: AsyncSession,
        *,
        ticket: Ticket,
        subject: str | None,
        authz: AuthorizationContext,
    ) -> Ticket:
        cls._require_ticket_write(authz)

        if subject is not None:
            # Self-assignment is allowed with ticket.write alone.
            # Assigning another subject requires member.read.
            if subject != authz.subject:
                cls._require_member_read(authz)

            membership = await OrganizationMembershipRepository.get_by_organization_and_subject(
                db,
                organization_id=authz.organization_id,
                subject=subject,
            )
            if membership is None:
                raise ValueError("Assignee is not a member of this organization")

        ticket.assigned_subject = subject
        await db.commit()
        await db.refresh(ticket)
        return ticket

    @classmethod
    async def unassign_subject(
        cls,
        db: AsyncSession,
        *,
        ticket: Ticket,
        authz: AuthorizationContext,
    ) -> Ticket:
        cls._require_ticket_write(authz)

        if ticket.assigned_subject is not None and ticket.assigned_subject != authz.subject:
            cls._require_member_read(authz)

        ticket.assigned_subject = None
        await db.commit()
        await db.refresh(ticket)
        return ticket

    @classmethod
    async def claim_ticket(
        cls,
        db: AsyncSession,
        *,
        ticket: Ticket,
        authz: AuthorizationContext,
    ) -> Ticket:
        cls._require_ticket_write(authz)

        # Serialize concurrent claims on the same ticket row. The initial
        # ticket may have been loaded without a lock, so re-fetch with
        # FOR UPDATE before mutating.
        locked_result = await db.execute(
            select(Ticket)
            .where(
                Ticket.id == ticket.id,
                Ticket.organization_id == authz.organization_id,
            )
            .with_for_update()
        )
        locked_ticket = locked_result.scalar_one_or_none()
        if locked_ticket is None:
            raise ValueError("Ticket not found")

        if locked_ticket.assigned_subject is not None:
            raise ValueError("Ticket is already claimed")

        locked_ticket.assigned_subject = authz.subject
        try:
            await db.commit()
            await db.refresh(locked_ticket)
        except IntegrityError:
            await db.rollback()
            log.warning(
                "ticket_claim_race_lost",
                ticket_id=locked_ticket.id,
                organization_id=authz.organization_id,
                subject=authz.subject,
            )
            raise ValueError("Ticket was claimed by another user")

        record_ticket_claimed(routing_source=locked_ticket.routing_source or "manual")
        return locked_ticket
