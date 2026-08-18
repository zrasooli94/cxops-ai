from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_action_event import AgentActionEvent
from app.models.agent_run import AgentRun


class AgentRunRepository:

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        run_id: str,
        ticket_id: int,
        decision: dict,
        sources: list[dict],
    ) -> AgentRun:

        run = AgentRun(
            run_id=run_id,
            ticket_id=ticket_id,
            action=decision["action"],
            reason=decision["reason"],
            recommended_team=decision.get(
                "recommended_team"
            ),
            recommended_priority=decision.get(
                "recommended_priority"
            ),
            response_draft=decision.get(
                "response_draft"
            ),
            requires_human_approval=decision.get(
                "requires_human_approval",
                True,
            ),
            status="pending_approval",
            sources=sources,
        )

        db.add(run)
        await db.commit()
        await db.refresh(run)

        return run

    @staticmethod
    async def get_by_run_id(
        db: AsyncSession,
        run_id: str,
    ) -> AgentRun | None:

        result = await db.execute(
            select(AgentRun).where(
                AgentRun.run_id == run_id
            )
        )

        return result.scalar_one_or_none()

    @staticmethod
    async def approve(
        db: AsyncSession,
        run: AgentRun,
        note: str | None,
    ) -> AgentRun:

        run.status = "approved"
        run.reviewer_note = note
        run.reviewed_at = datetime.now(
            timezone.utc
        )

        await db.commit()
        await db.refresh(run)

        return run

    @staticmethod
    async def reject(
        db: AsyncSession,
        run: AgentRun,
        note: str | None,
    ) -> AgentRun:

        run.status = "rejected"
        run.reviewer_note = note
        run.reviewed_at = datetime.now(
            timezone.utc
        )

        await db.commit()
        await db.refresh(run)

        return run

    @staticmethod
    async def add_event(
        db: AsyncSession,
        *,
        agent_run_id: int,
        event_type: str,
        actor: str,
        note: str | None = None,
        event_data: dict | None = None,
    ) -> AgentActionEvent:

        event = AgentActionEvent(
            agent_run_id=agent_run_id,
            event_type=event_type,
            actor=actor,
            note=note,
            event_data=event_data or {},
        )

        db.add(event)

        await db.commit()
        await db.refresh(event)

        return event

    @staticmethod
    async def claim_for_execution(
        db: AsyncSession,
        run_id: str,
    ) -> AgentRun | None:
    
        result = await db.execute(
            update(AgentRun)
            .where(
                AgentRun.run_id == run_id,
                AgentRun.status == "approved",
            )
            .values(
                status="executing"
            )
            .returning(
                AgentRun.id
            )
        )
    
        claimed_id = (
            result.scalar_one_or_none()
        )
    
        await db.commit()
    
        if claimed_id is None:
            return None
    
        result = await db.execute(
            select(AgentRun).where(
                AgentRun.id == claimed_id
            )
        )

        return result.scalar_one()

    
    @staticmethod
    async def mark_executed(
        db: AsyncSession,
        run: AgentRun,
    ) -> AgentRun:
    
        run.status = "executed"
        run.executed_at = datetime.now(
            timezone.utc
        )
    
        run.error_message = None
    
        await db.commit()
        await db.refresh(run)
    
        return run
    
    
    @staticmethod
    async def mark_execution_failed(
        db: AsyncSession,
        run: AgentRun,
        error_message: str,
    ) -> AgentRun:
    
        run.status = "execution_failed"
    
        run.error_message = (
            error_message[:4000]
        )
    
        await db.commit()
        await db.refresh(run)
    
        return run