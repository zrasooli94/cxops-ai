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
from app.services.tool_authorization_service import (
    ToolAuthorizationError,
    ToolAuthorizationService,
)
from app.core.metrics import (
    record_agent_execution_failure,
    record_agent_tool_execution,
    record_autonomous_execution,
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
                db=db,
                run_id=run_id,
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
                    "",
                )
            )

            if marker in body:
                return True

        return False

    async def _execute_tool(
        self,
        db: AsyncSession,
        *,
        run: AgentRun,
        ticket: Ticket,
        zendesk_ticket_id: int,
        tool_call: dict,
    ) -> None:

        tool_name = tool_call.get(
            "tool"
        )

        arguments = (
            tool_call.get(
                "arguments"
            )
            or {}
        )

        marker = self._comment_marker(
            run.run_id
        )

        # -----------------------------------------
        # Update Zendesk ticket fields
        # -----------------------------------------

        if (
            tool_name
            == "zendesk.update_ticket"
        ):

            team = arguments.get(
                "team"
            )

            priority = arguments.get(
                "priority"
            )

            group_id = None

            if team:

                group_id = (
                    await self.zendesk
                    .find_group_id(
                        db,
                        str(team),
                    )
                )

            await (
                self.zendesk
                .apply_agent_action(
                    db,
                    zendesk_ticket_id,
                    priority=priority,
                    group_id=group_id,
                )
            )

            await (
                AgentRunRepository
                .add_event(
                    db=db,
                    agent_run_id=run.id,
                    event_type="tool_executed",
                    actor="cxops-agent",
                    event_data={
                        "tool": tool_name,
                        "arguments": (
                            arguments
                        ),
                        "zendesk_ticket_id": (
                            zendesk_ticket_id
                        ),
                        "resolved_group_id": (
                            group_id
                        ),
                    },
                )
            )
            
            record_agent_tool_execution(
                tool=str(
                    tool_name
                ),
            )

            return

        # -----------------------------------------
        # Add private Zendesk note
        # -----------------------------------------

        if (
            tool_name
            == "zendesk.add_internal_note"
        ):

            reason = str(
                arguments.get(
                    "reason",
                    run.reason,
                )
            )

            body = (
                "CXOps AI approved agent action\n"
                f"Action: {run.action}\n"
                f"Reason: {reason}\n\n"
                f"{marker}"
            )

            await (
                self.zendesk
                .apply_agent_action(
                    db,
                    zendesk_ticket_id,
                    comment=body,
                    public=False,
                )
            )

            await (
                AgentRunRepository
                .add_event(
                    db=db,
                    agent_run_id=run.id,
                    event_type="tool_executed",
                    actor="cxops-agent",
                    event_data={
                        "tool": tool_name,
                        "arguments": (
                            arguments
                        ),
                        "zendesk_ticket_id": (
                            zendesk_ticket_id
                        ),
                    },
                )
            )
            record_agent_tool_execution(
                tool=str(
                    tool_name
                ),
            )
            return

        # -----------------------------------------
        # Send public customer reply
        # -----------------------------------------

        if (
            tool_name
            == "zendesk.send_reply"
        ):

            body = arguments.get(
                "body"
            )

            if not body:
                raise AgentExecutionError(
                    "zendesk.send_reply "
                    "requires a response body."
                )

            reply = (
                f"{body}\n\n"
                f"{marker}"
            )

            await (
                self.zendesk
                .apply_agent_action(
                    db,
                    zendesk_ticket_id,
                    comment=reply,
                    public=True,
                )
            )

            await (
                AgentRunRepository
                .add_event(
                    db=db,
                    agent_run_id=run.id,
                    event_type="tool_executed",
                    actor="cxops-agent",
                    event_data={
                        "tool": tool_name,
                        "zendesk_ticket_id": (
                            zendesk_ticket_id
                        ),
                    },
                )
            )
            record_agent_tool_execution(
                tool=str(
                    tool_name
                ),
            )
            return

        # -----------------------------------------
        # Non-executable tools
        # -----------------------------------------

        if tool_name in {
            "human.review",
            "none",
        }:

            raise AgentExecutionStateError(
                f"Tool '{tool_name}' "
                "does not require external "
                "execution."
            )

        raise AgentExecutionError(
            f"Unsupported agent tool: "
            f"{tool_name}"
        )

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
                f"Agent run {run_id} "
                "was not found."
            )

        # -----------------------------------------
        # Already completed
        # -----------------------------------------

        if (
            existing_run.status
            == "executed"
        ):

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
                    "Agent run was "
                    "already executed."
                ),
            }

        # -----------------------------------------
        # Non-executable agent decisions
        # -----------------------------------------

        if existing_run.action in {
            "human_review",
            "no_action",
        }:

            raise AgentExecutionStateError(
                f"Action "
                f"'{existing_run.action}' "
                "does not require external "
                "execution."
            )

        # -----------------------------------------
        # Valid execution states
        # -----------------------------------------

        if existing_run.status not in {
            "approved",
            "execution_failed",
        }:

            raise AgentExecutionStateError(
                "Agent run cannot be "
                "executed from status "
                f"{existing_run.status}."
            )

        # -----------------------------------------
        # Retry previously failed execution
        # -----------------------------------------

        if (
            existing_run.status
            == "execution_failed"
        ):

            existing_run.status = (
                "approved"
            )

            await db.commit()

        run = await (
            AgentRunRepository
            .claim_for_execution(
                db=db,
                run_id=run_id,
            )
        )

        if run is None:

            raise AgentExecutionStateError(
                "Agent run could not be "
                "claimed for execution."
            )

        # -----------------------------------------
        # Load local ticket
        # -----------------------------------------

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
                    (
                        "Local ticket "
                        "was not found."
                    ),
                )
            )
            record_agent_execution_failure(
                action=str(
                    run.action
                ),
            )
            raise AgentExecutionError(
                "Local ticket was not found."
            )

        if not ticket.external_id:

            await (
                AgentRunRepository
                .mark_execution_failed(
                    db,
                    run,
                    (
                        "Ticket has no Zendesk "
                        "external_id."
                    ),
                )
            )
            record_agent_execution_failure(
                action=str(
                    run.action
                ),
            )
            raise AgentExecutionError(
                "Ticket is not linked "
                "to Zendesk."
            )

        zendesk_ticket_id = int(
            ticket.external_id
        )

        tool_plan = (
            run.tool_plan
            or []
        )
        
        try:
            ToolAuthorizationService.assert_executable(
                tool_plan
            )

        except ToolAuthorizationError as exc:
        
            await (
                AgentRunRepository
                .mark_execution_failed(
                    db,
                    run,
                    str(exc),
                )
            )
            record_agent_execution_failure(
                action=str(
                    run.action
                ),
            )
            raise AgentExecutionStateError(
                str(exc)
            ) from exc
        
        if not tool_plan:

            await (
                AgentRunRepository
                .mark_execution_failed(
                    db,
                    run,
                    (
                        "Agent run contains "
                        "no tool plan."
                    ),
                )
            )
            record_agent_execution_failure(
                action=str(
                    run.action
                ),
            )
            raise AgentExecutionError(
                "Agent run contains "
                "no executable tool plan."
            )

        try:

            # -------------------------------------
            # Idempotency / crash recovery
            # -------------------------------------

            already_written = (
                await self._already_written(
                    db,
                    zendesk_ticket_id=(
                        zendesk_ticket_id
                    ),
                    run_id=run_id,
                )
            )

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
                        db=db,
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
                if (
                    run.reviewer_note
                    == (
                        "Automatically approved by "
                        "low-risk tool policy"
                    )
                ):
                    record_autonomous_execution(
                        action=str(
                            run.action
                        ),
                        outcome="recovered",
                    )
                return {
                    "run_id": run_id,
                    "ticket_id": (
                        ticket.id
                    ),
                    "status": "executed",
                    "action": run.action,
                    "external_ticket_id": (
                        ticket.external_id
                    ),
                    "executed": True,
                    "duplicate": True,
                    "message": (
                        "Existing Zendesk "
                        "execution detected "
                        "and safely recovered."
                    ),
                }

            # -------------------------------------
            # Execute persisted tool plan
            # -------------------------------------

            for tool_call in tool_plan:

                await self._execute_tool(
                    db,
                    run=run,
                    ticket=ticket,
                    zendesk_ticket_id=(
                        zendesk_ticket_id
                    ),
                    tool_call=tool_call,
                )

            # -------------------------------------
            # Synchronize external state
            # -------------------------------------

            await (
                ZendeskSyncService
                .sync_ticket(
                    db,
                    zendesk_ticket_id,
                )
            )

            # -------------------------------------
            # Finish execution
            # -------------------------------------

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
                    db=db,
                    agent_run_id=run.id,
                    event_type="executed",
                    actor="cxops-agent",
                    event_data={
                        "zendesk_ticket_id": (
                            zendesk_ticket_id
                        ),
                        "action": run.action,
                        "tool_count": len(
                            tool_plan
                        ),
                        "tools": [
                            tool.get(
                                "tool"
                            )
                            for tool
                            in tool_plan
                        ],
                    },
                )
            )
            #
            if (
                run.reviewer_note
                == (
                    "Automatically approved by "
                    "low-risk tool policy"
                )
            ):
                record_autonomous_execution(
                    action=str(
                        run.action
                    ),
                    outcome="executed",
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
                    "Approved agent tool "
                    "plan executed "
                    "successfully."
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
                        db=db,
                        agent_run_id=(
                            fresh_run.id
                        ),
                        event_type=(
                            "execution_failed"
                        ),
                        actor="cxops-agent",
                        note=str(exc),
                        event_data={
                            "tool_plan": (
                                fresh_run.tool_plan
                                or []
                            ),
                        },
                    )
                )
                record_agent_execution_failure(
                    action=str(
                        fresh_run.action
                    ),
                )

            if isinstance(
                exc,
                (
                    AgentExecutionError,
                    AgentExecutionStateError,
                ),
            ):
                raise

            raise AgentExecutionError(
                str(exc)
            ) from exc


agent_execution_service = (
    AgentExecutionService()
)