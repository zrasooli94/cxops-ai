"""Phase 1M — seed the synthetic "A1 Automotive Pilot (Demo)" tenant.

Idempotent, tenant-explicit seeding that reuses the generic CXOps models:
organizations, SLA policies, service queues, customers, tickets,
conversations, messages, and service escalations. No Automotive-specific
columns or services, no new migration, and no fabricated AI outcomes are
ever created: AgentRun, AIRequestLog, and evaluation rows are out of scope
for this seeder.

Safety contract:
- Only the pilot tenant (by its exact name) is created or mutated. The
  seeder never writes into an arbitrary organization.
- All actors are fictional (example.com emails / fictional 555 phones), no
  real VINs, and every conversation is local (provider ``cxops`` with a NULL
  external thread id).
- Re-runs are safe and deterministic; nothing is duplicated.

Knowledge ingestion is optional (``--with-knowledge-embeddings``) because
embeddings require an OpenAI key; document metadata lives in
``scripts/automotive_pilot_data.py`` and is surfaced by the pilot workspace
regardless.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.database import AsyncSessionLocal
from app.models.conversation import Conversation
from app.models.conversation_message import ConversationMessage
from app.models.customer import Customer
from app.models.organization import Organization
from app.models.organization_membership import (
    OrganizationMembership,
    OrganizationRole,
)
from app.models.service_escalation import ServiceEscalation
from app.models.service_queue import ServiceQueue
from app.models.sla_policy import SLAPolicy
from app.models.ticket import Ticket
from app.services.knowledge_ingestion_service import KnowledgeIngestionService
from scripts.automotive_pilot_data import (
    CUSTOMERS,
    KNOWLEDGE_DOCUMENTS,
    PILOT_INDUSTRY,
    PILOT_ORG_EXTERNAL_ID,
    PILOT_ORGANIZATION_NAME,
    QUEUES,
    SLA_POLICIES,
    TICKETS,
    validate_catalog,
)

ORGANIZATION_EXTERNAL_ID = PILOT_ORG_EXTERNAL_ID


async def _find_organization(db, *, name: str) -> Organization | None:
    result = await db.execute(select(Organization).where(Organization.name == name))
    return result.scalar_one_or_none()


async def _find_sla_by_name(db, organization_id: int, name: str) -> SLAPolicy | None:
    result = await db.execute(
        select(SLAPolicy).where(
            SLAPolicy.organization_id == organization_id,
            SLAPolicy.name == name,
        )
    )
    return result.scalar_one_or_none()


async def _find_queue_by_key(db, organization_id: int, key: str) -> ServiceQueue | None:
    result = await db.execute(
        select(ServiceQueue).where(
            ServiceQueue.organization_id == organization_id,
            ServiceQueue.key == key,
        )
    )
    return result.scalar_one_or_none()


async def _find_customer_by_email(
    db, organization_id: int, email: str
) -> Customer | None:
    result = await db.execute(
        select(Customer).where(
            Customer.organization_id == organization_id,
            Customer.email == email,
        )
    )
    return result.scalar_one_or_none()


async def _find_ticket_by_external_id(
    db, organization_id: int, external_id: str
) -> Ticket | None:
    result = await db.execute(
        select(Ticket).where(
            Ticket.organization_id == organization_id,
            Ticket.external_id == external_id,
        )
    )
    return result.scalar_one_or_none()


async def _find_conversation(
    db, organization_id: int, ticket_id: int | None
) -> Conversation | None:
    result = await db.execute(
        select(Conversation).where(
            Conversation.organization_id == organization_id,
            Conversation.ticket_id == ticket_id,
            Conversation.provider == "cxops",
        )
    )
    return result.scalar_one_or_none()


async def _find_message(db, conversation_id: int, dedupe_key: str):
    result = await db.execute(
        select(ConversationMessage).where(
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.dedupe_key == dedupe_key,
        )
    )
    return result.scalar_one_or_none()


async def _find_escalation(
    db, organization_id: int, ticket_id: int, event_key: str
) -> ServiceEscalation | None:
    result = await db.execute(
        select(ServiceEscalation).where(
            ServiceEscalation.organization_id == organization_id,
            ServiceEscalation.ticket_id == ticket_id,
            ServiceEscalation.event_key == event_key,
        )
    )
    return result.scalar_one_or_none()


async def _org_has_default_policy(db, organization_id: int) -> bool:
    result = await db.execute(
        select(func.count())
        .select_from(SLAPolicy)
        .where(
            SLAPolicy.organization_id == organization_id,
            SLAPolicy.is_default.is_(True),
        )
    )
    return bool(result.scalar_one())


def _deadline(created_at: datetime, policy: SLAPolicy, metric: str, priority: str):
    minutes = policy.target_minutes(metric=metric, priority=priority)
    if minutes is None:
        return None
    return created_at + timedelta(minutes=minutes)


def _escalation_event_key(
    ticket_external_id: str, escalation: dict, kind: str
) -> str:
    return (
        f"a1:{ticket_external_id}:{escalation['milestone']}:"
        f"{escalation['stage']}:{kind}"
    )


def _build_escalation(
    organization_id: int,
    ticket_id: int,
    ticket_external_id: str,
    escalation: dict,
    kind: str,
    event_key: str,
    reference: datetime,
) -> ServiceEscalation:
    triggered_at = reference - timedelta(
        days=escalation["triggered_days_ago"]
    )
    row = ServiceEscalation(
        organization_id=organization_id,
        ticket_id=ticket_id,
        milestone=escalation["milestone"],
        stage=escalation["stage"],
        status="open",
        event_key=event_key,
        triggered_at=triggered_at,
        source="sla_monitor",
        resolution_sla_cycle=0,
        transition_version=1,
    )
    if kind in ("resolved", "reopened"):
        row.status = "resolved"
        row.resolution_sla_cycle = 1
        row.resolved_at = reference - timedelta(
            days=escalation["resolved_days_ago"]
        )
        row.resolution_reason = escalation.get("reason")
    return row


async def seed(
    db,
    *,
    reference: datetime | None = None,
    demo_subject: str | None = None,
    with_knowledge: bool = False,
    print_fn=print,
) -> dict:
    """Create or refresh the pilot tenant. Returns a summary dict."""
    validate_catalog()

    reference = reference or datetime.now(UTC)
    created = {
        "policies": 0,
        "queues": 0,
        "customers": 0,
        "tickets": 0,
        "conversations": 0,
        "messages": 0,
        "escalations": 0,
    }
    existing = {
        "policies": 0,
        "queues": 0,
        "customers": 0,
        "tickets": 0,
        "conversations": 0,
        "messages": 0,
        "escalations": 0,
    }

    # --- Tenant -----------------------------------------------------------
    org = await _find_organization(db, name=PILOT_ORGANIZATION_NAME)
    if org is None:
        org = Organization(
            name=PILOT_ORGANIZATION_NAME,
            industry=PILOT_INDUSTRY,
            external_id=ORGANIZATION_EXTERNAL_ID,
        )
        db.add(org)
        await db.flush()
        print_fn(f"created organization {org.id} ({PILOT_ORGANIZATION_NAME})")
    else:
        print_fn(f"reusing organization {org.id} ({PILOT_ORGANIZATION_NAME})")

    # --- SLA policies ------------------------------------------------------
    has_default = await _org_has_default_policy(db, org.id)
    for spec in SLA_POLICIES:
        existing_policy = await _find_sla_by_name(db, org.id, spec["name"])
        if existing_policy is None:
            want_default = bool(spec["is_default"]) and not has_default
            if want_default:
                has_default = True
            db.add(
                SLAPolicy(
                    organization_id=org.id,
                    name=spec["name"],
                    enabled=True,
                    is_default=want_default,
                    first_response_low_minutes=spec["first_response"]["low"],
                    first_response_normal_minutes=spec["first_response"]["normal"],
                    first_response_high_minutes=spec["first_response"]["high"],
                    first_response_urgent_minutes=spec["first_response"]["urgent"],
                    resolution_low_minutes=spec["resolution"]["low"],
                    resolution_normal_minutes=spec["resolution"]["normal"],
                    resolution_high_minutes=spec["resolution"]["high"],
                    resolution_urgent_minutes=spec["resolution"]["urgent"],
                )
            )
            await db.flush()
            created["policies"] += 1
        else:
            existing["policies"] += 1
    await db.commit()

    policy_by_name: dict[str, SLAPolicy] = {}
    for spec in SLA_POLICIES:
        policy = await _find_sla_by_name(db, org.id, spec["name"])
        assert policy is not None, spec["name"]
        policy_by_name[spec["name"]] = policy

    # --- Service queues ------------------------------------------------------
    queue_by_key: dict[str, ServiceQueue] = {}
    for spec in QUEUES:
        queue = await _find_queue_by_key(db, org.id, spec["key"])
        if queue is None:
            queue = ServiceQueue(
                organization_id=org.id,
                key=spec["key"],
                name=spec["name"],
                description=spec["description"],
                active=True,
                is_default=bool(spec["is_default"]),
                sla_policy_id=policy_by_name[spec["sla_policy"]].id,
            )
            db.add(queue)
            await db.flush()
            created["queues"] += 1
        else:
            existing["queues"] += 1
            if queue.sla_policy_id != policy_by_name[spec["sla_policy"]].id:
                queue.sla_policy_id = policy_by_name[spec["sla_policy"]].id
        queue_by_key[spec["key"]] = queue
    await db.commit()

    # --- Optional owner membership for a demo subject ------------------------
    if demo_subject:
        await db.execute(
            pg_insert(OrganizationMembership)
            .values(
                organization_id=org.id,
                subject=demo_subject,
                role=OrganizationRole.OWNER,
            )
            .on_conflict_do_nothing(index_elements=["organization_id", "subject"])
        )
        await db.commit()

    # --- Customers ------------------------------------------------------------
    customer_by_ref: dict[str, Customer] = {}
    for spec in CUSTOMERS:
        customer = await _find_customer_by_email(db, org.id, spec["email"])
        if customer is None:
            customer = Customer(
                organization_id=org.id,
                name=spec["name"],
                email=spec["email"],
                phone=spec["phone"],
            )
            db.add(customer)
            await db.flush()
            created["customers"] += 1
        else:
            existing["customers"] += 1
        customer_by_ref[spec["ref"]] = customer
    await db.commit()

    # --- Tickets + conversations + messages + escalations ---------------------
    for spec in TICKETS:
        spec_created_at = reference - timedelta(days=spec["days_ago"])
        ticket = await _find_ticket_by_external_id(db, org.id, spec["external_id"])
        if ticket is None:
            ticket = await _create_ticket(
                db,
                org,
                spec,
                reference,
                spec_created_at,
                queue_by_key,
                customer_by_ref,
            )
            created["tickets"] += 1
        else:
            existing["tickets"] += 1
        await _sync_timeline(
            db,
            reference,
            org,
            spec,
            spec_created_at,
            ticket,
            created,
            existing,
        )
    await db.commit()

    # --- Optional knowledge ingestion ------------------------------------------
    knowledge = {"ingested": 0, "skipped": True}
    if with_knowledge:
        for document in KNOWLEDGE_DOCUMENTS:
            await KnowledgeIngestionService.ingest(
                db=db,
                organization_id=org.id,
                title=document["title"],
                content=document["content"],
                source="a1-pilot",
                source_uri=None,
                metadata={
                    "department": document["department"],
                    "disclaimer": "Illustrative pilot policy",
                    "pilot_tenant": "true",
                },
            )
            knowledge["ingested"] += 1
        knowledge["skipped"] = False
        await db.commit()

    print_fn("pilot seed complete")
    return {
        "organization_id": org.id,
        "organization_name": PILOT_ORGANIZATION_NAME,
        "created": created,
        "existing": existing,
        "knowledge": knowledge,
        "reference": reference.isoformat(),
    }


async def _create_ticket(
    db,
    org,
    spec,
    reference: datetime,
    created_at: datetime,
    queue_by_key,
    customer_by_ref,
) -> Ticket:
    queue = queue_by_key[spec["queue"]]
    sla_policy = await db.get(SLAPolicy, queue.sla_policy_id)
    assert sla_policy is not None, queue.sla_policy_id
    ticket = Ticket(
        organization_id=org.id,
        external_id=spec["external_id"],
        subject=spec["subject"],
        description=spec["description"],
        status=spec["status"],
        priority=spec["priority"],
        source="api",
        category=spec["category"],
        customer_id=customer_by_ref[spec["customer"]].id,
        service_queue_id=queue.id,
        sla_policy_id=sla_policy.id,
        created_at=created_at,
        first_response_due_at=_deadline(
            created_at, sla_policy, "first_response", spec["priority"]
        ),
        resolution_due_at=_deadline(
            created_at, sla_policy, "resolution", spec["priority"]
        ),
    )
    if spec["first_response_hours"] is not None:
        ticket.first_response_at = created_at + timedelta(
            hours=spec["first_response_hours"]
        )
    if spec["resolved_days_ago"] is not None:
        ticket.resolved_at = reference - timedelta(days=spec["resolved_days_ago"])
        ticket.resolution_sla_cycle = 1
    db.add(ticket)
    await db.flush()
    return ticket


async def _sync_timeline(
    db,
    reference: datetime,
    org,
    spec,
    created_at: datetime,
    ticket: Ticket,
    created: dict,
    existing: dict,
) -> None:
    conversation = await _find_conversation(db, org.id, ticket.id)
    if conversation is None:
        conversation = Conversation(
            organization_id=org.id,
            ticket_id=ticket.id,
            provider="cxops",
            channel=spec["channel"],
            external_thread_id=None,
            subject=spec["subject"],
            status="open",
            created_at=created_at,
        )
        db.add(conversation)
        await db.flush()
        created["conversations"] += 1
    else:
        existing["conversations"] += 1

    for index, message_spec in enumerate(spec["messages"] or []):
        sent_at = created_at + timedelta(hours=message_spec["h"])
        dedupe_key = f"a1:{spec['external_id']}:msg:{index}"
        message = await _find_message(db, conversation.id, dedupe_key)
        if message is not None:
            existing["messages"] += 1
            continue
        db.add(
            ConversationMessage(
                organization_id=org.id,
                conversation_id=conversation.id,
                provider="cxops",
                dedupe_key=dedupe_key,
                direction=message_spec["d"],
                visibility="public",
                body=message_spec["body"],
                sent_at=sent_at,
            )
        )
        created["messages"] += 1
        if conversation.latest_message_at is None or sent_at > conversation.latest_message_at:
            conversation.latest_message_at = sent_at
    await db.flush()

    for kind, escalation in (
        ("current", spec.get("current_escalation")),
        ("resolved", spec.get("resolved_escalation")),
        ("reopened", spec.get("reopened_escalation")),
    ):
        if escalation is None:
            continue
        event_key = _escalation_event_key(spec["external_id"], escalation, kind)
        bar = await _find_escalation(db, org.id, ticket.id, event_key)
        if bar is not None:
            existing["escalations"] += 1
            continue
        db.add(
            _build_escalation(
                org.id,
                ticket.id,
                spec["external_id"],
                escalation,
                kind,
                event_key,
                reference,
            )
        )
        created["escalations"] += 1
    await db.flush()


async def main(
    *,
    reference: datetime | None = None,
    demo_subject: str | None = None,
    with_knowledge: bool = False,
) -> dict:
    async with AsyncSessionLocal() as db:
        return await seed(
            db,
            reference=reference,
            demo_subject=demo_subject,
            with_knowledge=with_knowledge,
        )


def _parse_reference(raw: str | None) -> datetime | None:
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Seed the synthetic A1 Automotive Pilot (Demo) tenant."
    )
    parser.add_argument(
        "--demo-subject",
        default=None,
        help="Optional authenticated subject (Nhost sub) to grant owner "
        "membership for demo access.",
    )
    parser.add_argument(
        "--reference",
        default=None,
        help="Deterministic seed reference time (ISO 8601 UTC). Defaults to now.",
    )
    parser.add_argument(
        "--with-knowledge-embeddings",
        action="store_true",
        help="Also run vector embedding ingestion via KnowledgeIngestionService "
        "through the existing pipeline (requires OPENAI_API_KEY).",
    )
    args = parser.parse_args()

    result = asyncio.run(
        main(
            reference=_parse_reference(args.reference),
            demo_subject=args.demo_subject,
            with_knowledge=args.with_knowledge_embeddings,
        )
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))