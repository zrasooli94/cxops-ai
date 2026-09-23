from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.conversation import Conversation
from app.models.organization_membership import OrganizationMembership
from app.models.ticket import Ticket
from app.repositories.service_operations_repository import (
    ServiceOperationsRepository,
)
from app.schemas.service_escalation import WorkloadMember

OPEN_TICKET_STATUSES = ("new", "open", "pending")

# Same due-soon window as the SLA observability milestones (DUE_SOON_MINUTES).
DUE_SOON_MINUTES = 30


class WorkloadService:
    """Per-member workload view for the operations supervisor."""

    @classmethod
    async def workload_for_tenant(
        cls,
        db: AsyncSession,
        *,
        organization_id: int,
    ) -> list[WorkloadMember]:
        now = datetime.now(UTC)
        due_soon_threshold = now + timedelta(minutes=DUE_SOON_MINUTES)

        members_result = await db.execute(
            select(OrganizationMembership.subject).where(
                OrganizationMembership.organization_id == organization_id,
            )
        )
        subjects = sorted({row[0] for row in members_result.all()})

        if not subjects:
            return []

        # Reuse the canonical latest-public-message ranking so needs_response
        # matches the observability queues exactly (one definition, not a copy).
        ranked = ServiceOperationsRepository._latest_public_message_ranked(
            organization_id,
        )
        conv_sq = aliased(Conversation)
        needs_response_expr = (
            (conv_sq.status == "open")
            & (ranked.c.direction == "inbound")
        ).label("needs_response")

        agg_result = await db.execute(
            select(
                Ticket.assigned_subject.label("subject"),
                func.count().label("open_assigned"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                Ticket.status.in_(OPEN_TICKET_STATUSES)
                                & Ticket.resolution_due_at.isnot(None)
                                & (Ticket.resolution_due_at < now),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("breached"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                Ticket.status.in_(OPEN_TICKET_STATUSES)
                                & (Ticket.priority == "urgent"),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("urgent"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                Ticket.status.in_(OPEN_TICKET_STATUSES)
                                & needs_response_expr,
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("needs_response"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                Ticket.status.in_(OPEN_TICKET_STATUSES)
                                & (
                                    (
                                        Ticket.first_response_due_at.isnot(None)
                                        & (Ticket.first_response_due_at <= due_soon_threshold)
                                    )
                                    | (
                                        Ticket.resolution_due_at.isnot(None)
                                        & (Ticket.resolution_due_at <= due_soon_threshold)
                                    )
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("due_soon"),
            )
            .outerjoin(
                conv_sq,
                (conv_sq.ticket_id == Ticket.id)
                & (conv_sq.organization_id == Ticket.organization_id),
            )
            .outerjoin(
                ranked,
                (ranked.c.conversation_id == conv_sq.id) & (ranked.c.rn == 1),
            )
            .where(
                Ticket.organization_id == organization_id,
                Ticket.assigned_subject.in_(subjects),
                Ticket.status.in_(OPEN_TICKET_STATUSES),
            )
            .group_by(Ticket.assigned_subject)
        )

        rows = {row.subject: row for row in agg_result.all()}

        output = []
        for subject in subjects:
            row = rows.get(subject)
            output.append(
                WorkloadMember(
                    subject=subject,
                    open_assigned=int(row.open_assigned) if row else 0,
                    breached=int(row.breached) if row else 0,
                    urgent=int(row.urgent) if row else 0,
                    needs_response=int(row.needs_response) if row else 0,
                    due_soon=int(row.due_soon) if row else 0,
                )
            )
        return output
