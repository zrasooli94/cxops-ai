from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import (
    record_agent_approval,
)
from app.core.rbac import (
    AuthorizationContext,
    Capability,
    require_capability,
)
from app.models.agent_run import AgentRun
from app.repositories.agent_run_repository import (
    AgentRunRepository,
)
from app.services.tool_authorization_service import (
    ToolAuthorizationService,
)


class AgentRunNotFoundError(Exception):
    pass


class InvalidAgentRunStateError(Exception):
    pass


class AgentApprovalService:
    """Human reviewer approval/rejection for agent runs.

    ``approve`` and ``reject`` are human-only entrypoints. They are called
    exclusively from ``app/api/routes/agent.py`` (``/agent/runs/{run_id}/approve``
    and ``/agent/runs/{run_id}/reject``), which enforce
    ``RequireCapability(Capability.AGENT_APPROVE)``. No internal automation or
    machine workflow invokes these methods; the machine execution path uses
    ``IntegrationJobService.enqueue_agent_execution`` directly.
    """

    @staticmethod
    async def _get_pending_run(
        db: AsyncSession,
        run_id: str,
        organization_id: int,
    ) -> AgentRun:

        # Tenant-scoped first: a guessed run_id from another organization is
        # indistinguishable from a missing one (non-enumerating 404).
        run = await AgentRunRepository.get_by_run_id_for_tenant(
            db,
            run_id,
            organization_id,
        )

        if run is None:
            raise AgentRunNotFoundError(f"Agent run {run_id} was not found")

        if run.status != "pending_approval":
            raise InvalidAgentRunStateError(f"Agent run is already {run.status}")

        return run

    @staticmethod
    async def approve(
        db: AsyncSession,
        *,
        run_id: str,
        organization_id: int,
        note: str | None,
        authz: AuthorizationContext,
    ) -> AgentRun:

        require_capability(authz, Capability.AGENT_APPROVE)

        run = await AgentApprovalService._get_pending_run(
            db,
            run_id,
            organization_id,
        )

        if run.action == "human_review":
            run = await AgentRunRepository.mark_review_required(
                db,
                run,
                note,
            )

            await AgentRunRepository.add_event(
                db,
                agent_run_id=run.id,
                event_type="review_required",
                actor=authz.subject,
                note=note,
            )

            return run

        if run.action == "no_action":
            run = await AgentRunRepository.mark_no_action(
                db,
                run,
                note,
            )

            await AgentRunRepository.add_event(
                db,
                agent_run_id=run.id,
                event_type="no_action",
                actor=authz.subject,
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
            updated_tool = dict(tool)

            if updated_tool.get(
                "requires_approval",
                True,
            ):
                updated_tool["authorized"] = True

            updated_tool_plan.append(updated_tool)

        run.tool_plan = updated_tool_plan

        # The run's own organization is the trusted tenant for any external
        # side effect; a NULL-org legacy run can never reach approval (the
        # tenant-scoped lookup above refuses it), so this guard is defensive.
        org = run.organization_id
        if org is None:
            raise InvalidAgentRunStateError(
                "Cannot approve an unowned (NULL-org) agent run."
            )

        run.tool_policy_version = ToolAuthorizationService.TOOL_POLICY_VERSION
        run.authorization_source = "human_approval"
        run.authorized_by_subject = authz.subject
        run.authorized_at = datetime.now(timezone.utc)
        run.authorization_digest = ToolAuthorizationService.compute_run_digest(
            run_id=run.run_id,
            organization_id=org,
            ticket_id=run.ticket_id,
            policy_version=run.tool_policy_version,
            tool_plan=updated_tool_plan,
        )

        await AgentRunRepository.add_event(
            db,
            agent_run_id=run.id,
            event_type="approved",
            actor=authz.subject,
            note=note,
            event_data={
                "action": run.action,
                "recommended_team": (run.recommended_team),
                "recommended_priority": (run.recommended_priority),
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
        organization_id: int,
        note: str | None,
        authz: AuthorizationContext,
    ) -> AgentRun:

        require_capability(authz, Capability.AGENT_APPROVE)

        run = await AgentApprovalService._get_pending_run(
            db,
            run_id,
            organization_id,
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
            actor=authz.subject,
            note=note,
        )
        record_agent_approval(
            result="rejected",
        )
        return run
