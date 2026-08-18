from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.zendesk.client import (
    ZendeskClient,
)
from app.models.agent_run import AgentRun
from app.models.ticket import Ticket
from app.repositories.agent_run_repository import (
    AgentRunRepository,
)
from app.services.zendesk_sync_service import (
    ZendeskSyncService,
)


class AgentExecutionError(Exception):
    pass


class AgentExecutionStateError(Exception):
    pass


class AgentExecutionService:

    def __init__(self) -> None:
        self.zendesk = ZendeskClient()

    @staticmethod
    async def _get_run(
        db: AsyncSession,
        run_id: str,
    ) -> AgentRun | None:

        return await (
            AgentRunRepository
            .get_by_run_id(
                db,
                run_id,
            )
        )

    @staticmethod
    async def _get_ticket(
        db: AsyncSession,
        ticket_id: int,
    ) -> Ticket | None:

        result = await db.execute(
            select(Ticket).where(
                Ticket.id == ticket_id
            )
        )

        return (
            result.scalar_one_or_none()
        )

    @staticmethod
    def _comment_marker(
        run_id: str,
    ) -> str:

        return (
            f"CXOps Agent Run: {run_id}"
        )

    async def _already_written(
        self,
        db: AsyncSession,
        *,
        zendesk_ticket_id: int,
        run_id: str,
    ) -> bool:

        response = (
            await self.zendesk
            .get_ticket_comments(
                db,
                zendesk_ticket_id,
            )
        )

        comments = response.get(
            "comments",
            [],
        )

        marker = self._comment_marker(
            run_id
        )

        for comment in comments:

            body = str(
                comment.get(
                    "body",
                    ""
                )
            )

            if marker in body:
                return True

        return False

    async def execute(
        self,
        db: AsyncSession,
        *,
        run_id: str,
    ) -> dict:

        existing_run = await self._get_run(
            db,
            run_id,
        )

        if existing_run is None:
            raise AgentExecutionError(
                f"Agent run {run_id} was not found"
            )

        if existing_run.status == "executed":

            ticket = await self._get_ticket(
                db,
                existing_run.ticket_id,
            )

            return {
                "run_id": run_id,
                "ticket_id": (
                    existing_run.ticket_id
                ),
                "status": "executed",
                "action": (
                    existing_run.action
                ),
                "external_ticket_id": (
                    ticket.external_id
                    if ticket
                    else None
                ),
                "executed": True,
                "duplicate": True,
                "message": (
                    "Agent run was already executed."
                ),
            }

        if existing_run.status not in {
            "approved",
            "execution_failed",
        }:
            raise AgentExecutionStateError(
                "Agent run cannot be executed "
                f"from status "
                f"{existing_run.status}"
            )

        # Allow an explicit retry after a failed execution.
        if (
            existing_run.status
            == "execution_failed"
        ):
            existing_run.status = "approved"
            await db.commit()

        run = await (
            AgentRunRepository
            .claim_for_execution(
                db,
                run_id,
            )
        )

        if run is None:
            raise AgentExecutionStateError(
                "Agent run could not be claimed "
                "for execution."
            )

        ticket = await self._get_ticket(
            db,
            run.ticket_id,
        )

        if ticket is None:

            await (
                AgentRunRepository
                .mark_execution_failed(
                    db,
                    run,
                    "Local ticket was not found",
                )
            )

            raise AgentExecutionError(
                "Local ticket was not found"
            )

        if not ticket.external_id:

            await (
                AgentRunRepository
                .mark_execution_failed(
                    db,
                    run,
                    (
                        "Ticket has no Zendesk "
                        "external_id"
                    ),
                )
            )

            raise AgentExecutionError(
                "Ticket is not linked to Zendesk"
            )

        zendesk_ticket_id = int(
            ticket.external_id
        )

        if run.action in {
            "human_review",
            "no_action",
        }:

            await (
                AgentRunRepository
                .mark_execution_failed(
                    db,
                    run,
                    (
                        f"Action '{run.action}' "
                        "has no external execution."
                    ),
                )
            )

            raise AgentExecutionStateError(
                f"Action '{run.action}' "
                "cannot be executed."
            )

        try:

            already_written = (
                await self._already_written(
                    db,
                    zendesk_ticket_id=(
                        zendesk_ticket_id
                    ),
                    run_id=run_id,
                )
            )

            # Handles the case where Zendesk succeeded
            # but our worker/API crashed before DB completion.
            if already_written:

                await (
                    AgentRunRepository
                    .mark_executed(
                        db,
                        run,
                    )
                )

                await (
                    AgentRunRepository
                    .add_event(
                        db,
                        agent_run_id=run.id,
                        event_type=(
                            "execution_recovered"
                        ),
                        actor="cxops-agent",
                        event_data={
                            "zendesk_ticket_id": (
                                zendesk_ticket_id
                            ),
                            "run_id": run_id,
                        },
                    )
                )

                return {
                    "run_id": run_id,
                    "ticket_id": ticket.id,
                    "status": "executed",
                    "action": run.action,
                    "external_ticket_id": (
                        ticket.external_id
                    ),
                    "executed": True,
                    "duplicate": True,
                    "message": (
                        "Existing Zendesk action "
                        "was detected and execution "
                        "was safely recovered."
                    ),
                }

            group_id = None

            if run.recommended_team:

                group_id = (
                    await self.zendesk
                    .find_group_id(
                        db,
                        run.recommended_team,
                    )
                )

            marker = self._comment_marker(
                run_id
            )

            if run.action == "respond":

                if not run.response_draft:
                    raise AgentExecutionError(
                        "Agent proposed a response "
                        "but produced no response_draft."
                    )

                comment = (
                    f"{run.response_draft}\n\n"
                    f"{marker}"
                )

                public = True

            else:

                comment = (
                    "CXOps AI approved agent action\n"
                    f"Action: {run.action}\n"
                    f"Reason: {run.reason}\n"
                    f"Recommended team: "
                    f"{run.recommended_team or 'none'}\n"
                    f"Recommended priority: "
                    f"{run.recommended_priority or 'unchanged'}\n\n"
                    f"{marker}"
                )

                public = False

            await self.zendesk.apply_agent_action(
                db,
                zendesk_ticket_id,
                priority=(
                    run.recommended_priority
                ),
                group_id=group_id,
                comment=comment,
                public=public,
            )

            # Pull Zendesk state back into CXOps.
            await ZendeskSyncService.sync_ticket(
                db,
                zendesk_ticket_id,
            )

            await (
                AgentRunRepository
                .mark_executed(
                    db,
                    run,
                )
            )

            await (
                AgentRunRepository
                .add_event(
                    db,
                    agent_run_id=run.id,
                    event_type="executed",
                    actor="cxops-agent",
                    event_data={
                        "zendesk_ticket_id": (
                            zendesk_ticket_id
                        ),
                        "action": run.action,
                        "priority": (
                            run.recommended_priority
                        ),
                        "recommended_team": (
                            run.recommended_team
                        ),
                        "resolved_group_id": (
                            group_id
                        ),
                        "public_comment": (
                            public
                        ),
                    },
                )
            )

            return {
                "run_id": run_id,
                "ticket_id": ticket.id,
                "status": "executed",
                "action": run.action,
                "external_ticket_id": (
                    ticket.external_id
                ),
                "executed": True,
                "duplicate": False,
                "message": (
                    "Approved agent action "
                    "executed successfully."
                ),
            }

        except Exception as exc:

            await db.rollback()

            fresh_run = await self._get_run(
                db,
                run_id,
            )

            if (
                fresh_run is not None
                and fresh_run.status
                != "executed"
            ):

                await (
                    AgentRunRepository
                    .mark_execution_failed(
                        db,
                        fresh_run,
                        str(exc),
                    )
                )

                await (
                    AgentRunRepository
                    .add_event(
                        db,
                        agent_run_id=(
                            fresh_run.id
                        ),
                        event_type=(
                            "execution_failed"
                        ),
                        actor="cxops-agent",
                        note=str(exc),
                    )
                )

            if isinstance(
                exc,
                AgentExecutionError,
            ):
                raise

            raise AgentExecutionError(
                str(exc)
            ) from exc


agent_execution_service = (
    AgentExecutionService()
)