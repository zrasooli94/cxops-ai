import asyncio

from app.core.database import AsyncSessionLocal
from app.services.knowledge_ingestion_service import (
    KnowledgeIngestionService,
)


DOCUMENTS = [
    {
        "title": "Deposit Processing Policy",
        "content": """
Deposits normally appear immediately after confirmation.

Bank transfer deposits may require up to one business day.

If a customer's bank account has been charged but the deposit
has not appeared after one business day, the case should be
escalated to the payments team.

Customers should provide the transaction reference when available.
""",
        "department": "payments",
    },
    {
        "title": "Account Access Policy",
        "content": """
Customers who cannot access their account should attempt a
password reset first.

Accounts may be temporarily locked after repeated unsuccessful
login attempts.

If password reset does not restore access, the case should be
assigned to the identity team.

Suspected account takeover cases require high priority escalation.
""",
        "department": "identity",
    },
    {
        "title": "Identity Verification Policy",
        "content": """
Identity verification may require a valid passport, national ID,
or other approved identity document.

Uploaded documents must be readable and must not be expired.

Most verification reviews are completed within 24 hours.

Cases pending for more than 48 hours should be escalated to the
compliance team.
""",
        "department": "compliance",
    },
    {
        "title": "Refund Policy",
        "content": """
Approved refunds are normally initiated within two business days.

The receiving bank or payment provider may require an additional
three to five business days before the funds appear.

Duplicate refund requests should not be created while an existing
refund is being processed.
""",
        "department": "payments",
    },
    {
        "title": "Platform Incident Policy",
        "content": """
When multiple customers report the same technical failure,
support should check whether a platform incident is active.

Critical outages affecting authentication, payments, or trading
must be escalated immediately to technical operations.

Customers should be informed when an incident is confirmed and
should not be asked to repeatedly retry failed transactions.
""",
        "department": "technical",
    },
]


async def main():
    async with AsyncSessionLocal() as db:

        for document in DOCUMENTS:

            result = await KnowledgeIngestionService.ingest(
                db=db,
                title=document["title"],
                content=document["content"],
                source="cxops-policy",
                source_uri=None,
                metadata={
                    "department": document[
                        "department"
                    ],
                    "policy_version": "1.0",
                },
            )

            print(
                document["title"],
                "→",
                result,
            )


if __name__ == "__main__":
    asyncio.run(main())