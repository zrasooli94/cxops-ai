
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business_action import BusinessAction


class BusinessActionRepository:
    """Persistence for ``BusinessAction`` rows (Phase 1P.2).

    Static methods, mirroring ``AgentRunRepository``. All lookups are
    tenant-scoped via ``organization_id``; callers resolve the trusted
    organization from the agent run's persisted parent, never from arguments.
    """

    @staticmethod
    async def find_by_dedupe(
        db: AsyncSession,
        *,
        organization_id: int,
        ticket_id: int,
        request_type: str,
        dedupe_key: str,
    ) -> BusinessAction | None:
        result = await db.execute(
            select(BusinessAction).where(
                BusinessAction.organization_id == organization_id,
                BusinessAction.ticket_id == ticket_id,
                BusinessAction.request_type == request_type,
                BusinessAction.dedupe_key == dedupe_key,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def find_most_recent_by_reference(
        db: AsyncSession,
        *,
        organization_id: int,
        request_type: str,
        reference_id: str,
    ) -> BusinessAction | None:
        """Most recent action of a request type carrying a reference id.

        Used to resolve a lead record (``a1.create_vehicle_lead``) from the
        reference id the A1 adapter minted, in strict tenant scope.
        """
        result = await db.execute(
            select(BusinessAction)
            .where(
                BusinessAction.organization_id == organization_id,
                BusinessAction.request_type == request_type,
                BusinessAction.reference_id == reference_id,
            )
            .order_by(BusinessAction.created_at.desc(), BusinessAction.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        organization_id: int,
        ticket_id: int,
        conversation_id: int | None,
        customer_id: int | None,
        run_id: str | None,
        request_type: str,
        status: str,
        dedupe_key: str | None,
        payload_json: dict,
    ) -> BusinessAction:
        action = BusinessAction(
            organization_id=organization_id,
            ticket_id=ticket_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
            run_id=run_id,
            request_type=request_type,
            status=status,
            dedupe_key=dedupe_key,
            payload_json=payload_json,
            result_json={},
        )
        db.add(action)
        await db.flush()
        return action

    @staticmethod
    async def update_result(
        db: AsyncSession,
        action: BusinessAction,
        *,
        status: str,
        reference_id: str | None,
        result_json: dict,
    ) -> BusinessAction:
        action.status = status
        action.reference_id = reference_id
        action.result_json = result_json
        await db.flush()
        return action

    @staticmethod
    async def update_payload(
        db: AsyncSession,
        action: BusinessAction,
        *,
        payload_json: dict,
    ) -> BusinessAction:
        action.payload_json = payload_json
        await db.flush()
        return action

    @staticmethod
    async def get_by_id_for_tenant(
        db: AsyncSession,
        action_id: int,
        organization_id: int,
    ) -> BusinessAction | None:
        result = await db.execute(
            select(BusinessAction).where(
                BusinessAction.id == action_id,
                BusinessAction.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_for_conversation_for_tenant(
        db: AsyncSession,
        *,
        conversation_id: int,
        organization_id: int,
        limit: int = 100,
    ) -> list[BusinessAction]:
        result = await db.execute(
            select(BusinessAction)
            .where(
                BusinessAction.conversation_id == conversation_id,
                BusinessAction.organization_id == organization_id,
            )
            .order_by(BusinessAction.created_at.desc(), BusinessAction.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def list_for_ticket_for_tenant(
        db: AsyncSession,
        *,
        ticket_id: int,
        organization_id: int,
        limit: int = 100,
    ) -> list[BusinessAction]:
        result = await db.execute(
            select(BusinessAction)
            .where(
                BusinessAction.ticket_id == ticket_id,
                BusinessAction.organization_id == organization_id,
            )
            .order_by(BusinessAction.created_at.desc(), BusinessAction.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def count_for_ticket_for_tenant(
        db: AsyncSession,
        *,
        ticket_id: int,
        organization_id: int,
    ) -> int:
        result = await db.execute(
            select(func.count())
            .select_from(BusinessAction)
            .where(
                BusinessAction.ticket_id == ticket_id,
                BusinessAction.organization_id == organization_id,
            )
        )
        return int(result.scalar_one())