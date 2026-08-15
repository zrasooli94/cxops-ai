from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ticket import Ticket
from app.repositories.ticket_repository import TicketRepository
from app.schemas.ticket import TicketCreate, TicketUpdate


class TicketService:

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
                str(data.requester_email)
                if data.requester_email
                else None
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

        return await TicketRepository.get_by_id(
            db=db,
            ticket_id=ticket_id,
        )

    @staticmethod
    async def list_tickets(
        db: AsyncSession,
        offset: int = 0,
        limit: int = 100,
    ) -> list[Ticket]:

        return await TicketRepository.list(
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

        ticket = await TicketRepository.get_by_id(
            db=db,
            ticket_id=ticket_id,
        )

        if ticket is None:
            return None

        changes = data.model_dump(
            exclude_unset=True,
        )

        if (
            "requester_email" in changes
            and changes["requester_email"] is not None
        ):
            changes["requester_email"] = str(
                changes["requester_email"]
            )

        return await TicketRepository.update(
            db=db,
            ticket=ticket,
            changes=changes,
        )