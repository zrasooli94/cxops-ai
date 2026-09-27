"""Durable business-tool execution (Phase 1P.2).

``BusinessActionService.execute`` runs a persisted tool plan's business tools
against local persistence through the registered executor, recording each call
as a ``BusinessAction`` row. It is idempotent by ``(organization_id, ticket_id,
request_type, dedupe_key)`` where the dedupe key derives from the agent run, so
a recovered job converges to existing rows instead of re-executing side
effects. Every customer-visible result is mirrored as an outbound message in
the local provider conversation (never a direct Zendesk write).

The trusted context (organization, ticket, conversation, customer) is always
derived from the run's persisted ownership — never from tool arguments.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.metrics import record_agent_tool_execution
from app.models.agent_run import AgentRun
from app.models.ticket import Ticket
from app.repositories.business_action_repository import BusinessActionRepository
from app.repositories.conversation_repository import ConversationRepository
from app.services.business_integration_service import BusinessIntegrationService
from app.services.conversation_ingestion_service import (
    LOCAL_PROVIDER,
    ConversationIngestionService,
)
from app.services.tool_authorization_service import ToolAuthorizationService
from app.tools.base import (
    ACTION_STATUS_FAILED,
    ACTION_STATUS_RECEIVED,
    CUSTOMER_VISIBLE_ACTION_STATUSES,
    BusinessToolContext,
    BusinessToolValidationError,
    ToolExecutionResult,
)
from app.tools.registry import business_tool_registry

log = get_logger(__name__)


class BusinessActionError(Exception):
    pass


class BusinessActionPlanError(BusinessActionError):
    pass


class BusinessActionService:
    @staticmethod
    def _dedupe_key(run_id: str, tool_name: str) -> str:
        return f"agent_run:{run_id}:{tool_name}"

    @staticmethod
    def _result_json(result: ToolExecutionResult) -> dict:
        return {
            "summary": result.summary,
            "customer_message": result.customer_message,
            "metadata": result.metadata or {},
        }

    @classmethod
    async def assert_plan_executable(
        cls,
        db: AsyncSession,
        *,
        run: AgentRun,
        tool_plan: list[dict],
    ) -> None:
        """Fail closed: every executable tool must be policy-backed business.

        A business-only execution path must never carry a Zendesk tool
        (which would bypass the external target validation) or an unknown
        tool name.
        """
        works = [
            tool
            for tool in (tool_plan or [])
            if tool.get("tool", "") not in {"none", "human.review"}
        ]
        if not works:
            return

        # Execution re-checks tenant enablement from the DB; it never trusts a
        # plan that may reference a since-disabled provider.
        if run.organization_id is None:
            raise BusinessActionPlanError("Agent run has no tenant.")
        enabled = set(
            await BusinessIntegrationService.enabled_tool_names(
                db,
                run.organization_id,
            )
        )

        for tool in works:
            name = tool.get("tool", "")
            policy = ToolAuthorizationService.POLICIES.get(name)
            if policy is None:
                raise BusinessActionPlanError(f"Unrecognized tool in plan: {name}")
            if not bool(policy.get("business_tool", False)):
                raise BusinessActionPlanError(
                    f"Non-business tool in business plan: {name}"
                )
            if business_tool_registry.get(name) is None:
                raise BusinessActionPlanError(
                    f"Business tool '{name}' has no registered definition."
                )
            if name not in enabled:
                raise BusinessActionPlanError(
                    f"Business tool '{name}' is not enabled for this tenant."
                )

    @classmethod
    async def _mirror(
        cls,
        db: AsyncSession,
        *,
        run: AgentRun,
        ticket: Ticket,
        organization_id: int,
        tool_name: str,
        body: str,
    ) -> None:
        """Mirror a customer-visible business result into the local conversation.

        Idempotent by ``agent_run:<run_id>:<tool_name>``; absorbed on failure so
        the durable BusinessAction row remains the source of truth and the
        widget can still render it from there.
        """
        if not body:
            return
        try:
            await ConversationIngestionService.ensure_agent_reply_message(
                db,
                ticket=ticket,
                run_id=run.run_id,
                organization_id=organization_id,
                dedupe_suffix=tool_name,
                direction="outbound",
                visibility="public",
                body=body,
                provider=LOCAL_PROVIDER,
            )
        except Exception:
            log.exception(
                "business_action_mirror_failed",
                run_id=run.run_id,
                tool=tool_name,
                organization_id=organization_id,
            )

    @classmethod
    async def execute(
        cls,
        db: AsyncSession,
        *,
        run: AgentRun,
        ticket: Ticket,
        organization_id: int,
        tool_plan: list[dict],
    ) -> None:
        """Execute business tools, persist BusinessActions, mirror results.

        Fail-closed guarantees:
        - every executable tool is a registered business tool (above);
        - the tenant derives from the run/ticket, never from arguments;
        - the dedupe key is run-bound, so recovery cannot re-execute a lead
          creation, photo request, or pickup scheduling.
        """
        await cls.assert_plan_executable(db, run=run, tool_plan=tool_plan)

        conversation = await ConversationRepository.get_by_ticket_for_tenant(
            db,
            ticket_id=ticket.id,
            organization_id=organization_id,
        )
        conversation_id = conversation.id if conversation is not None else None

        for tool in tool_plan:
            name = tool.get("tool", "")
            if name in {"none", "human.review"}:
                continue

            definition = business_tool_registry.require(name)
            raw_arguments = tool.get("arguments") or {}
            try:
                arguments = definition.validate_arguments(raw_arguments)
            except BusinessToolValidationError as exc:
                raise BusinessActionPlanError(str(exc)) from exc

            dedupe_key = cls._dedupe_key(run.run_id, name)

            existing = await BusinessActionRepository.find_by_dedupe(
                db,
                organization_id=organization_id,
                ticket_id=ticket.id,
                request_type=name,
                dedupe_key=dedupe_key,
            )
            if existing is not None:
                await cls._reconcile_existing(
                    db,
                    run=run,
                    ticket=ticket,
                    organization_id=organization_id,
                    action=existing,
                )
                continue

            action = await BusinessActionRepository.create(
                db,
                organization_id=organization_id,
                ticket_id=ticket.id,
                conversation_id=conversation_id,
                customer_id=ticket.customer_id,
                run_id=run.run_id,
                request_type=name,
                status=ACTION_STATUS_RECEIVED,
                dedupe_key=dedupe_key,
                payload_json=arguments,
            )

            context = BusinessToolContext(
                organization_id=organization_id,
                ticket_id=ticket.id,
                conversation_id=conversation_id,
                customer_id=ticket.customer_id,
                run_id=run.run_id,
            )

            try:
                result = await definition.executor.execute(
                    db,
                    context=context,
                    arguments=arguments,
                )
            except Exception as exc:
                failure = ToolExecutionResult(
                    status=ACTION_STATUS_FAILED,
                    summary=str(exc),
                    customer_visible=False,
                    customer_message=None,
                )
                await BusinessActionRepository.update_result(
                    db,
                    action,
                    status=ACTION_STATUS_FAILED,
                    reference_id=None,
                    result_json=cls._result_json(failure),
                )
                raise

            if result.status not in CUSTOMER_VISIBLE_ACTION_STATUSES:
                raise BusinessActionError(
                    f"Executor returned non-customer-visible status '{result.status}'."
                )

            await BusinessActionRepository.update_result(
                db,
                action,
                status=result.status,
                reference_id=result.reference_id,
                result_json=cls._result_json(result),
            )
            record_agent_tool_execution(tool=name)

            if (
                result.customer_visible
                and result.status != ACTION_STATUS_FAILED
            ):
                await cls._mirror(
                    db,
                    run=run,
                    ticket=ticket,
                    organization_id=organization_id,
                    tool_name=name,
                    body=result.customer_message or "",
                )

    @classmethod
    async def _reconcile_existing(
        cls,
        db: AsyncSession,
        *,
        run: AgentRun,
        ticket: Ticket,
        organization_id: int,
        action,
    ) -> None:
        """A recovered run converged on an existing BusinessAction.

        Re-mirror the recorded customer message (idempotent mirror, so the
        local conversation converges too).
        """
        customer_message = (action.result_json or {}).get("customer_message")
        if action.status not in {ACTION_STATUS_FAILED, ACTION_STATUS_RECEIVED}:
            await cls._mirror(
                db,
                run=run,
                ticket=ticket,
                organization_id=organization_id,
                tool_name=action.request_type,
                body=customer_message or "",
            )


business_action_service = BusinessActionService()