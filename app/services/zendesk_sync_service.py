from sqlalchemy.exc import IntegrityError
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


class ZendeskSyncConflictError(RuntimeError):
    """A Zendesk sync collided with data already owned by another tenant.

    Raised instead of reaching another organization's rows. Never carries an
    organization identifier in the message.
    """


class ZendeskSyncService:
    @staticmethod
    async def sync_ticket_for_tenant(
        db: AsyncSession,
        zendesk_ticket_id: int,
        organization_id: int,
    ) -> Ticket:
        """Sync a Zendesk ticket into CXOps rows owned by ``organization_id``.

        This is the ONLY Zendesk ingestion path. The credential resolution and
        every CXOps read/write are scoped to ``organization_id``:
        - customer lookup/creation is tenant-scoped (org + email)
        - ticket lookup by external_id is tenant-scoped (org + external_id)
        - the ticket row is always created with ``organization_id``

        There is intentionally NO unscoped/global sync variant: "internal" webhook
        ingestion resolves its organization from the trusted connection before
        calling this method.
        """

        response = await zendesk_client.get_ticket(
            db=db,
            ticket_id=zendesk_ticket_id,
            organization_id=organization_id,
        )

        zendesk_ticket = response["ticket"]

        requester_email = None
        requester_name = None
        customer_id = None

        requester_id = zendesk_ticket.get("requester_id")

        if requester_id:
            user_response = await zendesk_client.get_user(
                db=db,
                user_id=requester_id,
                organization_id=organization_id,
            )

            zendesk_user = user_response["user"]

            requester_email = zendesk_user.get("email")

            requester_name = (
                zendesk_user.get("name") or requester_email or "Zendesk Customer"
            )

            if requester_email:
                # Resolve the customer within the current tenant only. A match
                # in another organization must never be attached to this tenant.
                customer = await CustomerRepository.get_by_email_for_tenant(
                    db=db,
                    email=requester_email,
                    organization_id=organization_id,
                )

                if customer is None:
                    try:
                        customer = Customer(
                            external_id=str(requester_id),
                            name=requester_name,
                            email=requester_email,
                            organization_id=organization_id,
                        )

                        customer = await CustomerRepository.create(
                            db=db,
                            customer=customer,
                        )
                    except IntegrityError:
                        # The requester email is already owned by another
                        # tenant's customer. Do not reach into that row.
                        await db.rollback()
                        raise ZendeskSyncConflictError(
                            "Zendesk ticket is already linked to an existing record"
                        ) from None

                customer_id = customer.id

        external_id = str(zendesk_ticket["id"])

        # Scope membership by tenant: a Zendesk ticket id owned by another
        # organization must never be retrieved or updated through tenant sync.
        existing = await TicketRepository.get_by_external_id_for_tenant(
            db=db,
            external_id=external_id,
            organization_id=organization_id,
        )

        description = (
            zendesk_ticket.get("description")
            or zendesk_ticket.get("subject")
            or "No description"
        )

        priority = zendesk_ticket.get("priority") or "normal"

        status = zendesk_ticket.get("status") or "new"

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

            return await TicketRepository.update_for_tenant(
                db=db,
                ticket=existing,
                changes=changes,
                organization_id=organization_id,
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
            organization_id=organization_id,
        )

        try:
            return await TicketRepository.create(
                db=db,
                ticket=ticket,
            )
        except IntegrityError:
            # The Zendesk id (or requester email) is already taken by another
            # tenant's row. Do not reach into that row; fail closed.
            await db.rollback()
            raise ZendeskSyncConflictError(
                "Zendesk ticket is already linked to an existing record"
            ) from None
