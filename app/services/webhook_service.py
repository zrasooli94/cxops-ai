from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ticket_event import TicketEvent
from app.repositories.ticket_event_repository import (
    TicketEventRepository,
)
from app.repositories.ticket_repository import (
    TicketRepository,
)
from app.schemas.webhook import (
    TicketEventWebhook,
    WebhookReceipt,
)
from app.services.automation_service import AutomationService


class WebhookService:
    @staticmethod
    async def receive_ticket_event(
        db: AsyncSession,
        data: TicketEventWebhook,
    ) -> WebhookReceipt:

        existing = await TicketEventRepository.get_by_event_key(
            db=db,
            event_key=data.event_id,
        )

        if existing is not None:
            return WebhookReceipt(
                event_id=data.event_id,
                stored_event_id=existing.id,
                duplicate=True,
                status="already_received",
            )

        event = TicketEvent(
            event_key=data.event_id,
            event_type=data.event_type,
            source=data.source,
            ticket_id=data.ticket_id,
            payload=data.payload,
        )

        try:
            event = await TicketEventRepository.create(
                db=db,
                event=event,
            )

        except IntegrityError:
            await db.rollback()

            existing = await TicketEventRepository.get_by_event_key(
                db=db,
                event_key=data.event_id,
            )

            if existing is None:
                raise

            return WebhookReceipt(
                event_id=data.event_id,
                stored_event_id=existing.id,
                duplicate=True,
                status="already_received",
            )

        # Derive the tenant from the persisted parent ticket, then hand it to
        # the tenant-scoped automation pipeline. The event body is signed
        # (``verify_ticket_event_signature``) and the ticket id is a
        # server-persisted reference, so the ticket itself is the trusted
        # tenant source — matching the bounded-internal-path contract. The
        # derivation read is the ONLY unscoped hop here: every downstream rule
        # read, rule match, and ticket update in ``AutomationService`` is
        # tenant-scoped, and a missing ticket / NULL-org ticket fails closed.
        if data.ticket_id is not None:
            ticket = await TicketRepository.get_by_id_unscoped(
                db=db,
                ticket_id=data.ticket_id,
            )
            derived_organization_id = (
                ticket.organization_id if ticket is not None else None
            )
        else:
            derived_organization_id = None

        await AutomationService.process_ticket_event(
            db=db,
            event=event,
            organization_id=derived_organization_id,
        )

        return WebhookReceipt(
            event_id=data.event_id,
            stored_event_id=event.id,
            duplicate=False,
            status="received",
        )
