from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer
from app.repositories.customer_repository import CustomerRepository
from app.repositories.ticket_repository import TicketRepository
from app.schemas.customer import CustomerSummary, CustomerTicketSummary


class Customer360Service:
    """Service contexts for the customer 360 view: linked tickets and summary.

    Everything is tenant-scoped; ``organization_id`` comes from the resolved
    CurrentTenant and is enforced in every SQL predicate.
    """

    @staticmethod
    async def get_for_tenant(
        db: AsyncSession,
        customer_id: int,
        organization_id: int,
    ) -> Customer | None:
        return await CustomerRepository.get_by_id_for_tenant(
            db, customer_id, organization_id
        )

    @staticmethod
    async def list_tickets_for_customer(
        db: AsyncSession,
        *,
        customer_id: int,
        organization_id: int,
        offset: int,
        limit: int,
    ) -> tuple[list[CustomerTicketSummary], int]:
        tickets = await TicketRepository.list_for_customer_for_tenant(
            db,
            customer_id=customer_id,
            organization_id=organization_id,
            offset=offset,
            limit=limit,
        )
        total = await TicketRepository.count_for_customer_for_tenant(
            db,
            customer_id=customer_id,
            organization_id=organization_id,
        )

        items = [
            CustomerTicketSummary.model_validate(ticket) for ticket in tickets
        ]
        return items, total

    @staticmethod
    async def get_summary(
        db: AsyncSession,
        *,
        customer_id: int,
        organization_id: int,
    ) -> CustomerSummary:
        metrics = await TicketRepository.summarize_for_customer(
            db,
            customer_id=customer_id,
            organization_id=organization_id,
        )
        most_recent = await TicketRepository.most_recent_ticket_for_customer(
            db,
            customer_id=customer_id,
            organization_id=organization_id,
        )
        common_category = await TicketRepository.common_category_for_customer(
            db,
            customer_id=customer_id,
            organization_id=organization_id,
        )

        return CustomerSummary(
            customer_id=customer_id,
            total_tickets=metrics["total"],
            open_tickets=metrics["open"],
            closed_or_resolved_tickets=metrics["resolved"],
            latest_ticket_at=metrics["latest_ticket_at"],
            latest_interaction_at=metrics["latest_interaction_at"],
            most_recent_ticket=(
                CustomerTicketSummary.model_validate(most_recent)
                if most_recent is not None
                else None
            ),
            common_category=common_category,
        )