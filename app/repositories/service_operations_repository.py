from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.conversation import Conversation
from app.models.conversation_message import ConversationMessage
from app.models.service_queue import ServiceQueue
from app.models.ticket import Ticket

OPEN_TICKET_STATUSES = ("new", "open", "pending")
RESOLVED_TICKET_STATUSES = ("solved", "closed")


class ServiceOperationsRepository:
    """Tenant-scoped queries for the service-operations work queue."""

    @staticmethod
    def _latest_public_message_ranked(organization_id: int):
        return (
            select(
                ConversationMessage.conversation_id.label("conversation_id"),
                ConversationMessage.id.label("message_id"),
                ConversationMessage.direction.label("direction"),
                func.row_number()
                .over(
                    partition_by=ConversationMessage.conversation_id,
                    order_by=(
                        ConversationMessage.sent_at.desc().nulls_last(),
                        ConversationMessage.id.desc(),
                    ),
                )
                .label("rn"),
            )
            .where(
                ConversationMessage.organization_id == organization_id,
                ConversationMessage.visibility == "public",
                (
                    ConversationMessage.delivery_status.is_(None)
                    | (ConversationMessage.delivery_status == "sent")
                ),
            )
            .subquery()
        )

    @classmethod
    async def list_operations_for_tenant(
        cls,
        db: AsyncSession,
        *,
        organization_id: int,
        queue_id: int | None = None,
        status: str | None = None,
        priority: str | None = None,
        sla_state: str | None = None,
        needs_response: bool | None = None,
        unassigned: bool | None = None,
        assignee_subject: str | None = None,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[tuple[Ticket, ServiceQueue | None, Conversation | None, bool]]:
        """Return tickets plus joined queue, conversation, and needs_response flag.

        Sorting is deterministic and operationally urgent:
        1. response SLA breached / resolution SLA breached
        2. response SLA due soon / resolution SLA due soon
        3. priority (urgent > high > normal > low)
        4. created_at ascending (older first)
        5. id ascending
        """
        ranked = cls._latest_public_message_ranked(organization_id)

        queue_sq = aliased(ServiceQueue)
        conv_sq = aliased(Conversation)

        # Build needs_response as a SQL expression: open conversation whose
        # latest public message is inbound.
        needs_response_expr = (
            (conv_sq.status == "open")
            & (ranked.c.direction == "inbound")
        ).label("needs_response")

        stmt = (
            select(Ticket, queue_sq, conv_sq, needs_response_expr)
            .outerjoin(
                queue_sq,
                (queue_sq.id == Ticket.service_queue_id)
                & (queue_sq.organization_id == Ticket.organization_id),
            )
            .outerjoin(
                conv_sq,
                (conv_sq.ticket_id == Ticket.id)
                & (conv_sq.organization_id == Ticket.organization_id),
            )
            .outerjoin(
                ranked,
                (ranked.c.conversation_id == conv_sq.id)
                & (ranked.c.rn == 1),
            )
            .where(
                Ticket.organization_id == organization_id,
            )
        )

        if queue_id is not None:
            stmt = stmt.where(Ticket.service_queue_id == queue_id)

        if status is not None:
            stmt = stmt.where(Ticket.status == status)

        if priority is not None:
            stmt = stmt.where(Ticket.priority == priority)

        if unassigned is True:
            stmt = stmt.where(Ticket.assigned_subject.is_(None))

        if assignee_subject is not None:
            stmt = stmt.where(Ticket.assigned_subject == assignee_subject)

        if needs_response is not None:
            if needs_response:
                stmt = stmt.where(needs_response_expr.is_(True))
            else:
                stmt = stmt.where(needs_response_expr.is_(False))

        if search is not None and search.strip():
            escaped = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            stmt = stmt.where(Ticket.subject.ilike(f"%{escaped}%", escape="\\"))

        # Operational sorting bands.
        now = datetime.now(UTC)
        due_soon_threshold = now + timedelta(minutes=30)
        priority_order = case(
            (Ticket.priority == "urgent", 1),
            (Ticket.priority == "high", 2),
            (Ticket.priority == "normal", 3),
            else_=4,
        )

        # A ticket is "SLA urgent" if either deadline is breached or due soon.
        # We sort breached before due-soon before everything else.
        sla_urgency = case(
            (
                (Ticket.first_response_due_at.isnot(None) & (Ticket.first_response_due_at < now))
                | (Ticket.resolution_due_at.isnot(None) & (Ticket.resolution_due_at < now)),
                1,
            ),
            (
                (
                    Ticket.first_response_due_at.isnot(None)
                    & (Ticket.first_response_due_at >= now)
                    & (Ticket.first_response_due_at <= due_soon_threshold)
                )
                | (
                    Ticket.resolution_due_at.isnot(None)
                    & (Ticket.resolution_due_at >= now)
                    & (Ticket.resolution_due_at <= due_soon_threshold)
                ),
                2,
            ),
            else_=3,
        )

        stmt = stmt.order_by(
            sla_urgency.asc(),
            priority_order.asc(),
            Ticket.created_at.asc(),
            Ticket.id.asc(),
        )

        stmt = stmt.limit(limit).offset(offset)

        result = await db.execute(stmt)
        return cast(list[tuple[Ticket, ServiceQueue | None, Conversation | None, bool]], list(result.all()))

    @classmethod
    async def summary_for_tenant(
        cls,
        db: AsyncSession,
        *,
        organization_id: int,
        now: datetime,
    ) -> dict:
        """Tenant-scoped operational snapshot counts."""
        due_soon_threshold = now + timedelta(minutes=30)
        ranked = cls._latest_public_message_ranked(organization_id)
        conv_sq = aliased(Conversation)

        needs_response_expr = (
            (conv_sq.status == "open")
            & (ranked.c.direction == "inbound")
        ).label("needs_response")

        base = (
            select(Ticket, needs_response_expr)
            .outerjoin(
                conv_sq,
                (conv_sq.ticket_id == Ticket.id)
                & (conv_sq.organization_id == Ticket.organization_id),
            )
            .outerjoin(
                ranked,
                (ranked.c.conversation_id == conv_sq.id)
                & (ranked.c.rn == 1),
            )
            .where(
                Ticket.organization_id == organization_id,
                Ticket.status.in_(OPEN_TICKET_STATUSES),
            )
            .subquery()
        )

        t = base.c

        result = await db.execute(
            select(
                func.count().label("open"),
                func.coalesce(
                    func.sum(case((t.assigned_subject.is_(None), 1), else_=0)),
                    0,
                ).label("unassigned"),
                func.coalesce(
                    func.sum(case((t.needs_response.is_(True), 1), else_=0)),
                    0,
                ).label("needs_response"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                t.first_response_due_at.isnot(None)
                                & (t.first_response_due_at < now),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("response_breaches"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                t.resolution_due_at.isnot(None)
                                & (t.resolution_due_at < now),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("resolution_breaches"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                (
                                    t.first_response_due_at.isnot(None)
                                    & (t.first_response_due_at >= now)
                                    & (t.first_response_due_at <= due_soon_threshold)
                                )
                                | (
                                    t.resolution_due_at.isnot(None)
                                    & (t.resolution_due_at >= now)
                                    & (t.resolution_due_at <= due_soon_threshold)
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("due_soon"),
                func.coalesce(
                    func.sum(case((t.priority == "urgent", 1), else_=0)),
                    0,
                ).label("urgent"),
                func.coalesce(
                    func.sum(case((t.priority == "high", 1), else_=0)),
                    0,
                ).label("high"),
            ).select_from(base)
        )

        row = result.one()

        return {
            "open": int(row.open),
            "unassigned": int(row.unassigned),
            "needs_response": int(row.needs_response),
            "response_breaches": int(row.response_breaches),
            "resolution_breaches": int(row.resolution_breaches),
            "due_soon": int(row.due_soon),
            "urgent": int(row.urgent),
            "high": int(row.high),
        }
