from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ticket_event import TicketEvent
from app.repositories.ticket_event_repository import (
    TicketEventRepository,
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

        event = await TicketEventRepository.create(
            db=db,
            event=event,
        )
        
        await AutomationService.process_ticket_event(
            db=db,
            event=event,
        )

        return WebhookReceipt(
            event_id=data.event_id,
            stored_event_id=event.id,
            duplicate=False,
            status="received",
        )