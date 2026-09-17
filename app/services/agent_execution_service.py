
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import (
    record_agent_execution_failure,
    record_agent_tool_execution,
    record_autonomous_execution,
)
from app.integrations.zendesk.client import (
    ZendeskClient,
)
from app.models.agent_run import AgentRun
from app.models.ticket import Ticket
from app.repositories.agent_run_repository import (
    AgentRunRepository,
)
from app.services.tool_authorization_service import (
    ToolAuthorizationError,
    ToolAuthorizationService,
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
    async def _get_run_unscoped(
        db: AsyncSession,
        run_id: str,
    ) -> AgentRun | None:
        """INTERNAL-ONLY unscoped run lookup for the durable job worker.

        The worker resolves a run from an agent-execution queue job outside any
        request tenant context. ``execute`` immediately re-validates the
        resolved organization against the job's bound organization before any
        side effect, so this lookup alone never authorizes a write.
        """

        return await AgentRunRepository.get_by_run_id_unscoped(
            db=db,
            run_id=run_id,
        )

    @staticmethod
    async def _get_ticket(
        db: AsyncSession,
        ticket_id: int,
        organization_id: int,
    ) -> Ticket | None:

        result = await db.execute(
            select(Ticket).where(
                Ticket.id == ticket_id,
                Ticket.organization_id == organization_id,
            )
        )

        return result.scalar_one_or_none()

    @staticmethod
    def _comment_marker(
        run_id: str,
    ) -> str:

        return f"CXOps Agent Run: {run_id}"

    async def _already_written(
        self,
        db: AsyncSession,
        *,
        zendesk_ticket_id: int,
        run_id: str,
        organization_id: int,
    ) -> bool:

        response = await self.zendesk.get_ticket_comments(
            db,
            zendesk_ticket_id,
            organization_id=organization_id,
        )

        comments = response.get(
            "comments",
            [],
        )

        marker = self._comment_marker(run_id)

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
        organization_id: int,
    ) -> None:

        tool_name = tool_call.get("tool")

        arguments = tool_call.get("arguments") or {}

        marker = self._comment_marker(run.run_id)

        # -----------------------------------------
        # Update Zendesk ticket fields
        # -----------------------------------------

        if tool_name == "zendesk.update_ticket":
            team = arguments.get("team")

            priority = arguments.get("priority")

            group_id = None

            if team:
                group_id = await self.zendesk.find_group_id(
                    db,
                    str(team),
                    organization_id=organization_id,
                )

            await self.zendesk.apply_agent_action(
                db,
                zendesk_ticket_id,
                organization_id=organization_id,
                priority=priority,
                group_id=group_id,
            )

            await AgentRunRepository.add_event(
                db=db,
                agent_run_id=run.id,
                event_type="tool_executed",
                actor="cxops-agent",
                event_data={
                    "tool": tool_name,
                    "arguments": (arguments),
                    "zendesk_ticket_id": (zendesk_ticket_id),
                    "resolved_group_id": (group_id),
                },
            )

            record_agent_tool_execution(
                tool=str(tool_name),
            )

            return

        # -----------------------------------------
        # Add private Zendesk note
        # -----------------------------------------

        if tool_name == "zendesk.add_internal_note":
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

            await self.zendesk.apply_agent_action(
                db,
                zendesk_ticket_id,
                organization_id=organization_id,
                comment=body,
                public=False,
            )

            await AgentRunRepository.add_event(
                db=db,
                agent_run_id=run.id,
                event_type="tool_executed",
                actor="cxops-agent",
                event_data={
                    "tool": tool_name,
                    "arguments": (arguments),
                    "zendesk_ticket_id": (zendesk_ticket_id),
                },
            )
            record_agent_tool_execution(
                tool=str(tool_name),
            )
            return

        # -----------------------------------------
        # Send public customer reply
        # -----------------------------------------

        if tool_name == "zendesk.send_reply":
            body = arguments.get("body", "")

            if not body:
                raise AgentExecutionError(
                    "zendesk.send_reply requires a response body."
                )

            reply = f"{body}\n\n{marker}"

            await self.zendesk.apply_agent_action(
                db,
                zendesk_ticket_id,
                organization_id=organization_id,
                comment=reply,
                public=True,
            )

            await AgentRunRepository.add_event(
                db=db,
                agent_run_id=run.id,
                event_type="tool_executed",
                actor="cxops-agent",
                event_data={
                    "tool": tool_name,
                    "zendesk_ticket_id": (zendesk_ticket_id),
                },
            )
            record_agent_tool_execution(
                tool=str(tool_name),
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
                f"Tool '{tool_name}' does not require external execution."
            )

        raise AgentExecutionError(f"Unsupported agent tool: {tool_name}")

    @staticmethod
    async def _validate_plan_preflight(
        db,
        run: AgentRun,
        tool_plan: list[dict],
        resolved_organization_id: int,
        zendesk_ticket_id: int,
        organization_id: int,
    ) -> None:
        from app.services.tool_authorization_service import (
            ToolAuthorizationService,
        )
        # 1. Policy version check
        if run.tool_policy_version != ToolAuthorizationService.TOOL_POLICY_VERSION:
            msg = (
                "Tool policy version mismatch: run has "
                + str(run.tool_policy_version)
                + ", expected "
                + str(ToolAuthorizationService.TOOL_POLICY_VERSION)
            )
            raise AgentExecutionStateError(msg)
        # 2. Authorization digest check
        if run.authorization_digest:
            import hashlib
            import json as _json
            digest_payload = {
                "run_id": run.run_id,
                "organization_id": run.organization_id,
                "ticket_id": run.ticket_id,
                "policy_version": (
                    run.tool_policy_version
                    if run.tool_policy_version is not None
                    else ToolAuthorizationService.TOOL_POLICY_VERSION
                ),
                "tool_plan": ToolAuthorizationService.authorize_plan(
                    run.tool_plan or []
                ),
            }
            expected = hashlib.sha256(
                _json.dumps(digest_payload, sort_keys=True, separators=(",", ":")).encode(
                    "utf-8"
                )
            ).hexdigest()
            if run.authorization_digest != expected:
                raise AgentExecutionStateError(
                    "Intent digest mismatch -- plan was modified after authorization."
                )
        # 3. Tool validation (risk, requires_approval, required_capability, args)
        for tool in (tool_plan or []):
            tool_name = tool.get("tool", "")
            if tool_name in ("none", "human.review"):
                continue
            policy = ToolAuthorizationService.POLICIES.get(tool_name)
            if policy is None:
                raise AgentExecutionError("Unrecognized tool in plan: " + tool_name)
            if tool.get("risk_level") != policy["risk_level"]:
                raise AgentExecutionError("Tool " + tool_name + " risk_level mismatch")
            if tool.get("requires_approval") != policy["requires_approval"]:
                raise AgentExecutionError("Tool " + tool_name + " requires_approval mismatch")
            if tool.get("required_capability") != policy["required_capability"]:
                raise AgentExecutionError("Tool " + tool_name + " required_capability mismatch")
            # Check forbidden args
            forbidden = {
                "organization_id",
                "tenant_id",
                "integration_id",
                "credential_id",
                "zendesk_ticket_id",
                "external_ticket_id",
            }
            for key in tool.get("arguments", {}):
                if key in forbidden:
                    raise AgentExecutionError(
                        f"Tool {tool_name} argument {key} is forbidden"
                    )
        # 4. Tenant/org consistency
        if run.organization_id != resolved_organization_id:
            raise AgentExecutionError("Agent run organization_id mismatch.")
        if run.organization_id != organization_id:
            raise AgentExecutionError("Provided organization_id does not match run's organization.")

    async def execute(
        self,
        db: AsyncSession,
        *,
        run_id: str,
        organization_id: int,
    ) -> dict:

        existing_run = await self._get_run_unscoped(
            db,
            run_id,
        )

        if existing_run is None:
            raise AgentExecutionError(f"Agent run {run_id} was not found.")

        # Fail closed on tenant mismatch: the job's bound organization must be
        # the run's persisted organization. The unscoped worker lookup above is
        # the only global hop, and this check is its mandatory mitigation —
        # a queue job must never execute a run outside its own tenant.
        if existing_run.organization_id != organization_id:
            raise AgentExecutionError(
                "Agent run is not owned by the executing organization."
            )

        # -----------------------------------------
        # Already completed
        # -----------------------------------------

        if existing_run.status == "executed":
            ticket = await self._get_ticket(
                db,
                existing_run.ticket_id,
                organization_id,
            )

            return {
                "run_id": run_id,
                "ticket_id": (existing_run.ticket_id),
                "status": "executed",
                "action": (existing_run.action),
                "external_ticket_id": (ticket.external_id if ticket else None),
                "executed": True,
                "duplicate": True,
                "message": ("Agent run was already executed."),
            }

        # -----------------------------------------
        # Non-executable agent decisions
        # -----------------------------------------

        if existing_run.organization_id is None:
            raise AgentExecutionStateError(
                "Agent run is not owned by an organization and cannot execute."
            )

        if existing_run.action in {
            "human_review",
            "no_action",
        }:
            raise AgentExecutionStateError(
                f"Action '{existing_run.action}' does not require external execution."
            )

        # -----------------------------------------
        # Valid execution states
        # -----------------------------------------

        if existing_run.status not in {
            "approved",
            "execution_failed",
        }:
            raise AgentExecutionStateError(
                f"Agent run cannot be executed from status {existing_run.status}."
            )

        # -----------------------------------------
        # Retry previously failed execution
        # -----------------------------------------

        if existing_run.status == "execution_failed":
            existing_run.status = "approved"

            await db.commit()

        run = await AgentRunRepository.claim_for_execution_unscoped(
            db=db,
            run_id=run_id,
            organization_id=organization_id,
        )

        if run is None:
            raise AgentExecutionStateError(
                "Agent run could not be claimed for execution."
            )

        # -----------------------------------------
        # Load local ticket
        # -----------------------------------------

        ticket = await self._get_ticket(
            db,
            run.ticket_id,
            organization_id,
        )

        if ticket is None:
            await AgentRunRepository.mark_execution_failed(
                db,
                run,
                ("Local ticket was not found."),
            )
            record_agent_execution_failure(
                action=str(run.action),
            )
            raise AgentExecutionError("Local ticket was not found.")

        if not ticket.external_id:
            await AgentRunRepository.mark_execution_failed(
                db,
                run,
                ("Ticket has no Zendesk external_id."),
            )
            record_agent_execution_failure(
                action=str(run.action),
            )
            raise AgentExecutionError("Ticket is not linked to Zendesk.")

        zendesk_ticket_id = int(ticket.external_id)

        tool_plan = run.tool_plan or []

        try:
            ToolAuthorizationService.assert_executable(tool_plan)

        except ToolAuthorizationError as exc:
            await AgentRunRepository.mark_execution_failed(
                db,
                run,
                str(exc),
            )
            record_agent_execution_failure(
                action=str(run.action),
            )
            raise AgentExecutionStateError(str(exc)) from exc

        if not tool_plan:
            await AgentRunRepository.mark_execution_failed(
                db,
                run,
                ("Agent run contains no tool plan."),
            )
            record_agent_execution_failure(
                action=str(run.action),
            )
            raise AgentExecutionError("Agent run contains no executable tool plan.")

        # External writes are scoped to the tenant already persisted on the
        # run, which the database guarantees equals the ticket's organization
        # (composite FK ``fk_agent_runs_ticket_organization``). The run is the
        # direct tenant source for agent executions because it is the object
        # the job claims; the cross-checks below fail closed even if an
        # internal caller ever attempts a mismatch. Unowned runs can never
        # reach a Zendesk credential.
        resolved_organization_id = run.organization_id

        if resolved_organization_id is None:
            await AgentRunRepository.mark_execution_failed(
                db,
                run,
                ("Agent run is not owned by an organization."),
            )
            record_agent_execution_failure(
                action=str(run.action),
            )
            raise AgentExecutionError("Agent run is not owned by an organization.")

        if ticket.organization_id != resolved_organization_id:
            await AgentRunRepository.mark_execution_failed(
                db,
                run,
                ("Agent run and ticket tenant ownership mismatch."),
            )
            record_agent_execution_failure(
                action=str(run.action),
            )
            raise AgentExecutionError(
                "Agent run and ticket tenant ownership mismatch."
            )

        try:
            # -------------------------------------
            # Idempotency / crash recovery
            # -------------------------------------

            already_written = await self._already_written(
                db,
                zendesk_ticket_id=(zendesk_ticket_id),
                run_id=run_id,
                organization_id=organization_id,
            )

            if already_written:
                await AgentRunRepository.mark_executed(
                    db,
                    run,
                )

                await AgentRunRepository.add_event(
                    db=db,
                    agent_run_id=run.id,
                    event_type=("execution_recovered"),
                    actor="cxops-agent",
                    event_data={
                        "zendesk_ticket_id": (zendesk_ticket_id),
                        "run_id": run_id,
                    },
                )
                if run.reviewer_note == (
                    "Automatically approved by low-risk tool policy"
                ):
                    record_autonomous_execution(
                        action=str(run.action),
                        outcome="recovered",
                    )
                return {
                    "run_id": run_id,
                    "ticket_id": (ticket.id),
                    "status": "executed",
                    "action": run.action,
                    "external_ticket_id": (ticket.external_id),
                    "executed": True,
                    "duplicate": True,
                    "message": (
                        "Existing Zendesk execution detected and safely recovered."
                    ),
                }

            # -------------------------------------
            # Execute persisted tool plan
            # -------------------------------------

            # --- Preflight validation (Phase 1D.3) ---
            # Validate the entire persisted plan before any external side effect.
            # This prevents partial execution when a later tool is malformed/unknown.
            _ = await self._validate_plan_preflight(
                run,
                tool_plan,
                resolved_organization_id,
                zendesk_ticket_id,
                organization_id,
            )

            if not tool_plan:
                await AgentRunRepository.mark_execution_failed(
                    db,
                    run,
                    ("Agent run contains no tool plan."),
                )
                record_agent_execution_failure(
                    action=str(run.action),
                )
                raise AgentExecutionError("Agent run contains no executable tool plan.")

            for tool_call in tool_plan:
                await self._execute_tool(
                    db,
                    run=run,
                    ticket=ticket,
                    zendesk_ticket_id=(zendesk_ticket_id),
                    tool_call=tool_call,
                    organization_id=organization_id,
                )

            # -------------------------------------
            # Synchronize external state
            # -------------------------------------

            await ZendeskSyncService.sync_ticket_for_tenant(
                db,
                zendesk_ticket_id,
                organization_id=organization_id,
            )

            # -------------------------------------
            # Finish execution
            # -------------------------------------

            await AgentRunRepository.mark_executed(
                db,
                run,
            )

            await AgentRunRepository.add_event(
                db=db,
                agent_run_id=run.id,
                event_type="executed",
                actor="cxops-agent",
                event_data={
                    "zendesk_ticket_id": (zendesk_ticket_id),
                    "action": run.action,
                    "tool_count": len(tool_plan),
                    "tools": [tool.get("tool") for tool in tool_plan],
                },
            )
            if run.reviewer_note == ("Automatically approved by low-risk tool policy"):
                record_autonomous_execution(
                    action=str(run.action),
                    outcome="executed",
                )
            return {
                "run_id": run_id,
                "ticket_id": ticket.id,
                "status": "executed",
                "action": run.action,
                "external_ticket_id": (ticket.external_id),
                "executed": True,
                "duplicate": False,
                "message": ("Approved agent tool plan executed successfully."),
            }

        except Exception as exc:
            await db.rollback()

            fresh_run = await self._get_run_unscoped(
                db,
                run_id,
            )

            if (
                fresh_run is not None
                and fresh_run.organization_id == organization_id
                and fresh_run.status != "executed"
            ):
                await AgentRunRepository.mark_execution_failed(
                    db,
                    fresh_run,
                    str(exc),
                )

                await AgentRunRepository.add_event(
                    db=db,
                    agent_run_id=(fresh_run.id),
                    event_type=("execution_failed"),
                    actor="cxops-agent",
                    note=str(exc),
                    event_data={
                        "tool_plan": (fresh_run.tool_plan or []),
                    },
                )
                record_agent_execution_failure(
                    action=str(fresh_run.action),
                )

            if isinstance(
                exc,
                (
                    AgentExecutionError,
                    AgentExecutionStateError,
                ),
            ):
                raise

            raise AgentExecutionError(str(exc)) from exc


agent_execution_service = AgentExecutionService()
