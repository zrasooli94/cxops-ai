from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.zendesk.client import zendesk_client
from app.models.ticket import Ticket
from app.repositories.ticket_repository import (
    TicketRepository,
)
from app.services.customer_identity_service import (
    CustomerIdentityConflictError,
    CustomerIdentityService,
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
        - customer lookup uses the trusted Zendesk requester identity first
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

            try:
                customer, _identity = (
                    await CustomerIdentityService.resolve_or_create_customer_by_provider_identity(
                        db,
                        organization_id=organization_id,
                        provider="zendesk",
                        identity_type="user_id",
                        identifier=str(requester_id),
                        display_name=requester_name,
                        email=requester_email,
                    )
                )

                # Apply any safe email change now that the authoritative
                # customer is known through the stable provider identity.
                customer = await CustomerIdentityService.safe_update_customer_email(
                    db,
                    customer=customer,
                    new_email=requester_email,
                    organization_id=organization_id,
                )

                customer_id = customer.id

            except CustomerIdentityConflictError as exc:
                await db.rollback()
                raise ZendeskSyncConflictError(
                    "Zendesk requester identity conflicts with existing customer data"
                ) from exc

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
            # The Zendesk id is already taken by another tenant's row. Do not
            # reach into that row; fail closed.
            await db.rollback()
            raise ZendeskSyncConflictError(
                "Zendesk ticket is already linked to an existing record"
            ) from None
