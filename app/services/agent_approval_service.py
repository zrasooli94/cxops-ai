from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_run import AgentRun
from app.repositories.agent_run_repository import (
    AgentRunRepository,
)
from app.core.metrics import (
    record_agent_approval,
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
        
        if run.action == "human_review":

            run = await (
                AgentRunRepository
                .mark_review_required(
                    db,
                    run,
                    note,
                )
            )

            await AgentRunRepository.add_event(
                db,
                agent_run_id=run.id,
                event_type="review_required",
                actor=actor,
                note=note,
            )

            return run


        if run.action == "no_action":
        
            run = await (
                AgentRunRepository
                .mark_no_action(
                    db,
                    run,
                    note,
                )
            )

            await AgentRunRepository.add_event(
                db,
                agent_run_id=run.id,
                event_type="no_action",
                actor=actor,
                note=note,
            )

            return run
        run = await AgentRunRepository.approve(
            db,
            run,
            note,
        )

        updated_tool_plan = []

        for tool in run.tool_plan or []:
        
            updated_tool = dict(
                tool
            )
        
            if updated_tool.get(
                "requires_approval",
                True,
            ):
                updated_tool[
                    "authorized"
                ] = True
        
            updated_tool_plan.append(
                updated_tool
            )
        
        run.tool_plan = updated_tool_plan

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
        record_agent_approval(
            result="approved",
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
        record_agent_approval(
            result="rejected",
        )
        return run