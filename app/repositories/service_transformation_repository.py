from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.agent_run import AgentRun
from app.models.conversation import Conversation
from app.models.conversation_message import ConversationMessage
from app.models.service_escalation import ServiceEscalation
from app.models.service_queue import ServiceQueue
from app.models.ticket import Ticket
from app.repositories.service_operations_repository import (
    ServiceOperationsRepository,
)

OPEN_TICKET_STATUSES = ("new", "open", "pending")


class ServiceTransformationRepository:
    """Tenant-scoped aggregation queries for the transformation analytics.

    Every query binds ``organization_id`` in SQL; NULL-org legacy rows can
    never enter an aggregate. All aggregations run in the database — no
    per-queue loops, no global aggregation then Python filtering.
    """

    @staticmethod
    def _latest_conversation_per_ticket(organization_id: int):
        return (
            select(
                Conversation.ticket_id.label("ticket_id"),
                Conversation.id.label("conversation_id"),
                Conversation.status.label("conversation_status"),
                func.row_number()
                .over(
                    partition_by=Conversation.ticket_id,
                    order_by=(
                        Conversation.latest_message_at.desc().nulls_last(),
                        Conversation.id.desc(),
                    ),
                )
                .label("rn"),
            )
            .where(Conversation.organization_id == organization_id)
            .subquery()
        )

    @classmethod
    async def current_snapshot_for_tenant(
        cls,
        db: AsyncSession,
        *,
        organization_id: int,
    ) -> dict:
        """Current open backlog + open tickets whose latest public message was
        inbound (they still need a response). Mirrors the ServiceOperations
        needs_response definition exactly.
        """
        ranked = ServiceOperationsRepository._latest_public_message_ranked(
            organization_id
        )
        conv_r = cls._latest_conversation_per_ticket(organization_id)

        base = (
            select(
                (
                    (conv_r.c.conversation_status == "open")
                    & (ranked.c.direction == "inbound")
                ).label("needs_response")
            )
            .select_from(Ticket)
            .outerjoin(conv_r, (conv_r.c.ticket_id == Ticket.id) & (conv_r.c.rn == 1))
            .outerjoin(
                ranked,
                (ranked.c.conversation_id == conv_r.c.conversation_id)
                & (ranked.c.rn == 1),
            )
            .where(
                Ticket.organization_id == organization_id,
                Ticket.status.in_(OPEN_TICKET_STATUSES),
            )
            .subquery()
        )

        result = await db.execute(
            select(
                func.count().label("open"),
                func.coalesce(
                    func.sum(case((base.c.needs_response.is_(True), 1), else_=0)),
                    0,
                ).label("needs_response"),
            ).select_from(base)
        )
        row = result.one()
        return {
            "open": int(row.open),
            "needs_response": int(row.needs_response),
        }

    @staticmethod
    async def tickets_created_in_window(
        db: AsyncSession,
        *,
        organization_id: int,
        start: datetime,
        end: datetime,
        queue_id: int | None = None,
    ) -> int:
        where = [
            Ticket.organization_id == organization_id,
            Ticket.created_at >= start,
            Ticket.created_at <= end,
        ]
        if queue_id is not None:
            where.append(Ticket.service_queue_id == queue_id)
        result = await db.execute(
            select(func.count()).select_from(Ticket).where(*where)
        )
        return int(result.scalar_one())

    @staticmethod
    async def first_response_analytics_for_window(
        db: AsyncSession,
        *,
        organization_id: int,
        start: datetime,
        end: datetime,
        queue_id: int | None = None,
    ) -> dict:
        where = [
            Ticket.organization_id == organization_id,
            Ticket.first_response_at.isnot(None),
            Ticket.first_response_due_at.isnot(None),
            Ticket.first_response_at >= start,
            Ticket.first_response_at <= end,
        ]
        if queue_id is not None:
            where.append(Ticket.service_queue_id == queue_id)
        result = await db.execute(
            select(
                func.count().label("count"),
                func.avg(
                    func.extract(
                        "epoch",
                        Ticket.first_response_at - Ticket.created_at,
                    )
                    / 60
                ).label("avg_minutes"),
                func.percentile_cont(0.5).within_group(
                    func.extract(
                        "epoch",
                        Ticket.first_response_at - Ticket.created_at,
                    )
                    / 60
                ).label("median_minutes"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                Ticket.first_response_at > Ticket.first_response_due_at,
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("breached"),
            ).where(*where)
        )
        row = result.one()
        count = int(row._mapping["count"])
        return {
            "count": count,
            "avg_minutes": float(row.avg_minutes) if count > 0 else None,
            "median_minutes": float(row.median_minutes) if count > 0 else None,
            "breached": int(row.breached),
        }

    @staticmethod
    async def resolution_analytics_for_window(
        db: AsyncSession,
        *,
        organization_id: int,
        start: datetime,
        end: datetime,
        queue_id: int | None = None,
    ) -> dict:
        where = [
            Ticket.organization_id == organization_id,
            Ticket.resolved_at.isnot(None),
            Ticket.resolution_due_at.isnot(None),
            Ticket.resolved_at >= start,
            Ticket.resolved_at <= end,
        ]
        if queue_id is not None:
            where.append(Ticket.service_queue_id == queue_id)
        result = await db.execute(
            select(
                func.count().label("count"),
                func.avg(
                    func.extract(
                        "epoch",
                        Ticket.resolved_at - Ticket.created_at,
                    )
                    / 60
                ).label("avg_minutes"),
                func.percentile_cont(0.5).within_group(
                    func.extract(
                        "epoch",
                        Ticket.resolved_at - Ticket.created_at,
                    )
                    / 60
                ).label("median_minutes"),
                func.coalesce(
                    func.sum(
                        case(
                            (Ticket.resolved_at > Ticket.resolution_due_at, 1),
                            else_=0,
                        )
                    ),
                    0,
                ).label("breached"),
            ).where(*where)
        )
        row = result.one()
        count = int(row._mapping["count"])
        return {
            "count": count,
            "avg_minutes": float(row.avg_minutes) if count > 0 else None,
            "median_minutes": float(row.median_minutes) if count > 0 else None,
            "breached": int(row.breached),
        }

    @staticmethod
    async def reopened_in_window(
        db: AsyncSession,
        *,
        organization_id: int,
        start: datetime,
        end: datetime,
        queue_id: int | None = None,
    ) -> dict:
        """Reopen events are the persisted, evidence-backed reopen trail: a
        previous escalation resolved with reason ``ticket_reopened`` inside the
        window. No inference from ``status=resolved``.
        """
        stmt = select(
            func.count(func.distinct(ServiceEscalation.ticket_id)).label(
                "distinct_tickets"
            ),
            func.count().label("events"),
        ).where(
            ServiceEscalation.organization_id == organization_id,
            ServiceEscalation.resolution_reason == "ticket_reopened",
            ServiceEscalation.resolved_at.isnot(None),
            ServiceEscalation.resolved_at >= start,
            ServiceEscalation.resolved_at <= end,
        )
        if queue_id is not None:
            stmt = stmt.join(
                Ticket,
                (Ticket.id == ServiceEscalation.ticket_id)
                & (Ticket.organization_id == organization_id),
            ).where(Ticket.service_queue_id == queue_id)
        result = await db.execute(stmt)
        row = result.one()
        return {
            "distinct_tickets": int(row.distinct_tickets),
            "events": int(row.events),
        }

    @staticmethod
    async def agent_run_counts_for_window(
        db: AsyncSession,
        *,
        organization_id: int,
        start: datetime,
        end: datetime,
        queue_id: int | None = None,
    ) -> dict:
        stmt = select(
            func.count(AgentRun.id).label("runs"),
            func.count(func.distinct(AgentRun.ticket_id)).label(
                "distinct_tickets"
            ),
            func.coalesce(
                func.sum(
                    case(
                        (
                            AgentRun.requires_human_approval.is_(True)
                            & AgentRun.authorization_source.is_distinct_from(
                                "policy_auto"
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("approval_required"),
            func.coalesce(
                func.sum(
                    case(
                        (AgentRun.authorization_source == "policy_auto", 1),
                        else_=0,
                    )
                ),
                0,
            ).label("autonomous"),
            func.coalesce(
                func.sum(
                    case(
                        (AgentRun.authorization_source == "human_approval", 1),
                        else_=0,
                    )
                ),
                0,
            ).label("human_approved"),
            func.coalesce(
                func.sum(
                    case((AgentRun.status == "rejected", 1), else_=0)
                ),
                0,
            ).label("human_rejected"),
            func.coalesce(
                func.sum(case((AgentRun.status == "executed", 1), else_=0)),
                0,
            ).label("executed"),
            func.coalesce(
                func.sum(
                    case((AgentRun.status == "execution_failed", 1), else_=0)
                ),
                0,
            ).label("execution_failed"),
            func.coalesce(
                func.sum(
                    case((AgentRun.action == "no_action", 1), else_=0)
                ),
                0,
            ).label("no_action"),
        ).where(
            AgentRun.organization_id == organization_id,
            AgentRun.created_at >= start,
            AgentRun.created_at <= end,
        )
        if queue_id is not None:
            stmt = stmt.join(
                Ticket,
                (Ticket.id == AgentRun.ticket_id)
                & (Ticket.organization_id == organization_id),
            ).where(Ticket.service_queue_id == queue_id)
        result = await db.execute(stmt)
        row = result.one()
        return {
            "runs": int(row.runs),
            "distinct_tickets": int(row.distinct_tickets),
            "approval_required": int(row.approval_required),
            "autonomous": int(row.autonomous),
            "human_approved": int(row.human_approved),
            "human_rejected": int(row.human_rejected),
            "executed": int(row.executed),
            "execution_failed": int(row.execution_failed),
            "no_action": int(row.no_action),
        }

    @staticmethod
    async def specialist_workflow_paths_for_window(
        db: AsyncSession,
        *,
        organization_id: int,
        start: datetime,
        end: datetime,
        queue_id: int | None = None,
    ) -> list[list[str]]:
        stmt = select(AgentRun.workflow_path).where(
            AgentRun.organization_id == organization_id,
            AgentRun.created_at >= start,
            AgentRun.created_at <= end,
        )
        if queue_id is not None:
            stmt = stmt.join(
                Ticket,
                (Ticket.id == AgentRun.ticket_id)
                & (Ticket.organization_id == organization_id),
            ).where(Ticket.service_queue_id == queue_id)
        result = await db.execute(stmt)
        return [list(path) for path in result.scalars().all()]

    @staticmethod
    async def human_workload_counts_for_window(
        db: AsyncSession,
        *,
        organization_id: int,
        start: datetime,
        end: datetime,
    ) -> dict:
        """Human-sent outbound messages are those carrying a requester subject
        (set by the authenticated human reply path); AI-executed replies are
        agent-authored mirrors (dedupe_key prefix ``agent_run:``). Sender is
        never inferred from message content.
        """
        result = await db.execute(
            select(
                func.coalesce(
                    func.sum(
                        case(
                            (
                                ConversationMessage.requested_by_subject.is_not(
                                    None
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("human_sent"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                ConversationMessage.dedupe_key.like("agent_run:%"),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("ai_executed"),
            ).where(
                ConversationMessage.organization_id == organization_id,
                ConversationMessage.direction == "outbound",
                ConversationMessage.sent_at.isnot(None),
                ConversationMessage.sent_at >= start,
                ConversationMessage.sent_at <= end,
            )
        )
        row = result.one()
        return {
            "human_sent": int(row.human_sent),
            "ai_executed": int(row.ai_executed),
        }

    @classmethod
    async def queue_breakdown_for_tenant(
        cls,
        db: AsyncSession,
        *,
        organization_id: int,
    ) -> list[dict]:
        """Per-queue snapshot aggregates for queued tickets only. Tickets with a
        NULL queue are excluded (no key/name to attribute them to); they remain
        visible in the org-level current snapshot.
        """
        ranked = ServiceOperationsRepository._latest_public_message_ranked(
            organization_id
        )
        conv_r = cls._latest_conversation_per_ticket(organization_id)
        queue = aliased(ServiceQueue)

        base = (
            select(
                Ticket.service_queue_id.label("queue_id"),
                (
                    (conv_r.c.conversation_status == "open")
                    & (ranked.c.direction == "inbound")
                ).label("needs_response"),
                (Ticket.priority.in_(("urgent", "high"))).label("urgent_high"),
                (Ticket.assigned_subject.is_not(None)).label("assigned"),
            )
            .select_from(Ticket)
            .outerjoin(conv_r, (conv_r.c.ticket_id == Ticket.id) & (conv_r.c.rn == 1))
            .outerjoin(
                ranked,
                (ranked.c.conversation_id == conv_r.c.conversation_id)
                & (ranked.c.rn == 1),
            )
            .where(
                Ticket.organization_id == organization_id,
                Ticket.status.in_(OPEN_TICKET_STATUSES),
                Ticket.service_queue_id.is_not(None),
            )
            .subquery()
        )

        result = await db.execute(
            select(
                base.c.queue_id.label("queue_id"),
                queue.key.label("queue_key"),
                queue.name.label("queue_name"),
                func.count().label("open"),
                func.coalesce(
                    func.sum(case((base.c.needs_response.is_(True), 1), else_=0)),
                    0,
                ).label("needs_response"),
                func.coalesce(
                    func.sum(case((base.c.urgent_high.is_(True), 1), else_=0)),
                    0,
                ).label("priority_urgent_high"),
                func.coalesce(
                    func.sum(case((base.c.assigned.is_(True), 1), else_=0)),
                    0,
                ).label("assigned"),
            )
            .outerjoin(
                queue,
                (queue.id == base.c.queue_id)
                & (queue.organization_id == organization_id),
            )
            .group_by(base.c.queue_id, queue.key, queue.name)
            .order_by(queue.key.asc().nulls_last(), base.c.queue_id.asc())
        )

        return [
            {
                "queue_id": row.queue_id,
                "queue_key": row.queue_key,
                "queue_name": row.queue_name,
                "open_tickets": int(row.open),
                "needs_response": int(row.needs_response),
                "priority_urgent_high": int(row.priority_urgent_high),
                "assigned_tickets": int(row.assigned),
            }
            for row in result.all()
        ]

    @staticmethod
    async def escalation_current_by_queue(
        db: AsyncSession,
        *,
        organization_id: int,
    ) -> dict[int, dict]:
        """Currently unresolved escalation rows per queue, bucketed by stage."""
        result = await db.execute(
            select(
                Ticket.service_queue_id.label("queue_id"),
                func.coalesce(
                    func.sum(
                        case((ServiceEscalation.stage == "due_soon", 1), else_=0)
                    ),
                    0,
                ).label("due_soon"),
                func.coalesce(
                    func.sum(
                        case((ServiceEscalation.stage == "breached", 1), else_=0)
                    ),
                    0,
                ).label("breached"),
            )
            .join(Ticket, Ticket.id == ServiceEscalation.ticket_id)
            .where(
                ServiceEscalation.organization_id == organization_id,
                ServiceEscalation.resolved_at.is_(None),
                Ticket.service_queue_id.is_not(None),
            )
            .group_by(Ticket.service_queue_id)
        )
        return {
            row.queue_id: {
                "due_soon": int(row.due_soon),
                "breached": int(row.breached),
            }
            for row in result.all()
        }

    @staticmethod
    async def resolved_per_queue_in_window(
        db: AsyncSession,
        *,
        organization_id: int,
        start: datetime,
        end: datetime,
    ) -> dict[int, int]:
        result = await db.execute(
            select(
                Ticket.service_queue_id.label("queue_id"),
                func.count().label("count"),
            ).where(
                Ticket.organization_id == organization_id,
                Ticket.resolved_at.isnot(None),
                Ticket.service_queue_id.is_not(None),
                Ticket.resolved_at >= start,
                Ticket.resolved_at <= end,
            ).group_by(Ticket.service_queue_id)
        )
        return {row.queue_id: int(row._mapping["count"]) for row in result.all()}

    @staticmethod
    async def agent_runs_per_queue_in_window(
        db: AsyncSession,
        *,
        organization_id: int,
        start: datetime,
        end: datetime,
    ) -> dict[int, dict]:
        result = await db.execute(
            select(
                Ticket.service_queue_id.label("queue_id"),
                func.count(AgentRun.id).label("runs"),
                func.coalesce(
                    func.sum(
                        case(
                            (AgentRun.authorization_source == "policy_auto", 1),
                            else_=0,
                        )
                    ),
                    0,
                ).label("autonomous"),
            )
            .join(Ticket, Ticket.id == AgentRun.ticket_id)
            .where(
                AgentRun.organization_id == organization_id,
                AgentRun.created_at >= start,
                AgentRun.created_at <= end,
                Ticket.service_queue_id.is_not(None),
            )
            .group_by(Ticket.service_queue_id)
        )
        return {
            row.queue_id: {
                "runs": int(row.runs),
                "autonomous": int(row.autonomous),
            }
            for row in result.all()
        }

    @staticmethod
    async def channel_conversation_counts_for_window(
        db: AsyncSession,
        *,
        organization_id: int,
        start: datetime,
        end: datetime,
    ) -> dict[str, int]:
        result = await db.execute(
            select(
                Conversation.channel.label("channel"),
                func.count(Conversation.id).label("count"),
            )
            .where(
                Conversation.organization_id == organization_id,
                Conversation.created_at >= start,
                Conversation.created_at <= end,
            )
            .group_by(Conversation.channel)
            .order_by(Conversation.channel)
        )
        return {str(row.channel): int(row._mapping["count"]) for row in result.all()}

    @staticmethod
    async def channel_message_counts_for_window(
        db: AsyncSession,
        *,
        organization_id: int,
        start: datetime,
        end: datetime,
    ) -> dict[str, int]:
        result = await db.execute(
            select(
                Conversation.channel.label("channel"),
                func.count(ConversationMessage.id).label("count"),
            )
            .join(
                Conversation,
                (Conversation.id == ConversationMessage.conversation_id)
                & (Conversation.organization_id == ConversationMessage.organization_id),
            )
            .where(
                ConversationMessage.organization_id == organization_id,
                ConversationMessage.sent_at.isnot(None),
                ConversationMessage.sent_at >= start,
                ConversationMessage.sent_at <= end,
            )
            .group_by(Conversation.channel)
            .order_by(Conversation.channel)
        )
        return {str(row.channel): int(row._mapping["count"]) for row in result.all()}