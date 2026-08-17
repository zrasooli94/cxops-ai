import asyncio

from app.core.database import AsyncSessionLocal
from app.services.integration_job_service import IntegrationJobService


async def main():
    async with AsyncSessionLocal() as db:
        result = await IntegrationJobService.enqueue_zendesk_event(
            db=db,
            invocation_id="retry_test_002",
            event_type="ticket.created",
            zendesk_ticket_id=9999999999,
            payload={
                "type": "zen:event-type:ticket.created",
                "detail": {
                    "id": "9999999999"
                },
            },
        )

        print(result)


if __name__ == "__main__":
    asyncio.run(main())