from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_action_event import (
    AgentActionEvent,
)
from app.models.agent_run import AgentRun
from app.models.ticket import Ticket
from app.services.tool_authorization_service import (
    ToolAuthorizationService,
)


class AgentRunRepository:
    @staticmethod
    def _assert_tenant_owned(run: AgentRun) -> None:
        """Refuse state transitions on an unowned (NULL-org) run.

        A NULL-org legacy run is inert: no tenant-facing path may ever approve,
        reject, or execute it, even if it is already loaded by some internal
        caller. Fail closed rather than transition a run without ownership.
        """
        if run.organization_id is None:
            raise ValueError("Cannot mutate an unowned (NULL-org) agent run.")

    @staticmethod
    async def find_current_run_by_fingerprint(
        db: AsyncSession,
        *,
        ticket_id: int,
        organization_id: int,
        fingerprint: str,
    ) -> AgentRun | None:
        """Return the newest non-superseded run matching the fingerprint.

        A matching fingerprint means the materially relevant inputs have not
        changed, so the existing analysis can be reused without a new LLM call.
        Superseded and rejected runs are ignored; they are audit history, not
        current decisions.
        """
        result = await db.execute(
            select(AgentRun)
            .where(
                AgentRun.ticket_id == ticket_id,
                AgentRun.organization_id == organization_id,
                AgentRun.fingerprint == fingerprint,
                AgentRun.status.notin_({"superseded", "rejected"}),
            )
            .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        run_id: str,
        ticket_id: int,
        organization_id: int,
        decision: dict,
        sources: list[dict],
        workflow_path: list[str],
        tool_plan: list[dict],
        fingerprint: str | None = None,
    ) -> AgentRun:
        # Serialize AgentRun persistence per ticket so two
        # simultaneous analyses cannot leave two active approvals.
        await db.execute(
            select(Ticket.id).where(Ticket.id == ticket_id).with_for_update()
        )

        # Preserve previous decisions for audit history, but remove
        # them from the active approval queue.
        result = await db.execute(
            select(AgentRun)
            .where(
                AgentRun.ticket_id == ticket_id,
                AgentRun.organization_id == organization_id,
                AgentRun.status == "pending_approval",
            )
            .with_for_update()
        )

        previous_runs = list(result.scalars().all())

        for previous_run in previous_runs:
            previous_run.status = "superseded"

            db.add(
                AgentActionEvent(
                    agent_run_id=previous_run.id,
                    event_type="superseded",
                    actor="cxops-policy",
                    note="Superseded by a newer analysis of the same ticket.",
                    event_data={
                        "replacement_run_id": run_id,
                        "ticket_id": ticket_id,
                    },
                )
            )

        run = AgentRun(
            run_id=run_id,
            ticket_id=ticket_id,
            organization_id=organization_id,
            action=decision["action"],
            reason=decision["reason"],
            recommended_team=decision.get("recommended_team"),
            recommended_priority=decision.get("recommended_priority"),
            response_draft=decision.get("response_draft"),
            requires_human_approval=ToolAuthorizationService.requires_human_approval(
                tool_plan or []
            ),
            status="pending_approval",
            sources=sources,
            workflow_path=workflow_path,
            tool_plan=tool_plan,
            fingerprint=fingerprint,
        )

        db.add(run)

        await db.commit()
        await db.refresh(run)

        return run

    @staticmethod
    async def get_by_run_id_for_tenant(
        db: AsyncSession,
        run_id: str,
        organization_id: int,
    ) -> AgentRun | None:
        """Tenant-scoped run lookup: a guessed run_id from another
        organization never resolves, so foreign runs are indistinguishable
        from missing runs (non-enumerating 404)."""

        result = await db.execute(
            select(AgentRun).where(
                AgentRun.run_id == run_id,
                AgentRun.organization_id == organization_id,
            )
        )

        return result.scalar_one_or_none()

    @staticmethod
    async def get_pending_run_for_tenant(
        db: AsyncSession,
        run_id: str,
        organization_id: int,
    ) -> AgentRun | None:
        """Tenant-scoped pending-approval lookup."""

        result = await db.execute(
            select(AgentRun).where(
                AgentRun.run_id == run_id,
                AgentRun.organization_id == organization_id,
                AgentRun.status == "pending_approval",
            )
        )

        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_run_id_unscoped(
        db: AsyncSession,
        run_id: str,
    ) -> AgentRun | None:
        """INTERNAL-ONLY unscoped run lookup.

        This binds run_id without an organization and MUST NOT be used by any
        human / JWT-authenticated / public route. It exists solely for the
        durable job worker, which resolves a run from the agent-execution queue
        outside any request tenant context and then re-validates the resolved
        organization on the run itself.
        """

        result = await db.execute(select(AgentRun).where(AgentRun.run_id == run_id))

        return result.scalar_one_or_none()

    @staticmethod
    async def list_runs_for_tenant(
        db: AsyncSession,
        *,
        organization_id: int,
        run_status: str | None = None,
        limit: int = 100,
    ) -> list[AgentRun]:
        statement = (
            select(AgentRun)
            .where(AgentRun.organization_id == organization_id)
            .order_by(AgentRun.created_at.desc())
            .limit(limit)
        )

        if run_status is not None:
            statement = statement.where(AgentRun.status == run_status)

        result = await db.execute(statement)

        return list(result.scalars().all())

    # Tenant-safe customer-scoped reads (customer 360). Runs are joined to their
    # tenant-owned ticket so a foreign customer id can never surface another
    # tenant's run history.

    @staticmethod
    async def list_for_customer_for_tenant(
        db: AsyncSession,
        *,
        customer_id: int,
        organization_id: int,
        offset: int,
        limit: int,
    ) -> list[AgentRun]:
        result = await db.execute(
            select(AgentRun)
            .join(Ticket, AgentRun.ticket_id == Ticket.id)
            .where(
                Ticket.customer_id == customer_id,
                AgentRun.organization_id == organization_id,
            )
            .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
            .offset(offset)
            .limit(limit)
        )

        return list(result.scalars().all())

    @staticmethod
    async def count_for_customer_for_tenant(
        db: AsyncSession,
        *,
        customer_id: int,
        organization_id: int,
    ) -> int:
        result = await db.execute(
            select(func.count())
            .select_from(AgentRun)
            .join(Ticket, AgentRun.ticket_id == Ticket.id)
            .where(
                Ticket.customer_id == customer_id,
                AgentRun.organization_id == organization_id,
            )
        )

        return int(result.scalar_one())

    @staticmethod
    async def approve(
        db: AsyncSession,
        run: AgentRun,
        note: str | None,
    ) -> AgentRun:

        AgentRunRepository._assert_tenant_owned(run)

        run.status = "approved"
        run.reviewer_note = note
        run.reviewed_at = datetime.now(timezone.utc)

        await db.commit()
        await db.refresh(run)

        return run

    @staticmethod
    async def reject(
        db: AsyncSession,
        run: AgentRun,
        note: str | None,
    ) -> AgentRun:

        AgentRunRepository._assert_tenant_owned(run)

        run.status = "rejected"
        run.reviewer_note = note
        run.reviewed_at = datetime.now(timezone.utc)

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
    async def claim_for_execution_unscoped(
        db: AsyncSession,
        run_id: str,
        organization_id: int,
    ) -> AgentRun | None:
        """INTERNAL-ONLY execution claim, bound to the resolved organization.

        Idempotently transitions an approved run to ``executing``. Unlike the
        lookup, the claim is co-bound with ``organization_id`` so a queue job
        can never claim a run outside the organization the job was created for.
        Called exclusively by the durable job worker; no route caller exists.
        """

        result = await db.execute(
            update(AgentRun)
            .where(
                AgentRun.run_id == run_id,
                AgentRun.organization_id == organization_id,
                AgentRun.status == "approved",
            )
            .values(status="executing")
            .returning(AgentRun.id)
        )

        claimed_id: int | None = result.scalar_one_or_none()

        await db.commit()

        if claimed_id is None:
            return None

        run_result = await db.execute(select(AgentRun).where(AgentRun.id == claimed_id))

        return run_result.scalar_one_or_none()

    @staticmethod
    async def mark_executed(
        db: AsyncSession,
        run: AgentRun,
    ) -> AgentRun:

        AgentRunRepository._assert_tenant_owned(run)

        run.status = "executed"

        run.executed_at = datetime.now(timezone.utc)

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

        AgentRunRepository._assert_tenant_owned(run)

        run.status = "execution_failed"

        run.error_message = error_message[:4000]

        await db.commit()
        await db.refresh(run)

        return run

    @staticmethod
    async def mark_review_required(
        db: AsyncSession,
        run: AgentRun,
        note: str | None,
    ) -> AgentRun:

        AgentRunRepository._assert_tenant_owned(run)

        run.status = "review_required"
        # Do NOT populate reviewer_note or reviewed_at for a new review_required
        # run. The agent explanation belongs in ``reason``; human review metadata
        # is only set when an authorized reviewer explicitly completes the case.
        run.reviewer_note = None
        run.reviewed_at = None

        await db.commit()
        await db.refresh(run)

        return run

    @staticmethod
    async def mark_reviewed(
        db: AsyncSession,
        run: AgentRun,
        note: str | None,
    ) -> AgentRun:

        AgentRunRepository._assert_tenant_owned(run)

        run.status = "reviewed"
        run.reviewer_note = note
        run.reviewed_at = datetime.now(timezone.utc)

        await db.commit()
        await db.refresh(run)

        return run

    @staticmethod
    async def mark_no_action(
        db: AsyncSession,
        run: AgentRun,
        note: str | None,
    ) -> AgentRun:

        AgentRunRepository._assert_tenant_owned(run)

        run.status = "no_action"
        run.reviewer_note = note

        run.reviewed_at = datetime.now(timezone.utc)

        await db.commit()
        await db.refresh(run)

        return run

    @staticmethod
    async def mark_auto_approved(
        db: AsyncSession,
        run: AgentRun,
    ) -> AgentRun:

        AgentRunRepository._assert_tenant_owned(run)

        run.status = "approved"

        run.reviewer_note = "Automatically approved by low-risk tool policy"

        run.reviewed_at = datetime.now(timezone.utc)

        await db.commit()
        await db.refresh(run)

        return run

    @staticmethod
    async def list_events(
        db: AsyncSession,
        *,
        agent_run_id: int,
    ) -> list[AgentActionEvent]:
        """List the event stream for an already-resolved run id.

        Identity alone carries no tenant signal, so callers MUST resolve the
        run object through a tenant-scoped lookup first (see the routes). The
        run's own ``organization_id`` is the guard that foreign ids never
        reach this method.
        """

        result = await db.execute(
            select(AgentActionEvent)
            .where(AgentActionEvent.agent_run_id == agent_run_id)
            .order_by(AgentActionEvent.id.asc())
        )

        return list(result.scalars().all())