from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ticket import Ticket
from app.repositories.customer_repository import CustomerRepository
from app.repositories.ticket_repository import TicketRepository
from app.schemas.ticket import TicketCreate, TicketUpdate


class TicketService:
    @staticmethod
    async def create_ticket_for_tenant(
        db: AsyncSession,
        data: TicketCreate,
        organization_id: int,
    ) -> Ticket:

        # Validate customer belongs to same tenant if customer_id provided
        if data.customer_id is not None:
            customer = await CustomerRepository.get_by_id_for_tenant(
                db, data.customer_id, organization_id
            )
            if customer is None:
                raise ValueError("Customer not found in this organization")

        ticket = Ticket(
            external_id=data.external_id,
            subject=data.subject,
            description=data.description,
            requester_email=(
                str(data.requester_email) if data.requester_email else None
            ),
            priority=data.priority,
            source=data.source,
            customer_id=data.customer_id,
            organization_id=organization_id,
        )

        return await TicketRepository.create(
            db=db,
            ticket=ticket,
        )

    @staticmethod
    async def get_ticket_for_tenant(
        db: AsyncSession,
        ticket_id: int,
        organization_id: int,
    ) -> Ticket | None:

        return await TicketRepository.get_by_id_for_tenant(
            db=db,
            ticket_id=ticket_id,
            organization_id=organization_id,
        )

    @staticmethod
    async def list_tickets_for_tenant(
        db: AsyncSession,
        organization_id: int,
        offset: int = 0,
        limit: int = 100,
    ) -> list[Ticket]:

        return await TicketRepository.list_for_tenant(
            db=db,
            organization_id=organization_id,
            offset=offset,
            limit=limit,
        )

    @staticmethod
    async def update_ticket_for_tenant(
        db: AsyncSession,
        ticket_id: int,
        data: TicketUpdate,
        organization_id: int,
    ) -> Ticket | None:

        ticket = await TicketRepository.get_by_id_for_tenant(
            db=db,
            ticket_id=ticket_id,
            organization_id=organization_id,
        )

        if ticket is None:
            return None

        changes = data.model_dump(
            exclude_unset=True,
        )

        if "requester_email" in changes and changes["requester_email"] is not None:
            changes["requester_email"] = str(changes["requester_email"])

        # Validate customer belongs to same tenant if customer_id is being changed.
        # An explicit `customer_id: null` is an intentional unlink request: the
        # ticket keeps its CurrentTenant organization_id and simply drops the
        # customer link. No cross-tenant side effect is possible because the
        # non-null branch is tenant-scoped and the ticket itself was already
        # fetched through the tenant-scoped get_by_id_for_tenant above.
        if "customer_id" in changes and changes["customer_id"] is not None:
            from app.repositories.customer_repository import CustomerRepository
            customer = await CustomerRepository.get_by_id_for_tenant(
                db, changes["customer_id"], organization_id
            )
            if customer is None:
                return None  # Customer not found in this tenant

        return await TicketRepository.update_for_tenant(
            db=db,
            ticket=ticket,
            changes=changes,
            organization_id=organization_id,
        )

    # Internal/global methods (kept for webhook/internal consumers)
    @staticmethod
    async def create_ticket(
        db: AsyncSession,
        data: TicketCreate,
    ) -> Ticket:

        ticket = Ticket(
            external_id=data.external_id,
            subject=data.subject,
            description=data.description,
            requester_email=(
                str(data.requester_email) if data.requester_email else None
            ),
            priority=data.priority,
            source=data.source,
            customer_id=data.customer_id,
        )

        return await TicketRepository.create(
            db=db,
            ticket=ticket,
        )

    @staticmethod
    async def get_ticket(
        db: AsyncSession,
        ticket_id: int,
    ) -> Ticket | None:

        return await TicketRepository.get_by_id_unscoped(
            db=db,
            ticket_id=ticket_id,
        )

    @staticmethod
    async def list_tickets(
        db: AsyncSession,
        offset: int = 0,
        limit: int = 100,
    ) -> list[Ticket]:

        return await TicketRepository.list_unscoped(
            db=db,
            offset=offset,
            limit=limit,
        )

    @staticmethod
    async def update_ticket(
        db: AsyncSession,
        ticket_id: int,
        data: TicketUpdate,
    ) -> Ticket | None:

        ticket = await TicketRepository.get_by_id_unscoped(
            db=db,
            ticket_id=ticket_id,
        )

        if ticket is None:
            return None

        changes = data.model_dump(
            exclude_unset=True,
        )

        if "requester_email" in changes and changes["requester_email"] is not None:
            changes["requester_email"] = str(changes["requester_email"])

        return await TicketRepository.update_unscoped(
            db=db,
            ticket=ticket,
            changes=changes,
        )
