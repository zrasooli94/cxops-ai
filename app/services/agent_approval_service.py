from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_run import AgentRun
from app.repositories.agent_run_repository import (
    AgentRunRepository,
)


class AgentRunNotFoundError(Exception):
    pass


class InvalidAgentRunStateError(Exception):
    pass


class AgentApprovalService:

    @staticmethod
    async def _get_pending_run(
        db: AsyncSession,
        run_id: str,
    ) -> AgentRun:

        run = await (
            AgentRunRepository.get_by_run_id(
                db,
                run_id,
            )
        )

        if run is None:
            raise AgentRunNotFoundError(
                f"Agent run {run_id} was not found"
            )

        if run.status != "pending_approval":
            raise InvalidAgentRunStateError(
                f"Agent run is already {run.status}"
            )

        return run

    @staticmethod
    async def approve(
        db: AsyncSession,
        *,
        run_id: str,
        note: str | None,
        actor: str = "human-reviewer",
    ) -> AgentRun:

        run = await (
            AgentApprovalService._get_pending_run(
                db,
                run_id,
            )
        )

        run = await AgentRunRepository.approve(
            db,
            run,
            note,
        )

        await AgentRunRepository.add_event(
            db,
            agent_run_id=run.id,
            event_type="approved",
            actor=actor,
            note=note,
            event_data={
                "action": run.action,
                "recommended_team": (
                    run.recommended_team
                ),
                "recommended_priority": (
                    run.recommended_priority
                ),
            },
        )

        return run

    @staticmethod
    async def reject(
        db: AsyncSession,
        *,
        run_id: str,
        note: str | None,
        actor: str = "human-reviewer",
    ) -> AgentRun:

        run = await (
            AgentApprovalService._get_pending_run(
                db,
                run_id,
            )
        )

        run = await AgentRunRepository.reject(
            db,
            run,
            note,
        )

        await AgentRunRepository.add_event(
            db,
            agent_run_id=run.id,
            event_type="rejected",
            actor=actor,
            note=note,
        )

        return run