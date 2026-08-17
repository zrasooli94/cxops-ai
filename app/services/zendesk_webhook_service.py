from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.zendesk.client import zendesk_client
from app.models.ticket_event import TicketEvent
from app.repositories.ticket_event_repository import (
    TicketEventRepository,
)
from app.repositories.ticket_repository import TicketRepository
from app.schemas.webhook import WebhookReceipt
from app.services.automation_service import AutomationService
from app.services.zendesk_sync_service import ZendeskSyncService


class ZendeskWebhookService:

    @staticmethod
    async def process(
        db: AsyncSession,
        *,
        invocation_id: str,
        event_type: str,
        zendesk_ticket_id: int,
        payload: dict,
    ) -> WebhookReceipt:

        existing = await TicketEventRepository.get_by_event_key(
            db=db,
            event_key=invocation_id,
        )

        if existing:
            return WebhookReceipt(
                event_id=invocation_id,
                stored_event_id=existing.id,
                duplicate=True,
                status="already_received",
            )

        # 1. Pull latest Zendesk ticket into CXOps
        local_ticket = await ZendeskSyncService.sync_ticket(
            db=db,
            zendesk_ticket_id=zendesk_ticket_id,
        )

        # 2. Save webhook event using LOCAL ticket ID
        event = TicketEvent(
            event_key=invocation_id,
            event_type=event_type,
            source="zendesk",
            ticket_id=local_ticket.id,
            payload={
                **payload,
                "zendesk_ticket_id": zendesk_ticket_id,
            },
        )

        event = await TicketEventRepository.create(
            db=db,
            event=event,
        )

        # 3. Run CXOps automation rules
        await AutomationService.process_ticket_event(
            db=db,
            event=event,
        )

        # 4. Reload updated local ticket
        local_ticket = await TicketRepository.get_by_id(
            db=db,
            ticket_id=local_ticket.id,
        )

        if local_ticket is None:
            raise RuntimeError(
                "Local ticket disappeared after automation"
            )

        # 5. Push automation result back to Zendesk
        note = (
            "CXOps AI automation result\n"
            f"Category: {local_ticket.category or 'unclassified'}\n"
            f"Assigned team: {local_ticket.assigned_team or 'unassigned'}\n"
            f"Priority: {local_ticket.priority}"
        )

        await zendesk_client.update_ticket(
            db=db,
            ticket_id=zendesk_ticket_id,
            changes={
                "priority": local_ticket.priority,
                "comment": {
                    "body": note,
                    "public": False,
                },
            },
        )

        return WebhookReceipt(
            event_id=invocation_id,
            stored_event_id=event.id,
            duplicate=False,
            status="processed",
        )