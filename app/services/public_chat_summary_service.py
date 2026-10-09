"""Read-only tenant-scoped pilot summary for the public-chat surface (Phase 1P.7).

The live-pilot summary is an operations/monitoring surface. It distills the
public-chat tables, the grounded-auto-reply audit trail, the RAG observability
log, and the integration/execution records into bounded aggregates for the
control-center dashboard. Two invariants hold:

* **Tenant-scoped in SQL.** Every aggregate starts from a server-resolved
  ``organization_id`` (the authenticated principal's tenant — never a client
  value) and enforces the predicate in SQL. NULL-org legacy rows remain inert
  and are never counted.
* **No content.** Responses are counts, timestamps, rates, and costs. Customer
  message text, session tokens, widget keys, IPs, prompts, and other PII are
  never part of an aggregation or of the returned shape.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import case, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_action_event import AgentActionEvent
from app.models.agent_run import AgentRun
from app.models.ai_request_log import AIRequestLog
from app.models.conversation_message import ConversationMessage
from app.models.integration_job import IntegrationJob
from app.models.public_chat import PublicChatConfiguration, PublicChatSession
from app.services.integration_job_service import IntegrationJobService

# Closed set of supported summary windows, mapped to look-back hours.
WINDOW_TO_HOURS: dict[str, int] = {
    "24h": 24,
    "7d": 168,
}

# RAG observability rows are attributed to the widget via this feature label.
PUBLIC_CHAT_RAG_FEATURE = "public_chat"

GROUNDED_AUTO_REPLY_EVENT = "public_grounded_auto_reply"

# The pilot is operated by humans on a queue. A wait over this long is an
# operator-attention signal only — it is deliberately NOT an SLA.
QUEUE_ATTENTION_THRESHOLD_MINUTES = 15

# Only sessions that are still live (expires_at in the future) are a queue
# problem: an expired session is nobody's pending work.
_ACTIVE_STATUSES = ("ai_active", "human_requested", "human_assigned")

# The health signal measures only sessions currently waiting for their first
# staff assignment. Assigned sessions are already being handled by a human, so
# their age is not waiting time.
WAITING_STATUSES = ("human_requested",)

# Canonical job type for agent-execution integration jobs
# (``IntegrationJobService.AGENT_EXECUTION``); any other job type is not an
# agent execution and is never attributed to public chat.
AGENT_EXECUTION_JOB_TYPE = IntegrationJobService.AGENT_EXECUTION

# Agent-run statuses whose run represents an attempted public-chat execution.
# A ``policy_auto`` run in either of these crossed the human-approval boundary
# and actually attempted the tool.
EXECUTION_ATTEMPTED_STATUSES = ("executed", "execution_failed")


def _public_chat_ticket_ids(organization_id: int):
    """Subquery of ticket ids that belong to a public-chat session in the tenant."""
    return select(PublicChatSession.ticket_id).where(
        PublicChatSession.organization_id == organization_id
    )


class PublicChatSummaryService:
    """Read-only aggregation of live-pilot health for one tenant.

    Every method is a plain SELECT; nothing here mutates, commits, or writes
    audit events. Callers resolve the tenant through ``CurrentTenant``.
    """

    @staticmethod
    async def _config_summary(
        db: AsyncSession,
        organization_id: int,
    ) -> dict:
        result = await db.execute(
            select(PublicChatConfiguration).where(
                PublicChatConfiguration.organization_id == organization_id
            )
        )
        config = result.scalar_one_or_none()
        if config is None:
            return {
                "widget_enabled": False,
                "grounded_auto_reply_enabled": False,
                "theme_token": None,
                "allowed_origin_count": 0,
            }
        return {
            "widget_enabled": config.enabled,
            "grounded_auto_reply_enabled": config.grounded_auto_reply_enabled,
            "theme_token": config.theme_token,
            "allowed_origin_count": len(config.allowed_origins or []),
        }

    @staticmethod
    async def _queue_summary(
        db: AsyncSession,
        organization_id: int,
    ) -> tuple[dict, dict]:
        """Current queue counts plus operator-attention queue health.

        Queue counts cover only sessions that are still live (``expires_at`` in
        the future): expired sessions are nobody's pending work and never count
        toward ``active_total``.

        The attention signal measures only sessions currently waiting for their
        first staff assignment (``status == 'human_requested'`` and live) — an
        assigned session is already being handled by a human, so its age is not
        "waiting". The schema has no dedicated ``handoff_requested_at``
        timestamp, so the honest proxy is ``updated_at``, which is written on
        the status transition into ``human_requested``: ``oldest_waiting_since``
        is "waiting since the latest entry into ``human_requested``". The
        15-minute threshold remains an operator-attention signal, not an SLA.
        """
        now = datetime.now(UTC)
        counts = {status: 0 for status in _ACTIVE_STATUSES}
        result = await db.execute(
            select(
                PublicChatSession.status,
                func.count(PublicChatSession.id),
            )
            .where(
                PublicChatSession.organization_id == organization_id,
                PublicChatSession.status.in_(_ACTIVE_STATUSES),
                PublicChatSession.expires_at > now,
            )
            .group_by(PublicChatSession.status)
        )
        for status, count in result.all():
            counts[status] = int(count)

        queue = {
            "human_requested": counts["human_requested"],
            "human_assigned": counts["human_assigned"],
            "ai_active": counts["ai_active"],
            "active_total": sum(counts.values()),
        }

        oldest_result = await db.execute(
            select(func.min(PublicChatSession.updated_at)).where(
                PublicChatSession.organization_id == organization_id,
                PublicChatSession.status.in_(WAITING_STATUSES),
                PublicChatSession.expires_at > now,
            )
        )
        oldest_waiting_since = oldest_result.scalar_one_or_none()
        oldest_waiting_minutes: int | None = None
        if oldest_waiting_since is not None:
            oldest_waiting_minutes = int(
                max(
                    0,
                    (datetime.now(UTC) - oldest_waiting_since).total_seconds()
                    // 60,
                )
            )

        health = "normal"
        if (
            oldest_waiting_minutes is not None
            and oldest_waiting_minutes >= QUEUE_ATTENTION_THRESHOLD_MINUTES
        ):
            health = "attention"

        queue_health = {
            "health": health,
            "oldest_waiting_since": oldest_waiting_since,
            "oldest_waiting_minutes": oldest_waiting_minutes,
        }
        return queue, queue_health

    @staticmethod
    async def _window_summary(
        db: AsyncSession,
        organization_id: int,
        since: datetime,
    ) -> dict:
        sessions_created = await db.execute(
            select(func.count(PublicChatSession.id)).where(
                PublicChatSession.organization_id == organization_id,
                PublicChatSession.created_at >= since,
            )
        )

        customer_messages = await db.execute(
            select(func.count(ConversationMessage.id))
            .select_from(ConversationMessage)
            .where(
                ConversationMessage.organization_id == organization_id,
                ConversationMessage.direction == "inbound",
                ConversationMessage.created_at >= since,
                ConversationMessage.conversation_id.in_(
                    select(PublicChatSession.conversation_id).where(
                        PublicChatSession.organization_id == organization_id
                    )
                ),
            )
        )

        sessions_closed = await db.execute(
            select(func.count(PublicChatSession.id)).where(
                PublicChatSession.organization_id == organization_id,
                PublicChatSession.status == "closed",
                PublicChatSession.closed_at >= since,
            )
        )

        grounded_replies = await db.execute(
            select(func.count(AgentActionEvent.id))
            .join(AgentRun, AgentRun.id == AgentActionEvent.agent_run_id)
            .where(
                AgentActionEvent.event_type == GROUNDED_AUTO_REPLY_EVENT,
                AgentRun.organization_id == organization_id,
                AgentActionEvent.created_at >= since,
            )
        )

        return {
            "sessions_created": int(sessions_created.scalar_one()),
            "customer_messages": int(customer_messages.scalar_one()),
            "sessions_closed": int(sessions_closed.scalar_one()),
            "grounded_public_auto_replies": int(grounded_replies.scalar_one()),
        }

    @staticmethod
    async def _rag_summary(
        db: AsyncSession,
        organization_id: int,
        since: datetime,
    ) -> dict:
        """RAG observability attributed to the public widget.

        Only rows written with ``feature='public_chat'`` are counted. Rows
        recorded before Phase 1P.7 carry the shared ``'rag_answer'`` label and
        cannot be attributed to the widget without guessing, so they are
        deliberately excluded (a lower bound, never an approximation). Error
        rows are durable only from the moment the widget began recording them.

        ``avg_rag_latency_ms`` averages successful rows only. The synthetic
        failure marker rows the widget writes carry ``latency_ms=0.0``, so
        folding them in would drag the latency figure down with manufactured
        zeros; averaging over successes keeps the number an honest signal, and
        a window with no successful rows reports ``0.0``.
        """
        result = await db.execute(
            select(
                func.count(AIRequestLog.id),
                func.sum(
                    case(
                        (AIRequestLog.grounded.is_(True), 1),
                        else_=0,
                    )
                ),
                func.sum(
                    case(
                        (AIRequestLog.status != "success", 1),
                        else_=0,
                    )
                ),
                func.avg(
                    case(
                        (AIRequestLog.status == "success", AIRequestLog.latency_ms),
                        else_=None,
                    )
                ),
                func.avg(AIRequestLog.best_similarity),
                func.sum(AIRequestLog.estimated_cost_usd),
            ).where(
                AIRequestLog.organization_id == organization_id,
                AIRequestLog.feature == PUBLIC_CHAT_RAG_FEATURE,
                AIRequestLog.created_at >= since,
            )
        )
        row = result.one()
        return {
            "rag_requests": int(row[0] or 0),
            "rag_grounded": int(row[1] or 0),
            "rag_errors": int(row[2] or 0),
            "avg_rag_latency_ms": float(row[3] or 0),
            "avg_best_similarity": float(row[4]) if row[4] is not None else None,
            "estimated_ai_cost_usd": float(row[5] or 0),
        }

    @staticmethod
    async def _safety_summary(
        db: AsyncSession,
        organization_id: int,
        since: datetime,
    ) -> dict:
        """Agent-execution jobs and autonomous executions attributable to public chat.

        These are expected to stay at zero for the pilot. Attribution is exact:

        * An integration job belongs to public chat only when it is an
          agent-execution job (``job_type == 'agent.execute'``) whose payload
          ``run_id`` resolves to a tenant-scoped agent run on a ticket that
          owns a ``PublicChatSession`` in this tenant.
        * An autonomous execution is an agent run authorized by policy
          (``authorization_source == 'policy_auto'``) that attempted execution
          (``status`` in ``executed`` / ``execution_failed``) on a public-chat
          ticket in this tenant. Human-approved runs
          (``authorization_source == 'human_approval'``) are deliberately NOT
          counted as autonomous even though they carry a ``run_id``. The window
          uses the execution timestamp ``executed_at`` when populated (executed
          runs); ``execution_failed`` runs do not populate ``executed_at``, so
          ``created_at`` is the documented fallback.
        """
        ticket_ids = _public_chat_ticket_ids(organization_id)

        integration_jobs = await db.execute(
            select(func.count(IntegrationJob.id)).where(
                IntegrationJob.organization_id == organization_id,
                IntegrationJob.job_type == AGENT_EXECUTION_JOB_TYPE,
                IntegrationJob.created_at >= since,
                exists(
                    select(AgentRun.id).where(
                        AgentRun.organization_id == organization_id,
                        AgentRun.run_id == IntegrationJob.payload["run_id"].astext,
                        AgentRun.ticket_id.in_(ticket_ids),
                    )
                ),
            )
        )

        autonomous_executions = await db.execute(
            select(func.count(AgentRun.id)).where(
                AgentRun.organization_id == organization_id,
                AgentRun.ticket_id.in_(ticket_ids),
                AgentRun.authorization_source == "policy_auto",
                AgentRun.status.in_(EXECUTION_ATTEMPTED_STATUSES),
                func.coalesce(
                    AgentRun.executed_at, AgentRun.created_at
                )
                >= since,
            )
        )

        return {
            "public_chat_integration_jobs": int(integration_jobs.scalar_one()),
            "autonomous_public_chat_executions": int(
                autonomous_executions.scalar_one()
            ),
        }

    @classmethod
    async def summary(
        cls,
        db: AsyncSession,
        *,
        organization_id: int,
        window_hours: int,
    ) -> dict:
        since = datetime.now(UTC) - timedelta(hours=window_hours)
        config = await cls._config_summary(db, organization_id)
        queue, queue_health = await cls._queue_summary(db, organization_id)
        window_summary = await cls._window_summary(db, organization_id, since)
        rag = await cls._rag_summary(db, organization_id, since)
        safety = await cls._safety_summary(db, organization_id, since)
        return {
            "config": config,
            "queue": queue,
            "queue_health": queue_health,
            "window_summary": window_summary,
            "rag": rag,
            "safety": safety,
        }


public_chat_summary_service = PublicChatSummaryService()