from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ticket import Ticket
from app.models.ticket_event import TicketEvent
from app.repositories.ticket_event_repository import TicketEventRepository
from app.repositories.ticket_repository import TicketRepository


class AutomationService:

    @staticmethod
    def classify_ticket(
        subject: str,
        description: str,
    ) -> tuple[str, str, str]:

        text = f"{subject} {description}".lower()

        if any(
            keyword in text
            for keyword in [
                "withdraw",
                "withdrawal",
                "deposit",
                "payment",
                "transaction",
                "funds",
            ]
        ):
            return (
                "payments",
                "payments-team",
                "high",
            )

        if any(
            keyword in text
            for keyword in [
                "login",
                "password",
                "account access",
                "locked",
                "authentication",
            ]
        ):
            return (
                "account-access",
                "identity-team",
                "high",
            )

        if any(
            keyword in text
            for keyword in [
                "verification",
                "kyc",
                "identity",
                "document",
                "passport",
            ]
        ):
            return (
                "verification",
                "compliance-team",
                "normal",
            )

        if any(
            keyword in text
            for keyword in [
                "bug",
                "error",
                "crash",
                "technical",
                "not working",
            ]
        ):
            return (
                "technical",
                "technical-support",
                "normal",
            )

        return (
            "general",
            "customer-support",
            "normal",
        )

    @staticmethod
    async def process_ticket_event(
        db: AsyncSession,
        event: TicketEvent,
    ) -> TicketEvent:

        if event.processed:
            return event

        if event.ticket_id is None:
            return await TicketEventRepository.mark_processed(
                db,
                event,
            )

        ticket = await TicketRepository.get_by_id(
            db=db,
            ticket_id=event.ticket_id,
        )

        if ticket is None:
            return await TicketEventRepository.mark_processed(
                db,
                event,
            )

        category, assigned_team, priority = (
            AutomationService.classify_ticket(
                subject=ticket.subject,
                description=ticket.description,
            )
        )

        changes = {
            "category": category,
            "assigned_team": assigned_team,
            "priority": priority,
        }

        await TicketRepository.update(
            db=db,
            ticket=ticket,
            changes=changes,
        )

        return await TicketEventRepository.mark_processed(
            db=db,
            event=event,
        )