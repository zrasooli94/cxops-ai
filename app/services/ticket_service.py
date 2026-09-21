from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ticket import Ticket
from app.repositories.customer_repository import CustomerRepository
from app.repositories.ticket_repository import TicketRepository
from app.schemas.ticket import TicketCreate, TicketUpdate
from app.services.conversation_ingestion_service import (
    ConversationIngestionService,
)
from app.services.customer_service import CustomerService


class TicketService:
    @staticmethod
    async def create_ticket_for_tenant(
        db: AsyncSession,
        data: TicketCreate,
        organization_id: int,
    ) -> Ticket:

        customer_id = data.customer_id

        # CASE A: explicit customer_id takes precedence and is validated
        # against the resolved tenant. No email-based rematching occurs.
        if customer_id is not None:
            customer = await CustomerRepository.get_by_id_for_tenant(
                db, customer_id, organization_id
            )
            if customer is None:
                raise ValueError("Customer not found in this organization")

        # CASE B: no explicit customer but a requester email is present:
        # resolve or create a tenant-scoped customer by normalized email.
        elif data.requester_email is not None:
            customer = await CustomerService.resolve_or_create_by_email_for_tenant(
                db,
                email=str(data.requester_email),
                organization_id=organization_id,
            )
            customer_id = customer.id

        # CASE C: no customer_id and no email leaves customer_id NULL.

        requester_email = (
            CustomerService._normalize_email(str(data.requester_email))
            if data.requester_email is not None
            else None
        )

        ticket = Ticket(
            external_id=data.external_id,
            subject=data.subject,
            description=data.description,
            requester_email=requester_email,
            priority=data.priority,
            source=data.source,
            customer_id=customer_id,
            organization_id=organization_id,
        )

        created = await TicketRepository.create(
            db=db,
            ticket=ticket,
        )

        # Local tickets open a provider-neutral cxops conversation with an
        # idempotent initial customer message. Zendesk tickets created through
        # this path do not open a conflicting cxops thread; their conversation
        # is established by the Zendesk ingestion path.
        if created.source != "zendesk":
            conversation = (
                await ConversationIngestionService.sync_ticket_conversation(
                    db,
                    ticket=created,
                    organization_id=organization_id,
                )
            )
            await ConversationIngestionService.ingest_initial_message(
                db,
                conversation=conversation,
                ticket=created,
                organization_id=organization_id,
            )

        return created

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
        customer_id: int | None = None,
    ) -> list[Ticket]:
        if customer_id is not None:
            customer = await CustomerRepository.get_by_id_for_tenant(
                db, customer_id, organization_id
            )
            if customer is None:
                raise ValueError("Customer not found in this organization")

        return await TicketRepository.list_for_tenant(
            db=db,
            organization_id=organization_id,
            offset=offset,
            limit=limit,
            customer_id=customer_id,
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

        updated = await TicketRepository.update_for_tenant(
            db=db,
            ticket=ticket,
            changes=changes,
            organization_id=organization_id,
        )

        # Mirror subject/status/customer changes onto the canonical
        # conversation so the inbox and the ticket can never drift apart.
        await ConversationIngestionService.sync_ticket_conversation(
            db,
            ticket=updated,
            organization_id=organization_id,
        )

        return updated

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
