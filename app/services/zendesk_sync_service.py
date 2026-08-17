from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.zendesk.client import zendesk_client
from app.models.customer import Customer
from app.models.ticket import Ticket
from app.repositories.customer_repository import (
    CustomerRepository,
)
from app.repositories.ticket_repository import (
    TicketRepository,
)


class ZendeskSyncService:

    @staticmethod
    async def sync_ticket(
        db: AsyncSession,
        zendesk_ticket_id: int,
    ) -> Ticket:

        response = await zendesk_client.get_ticket(
            db=db,
            ticket_id=zendesk_ticket_id,
        )

        zendesk_ticket = response["ticket"]

        requester_email = None
        requester_name = None
        customer_id = None

        requester_id = zendesk_ticket.get(
            "requester_id"
        )

        if requester_id:
            user_response = await zendesk_client.get_user(
                db=db,
                user_id=requester_id,
            )

            zendesk_user = user_response["user"]

            requester_email = zendesk_user.get(
                "email"
            )

            requester_name = (
                zendesk_user.get("name")
                or requester_email
                or "Zendesk Customer"
            )

            if requester_email:
                customer = (
                    await CustomerRepository.get_by_email(
                        db=db,
                        email=requester_email,
                    )
                )

                if customer is None:
                    customer = Customer(
                        external_id=str(requester_id),
                        name=requester_name,
                        email=requester_email,
                    )

                    customer = (
                        await CustomerRepository.create(
                            db=db,
                            customer=customer,
                        )
                    )

                customer_id = customer.id

        external_id = str(
            zendesk_ticket["id"]
        )

        existing = (
            await TicketRepository.get_by_external_id(
                db=db,
                external_id=external_id,
            )
        )

        description = (
            zendesk_ticket.get("description")
            or zendesk_ticket.get("subject")
            or "No description"
        )

        priority = (
            zendesk_ticket.get("priority")
            or "normal"
        )

        status = (
            zendesk_ticket.get("status")
            or "new"
        )

        if existing:

            changes = {
                "subject": zendesk_ticket["subject"],
                "description": description,
                "status": status,
                "priority": priority,
                "requester_email": requester_email,
                "customer_id": customer_id,
                "source": "zendesk",
            }

            return await TicketRepository.update(
                db=db,
                ticket=existing,
                changes=changes,
            )

        ticket = Ticket(
            external_id=external_id,
            subject=zendesk_ticket["subject"],
            description=description,
            status=status,
            priority=priority,
            requester_email=requester_email,
            customer_id=customer_id,
            source="zendesk",
        )

        return await TicketRepository.create(
            db=db,
            ticket=ticket,
        )