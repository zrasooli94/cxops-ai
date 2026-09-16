"""Enqueue a synthetic Zendesk ticket event for retry/manual testing.

Usage:
    python -m scripts.enqueue_retry_test --organization-id 1
"""

import argparse
import asyncio
import os

from app.core.database import AsyncSessionLocal
from app.services.integration_job_service import IntegrationJobService


def _organization_id() -> int:
    env_value = os.environ.get("CXOPS_ORGANIZATION_ID", "").strip()
    if env_value:
        try:
            return int(env_value)
        except ValueError as exc:
            raise SystemExit(
                f"error: CXOPS_ORGANIZATION_ID is not an integer: {env_value!r}"
            ) from exc
    return 0


async def main():
    parser = argparse.ArgumentParser(
        description="Enqueue a synthetic Zendesk ticket event for testing."
    )
    parser.add_argument(
        "--organization-id",
        type=int,
        default=_organization_id(),
        help=(
            "CXOps organization id that owns the synthetic event "
            "(default: CXOPS_ORGANIZATION_ID env var)"
        ),
    )
    args = parser.parse_args()

    if args.organization_id <= 0:
        parser.error("--organization-id is required (or set CXOPS_ORGANIZATION_ID)")

    async with AsyncSessionLocal() as db:
        result = await IntegrationJobService.enqueue_zendesk_event(
            db=db,
            invocation_id="retry_test_002",
            event_type="ticket.created",
            zendesk_ticket_id=9999999999,
            payload={
                "type": "zen:event-type:ticket.created",
                "detail": {"id": "9999999999"},
            },
            organization_id=args.organization_id,
        )

        print(result)


if __name__ == "__main__":
    asyncio.run(main())
