"""Database-level tenant-integrity tests for the customer <-> ticket link.

Phase 1E.2 moves the Customer/Ticket tenant invariant from service-layer-only
validation into PostgreSQL:

    tickets(customer_id, organization_id)
        -> customers(id, organization_id)

These tests prove the DATABASE constraint rejects a cross-organization link
(independently of the service checks, which are preserved and also exercised),
that unlinked / legacy NULL shapes remain valid-but-untrusted, and that the
migration preflight audit flags rows that would violate the invariant.
"""

import importlib.util
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError

os.environ["ENVIRONMENT"] = "development"

from app.core.database import AsyncSessionLocal
from app.models.customer import Customer
from app.models.organization import Organization
from app.models.ticket import Ticket
from app.repositories.customer_repository import CustomerRepository
from app.repositories.ticket_repository import TicketRepository
from app.schemas.ticket import TicketCreate, TicketUpdate
from app.services.ticket_service import TicketService

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "1e2a0001_customer_ticket_tenant_integrity.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "mig_1e2a0001_customer_ticket_tenant_integrity",
        _MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest_asyncio.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def tracked(db):
    """Track created rows so teardown can delete them in FK-safe order."""

    tracked_rows = SimpleNamespace(tickets=[], customers=[], orgs=[])
    yield tracked_rows

    await db.rollback()
    if tracked_rows.tickets:
        await db.execute(delete(Ticket).where(Ticket.id.in_(tracked_rows.tickets)))
    if tracked_rows.customers:
        await db.execute(
            delete(Customer).where(Customer.id.in_(tracked_rows.customers))
        )
    if tracked_rows.orgs:
        await db.execute(
            delete(Organization).where(Organization.id.in_(tracked_rows.orgs))
        )
    await db.commit()


async def _new_org(db, tracked, tag: str) -> int:
    org = Organization(name=f"ti-{tag}-{uuid.uuid4().hex[:8]}")
    db.add(org)
    await db.commit()
    await db.refresh(org)
    tracked.orgs.append(org.id)
    return org.id


async def _new_customer(db, tracked, organization_id: int | None, tag: str = "c") -> int:
    customer = Customer(
        name=f"{tag}-customer",
        email=f"{tag}-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=organization_id,
    )
    db.add(customer)
    await db.commit()
    await db.refresh(customer)
    tracked.customers.append(customer.id)
    return customer.id


# ===================================================================
# 1. Same-organization customer link is allowed (DB level)
# ===================================================================
@pytest.mark.asyncio
async def test_same_org_customer_link_allowed(db, tracked):
    org = await _new_org(db, tracked, "a")
    customer = await _new_customer(db, tracked, org, "a")

    ticket = Ticket(
        subject="same org",
        description="link is valid",
        organization_id=org,
        customer_id=customer,
    )
    db.add(ticket)
    await db.commit()
    await db.refresh(ticket)
    tracked.tickets.append(ticket.id)

    assert ticket.customer_id == customer
    assert ticket.organization_id == org


# ===================================================================
# 2. Cross-organization customer link is rejected BY THE DATABASE.
#    The service layer is bypassed so the rejection can only come from
#    the composite foreign key.
# ===================================================================
@pytest.mark.asyncio
async def test_cross_org_customer_link_rejected_by_database(db, tracked):
    org_a = await _new_org(db, tracked, "a")
    org_b = await _new_org(db, tracked, "b")
    customer_b = await _new_customer(db, tracked, org_b, "b")

    bad_ticket = Ticket(
        subject="cross org",
        description="Org A ticket referencing an Org B customer",
        organization_id=org_a,
        customer_id=customer_b,
    )
    db.add(bad_ticket)

    # If the composite FK is absent (pre-1E.2 schema) the commit SUCCEEDS and
    # the test fails with "DID NOT RAISE"; the row must still be removed, or it
    # leaks into the shared DB, poisons the migration preflight, and blocks the
    # reconciliation tool. Track it whenever an id was allocated, then roll back
    # regardless of which path was taken.
    try:
        with pytest.raises(IntegrityError):
            await db.commit()
    finally:
        if bad_ticket.id is not None:
            tracked.tickets.append(bad_ticket.id)
        await db.rollback()


# ===================================================================
# 3. NULL customer_id with a valid organization is allowed
# ===================================================================
@pytest.mark.asyncio
async def test_null_customer_link_allowed(db, tracked):
    org = await _new_org(db, tracked, "a")

    ticket = Ticket(
        subject="unlinked",
        description="no customer",
        organization_id=org,
        customer_id=None,
    )
    db.add(ticket)
    await db.commit()
    await db.refresh(ticket)
    tracked.tickets.append(ticket.id)

    assert ticket.customer_id is None
    assert ticket.organization_id == org


# ===================================================================
# 4. Legacy NULL-org / NULL-customer ticket shape still inserts
# ===================================================================
@pytest.mark.asyncio
async def test_legacy_null_org_null_customer_ticket_allowed(db, tracked):
    ticket = Ticket(
        subject="legacy",
        description="pre-tenant row",
        organization_id=None,
        customer_id=None,
    )
    db.add(ticket)
    await db.commit()
    await db.refresh(ticket)
    tracked.tickets.append(ticket.id)

    assert ticket.organization_id is None
    assert ticket.customer_id is None


# ===================================================================
# 5. Service create flow still links a valid same-tenant customer
# ===================================================================
@pytest.mark.asyncio
async def test_service_create_valid_customer_link(db, tracked):
    org = await _new_org(db, tracked, "a")
    customer = await _new_customer(db, tracked, org, "a")

    ticket = await TicketService.create_ticket_for_tenant(
        db,
        TicketCreate(
            subject="service create",
            description="valid customer",
            customer_id=customer,
        ),
        org,
    )
    tracked.tickets.append(ticket.id)

    assert ticket.customer_id == customer
    assert ticket.organization_id == org


# ===================================================================
# 6. Service update flow still links a valid same-tenant customer
# ===================================================================
@pytest.mark.asyncio
async def test_service_update_valid_customer_link(db, tracked):
    org = await _new_org(db, tracked, "a")
    customer = await _new_customer(db, tracked, org, "a")

    ticket = await TicketService.create_ticket_for_tenant(
        db,
        TicketCreate(subject="service update", description="no customer"),
        org,
    )
    tracked.tickets.append(ticket.id)
    assert ticket.customer_id is None

    updated = await TicketService.update_ticket_for_tenant(
        db,
        ticket.id,
        TicketUpdate(customer_id=customer),
        org,
    )
    assert updated is not None
    assert updated.customer_id == customer
    assert updated.organization_id == org


# ===================================================================
# 7. Service create still rejects a cross-tenant customer
# ===================================================================
@pytest.mark.asyncio
async def test_service_create_cross_tenant_customer_rejected(db, tracked):
    org_a = await _new_org(db, tracked, "a")
    org_b = await _new_org(db, tracked, "b")
    customer_b = await _new_customer(db, tracked, org_b, "b")

    with pytest.raises(ValueError):
        await TicketService.create_ticket_for_tenant(
            db,
            TicketCreate(
                subject="cross tenant",
                description="org B customer",
                customer_id=customer_b,
            ),
            org_a,
        )


# ===================================================================
# 8. Service update still rejects a cross-tenant customer (returns None)
# ===================================================================
@pytest.mark.asyncio
async def test_service_update_cross_tenant_customer_rejected(db, tracked):
    org_a = await _new_org(db, tracked, "a")
    org_b = await _new_org(db, tracked, "b")
    customer_b = await _new_customer(db, tracked, org_b, "b")

    ticket = await TicketService.create_ticket_for_tenant(
        db,
        TicketCreate(subject="service update cross", description="no customer"),
        org_a,
    )
    tracked.tickets.append(ticket.id)

    result = await TicketService.update_ticket_for_tenant(
        db,
        ticket.id,
        TicketUpdate(customer_id=customer_b),
        org_a,
    )
    assert result is None


# ===================================================================
# 9. Same numeric IDs across tenants remain isolated via service paths
# ===================================================================
@pytest.mark.asyncio
async def test_cross_tenant_ticket_paths_are_scoped(db, tracked):
    org_a = await _new_org(db, tracked, "a")
    org_b = await _new_org(db, tracked, "b")
    customer_b = await _new_customer(db, tracked, org_b, "b")

    ticket_b = await TicketService.create_ticket_for_tenant(
        db,
        TicketCreate(
            subject="org b ticket",
            description="owned by org b",
            customer_id=customer_b,
        ),
        org_b,
    )
    tracked.tickets.append(ticket_b.id)

    assert await TicketService.get_ticket_for_tenant(db, ticket_b.id, org_a) is None
    assert (
        await TicketService.update_ticket_for_tenant(
            db,
            ticket_b.id,
            TicketUpdate(subject="hacked"),
            org_a,
        )
        is None
    )


# ===================================================================
# 10. DB schema: composite FK exists with correct columns/order,
#     NO ACTION delete behaviour, and the ticket customer index.
# ===================================================================
@pytest.mark.asyncio
async def test_composite_fk_definition_is_tenant_safe(db):
    row = (
        await db.execute(
            text(
                """
                SELECT pg_get_constraintdef(oid) AS definition,
                       confdeltype::text AS confdeltype
                FROM pg_constraint
                WHERE conname = 'fk_tickets_customer_id_organization_id_customers'
                """
            )
        )
    ).mappings().one()

    assert row["definition"] == (
        "FOREIGN KEY (customer_id, organization_id) "
        "REFERENCES customers(id, organization_id)"
    )
    # 'a' = NO ACTION: never silently SET NULL the tenant column.
    assert row["confdeltype"] == "a"


@pytest.mark.asyncio
async def test_customer_composite_unique_exists(db):
    row = (
        await db.execute(
            text(
                """
                SELECT pg_get_constraintdef(oid) AS definition
                FROM pg_constraint
                WHERE conname = 'ux_customers_id_organization_id'
                """
            )
        )
    ).mappings().one()

    assert row["definition"] == "UNIQUE (id, organization_id)"


@pytest.mark.asyncio
async def test_ticket_customer_index_exists(db):
    count = (
        await db.execute(
            text(
                """
                SELECT COUNT(*) FROM pg_indexes
                WHERE tablename = 'tickets'
                  AND indexname = 'ix_tickets_customer_id'
                """
            )
        )
    ).scalar_one()

    assert count == 1


# ===================================================================
# 11. Legacy NULL-org rows gain no tenant trust
# ===================================================================
@pytest.mark.asyncio
async def test_legacy_null_org_rows_are_not_tenant_trusted(db, tracked):
    org_a = await _new_org(db, tracked, "a")
    legacy_customer = await _new_customer(db, tracked, None, "legacy")
    assert legacy_customer is not None

    legacy_ticket = Ticket(
        subject="legacy null org",
        description="must stay untrusted",
        organization_id=None,
        customer_id=legacy_customer,
    )
    db.add(legacy_ticket)
    await db.commit()
    await db.refresh(legacy_ticket)
    tracked.tickets.append(legacy_ticket.id)

    assert (
        await CustomerRepository.get_by_id_for_tenant(db, legacy_customer, org_a)
        is None
    )
    assert (
        await TicketRepository.get_by_id_for_tenant(db, legacy_ticket.id, org_a) is None
    )


# ===================================================================
# 12. Migration preflight detects rows that would violate the invariant.
#     The composite FK is dropped inside the test transaction so an
#     inconsistent row can be created; the transaction is rolled back, so
#     the constraint and data are restored.
# ===================================================================
@pytest.mark.asyncio
async def test_migration_preflight_detects_inconsistent_rows(db, tracked):
    org_a = await _new_org(db, tracked, "a")
    org_b = await _new_org(db, tracked, "b")
    customer_b = await _new_customer(db, tracked, org_b, "b")
    null_org_customer = await _new_customer(db, tracked, None, "nullorg")

    migration = _load_migration_module()

    try:
        await db.execute(
            text(
                "ALTER TABLE tickets DROP CONSTRAINT "
                "fk_tickets_customer_id_organization_id_customers"
            )
        )

        await db.flush()

        cross_org = Ticket(
            subject="preflight cross org",
            description="org A ticket + org B customer",
            organization_id=org_a,
            customer_id=customer_b,
        )
        null_ticket_org = Ticket(
            subject="preflight null ticket org",
            description="customer link without ticket org",
            organization_id=None,
            customer_id=null_org_customer,
        )
        db.add_all([cross_org, null_ticket_org])
        await db.flush()
        tracked.tickets.extend([cross_org.id, null_ticket_org.id])

        row = (await db.execute(migration._PREFLIGHT_SQL)).mappings().one()

        assert row["cross_org_link"] >= 1
        assert row["customer_link_without_ticket_org"] >= 1
        assert row["customer_link_to_null_org_customer"] >= 1
    finally:
        await db.rollback()


@pytest.mark.asyncio
async def test_migration_preflight_clean_on_current_data(db):
    migration = _load_migration_module()

    row = (await db.execute(migration._PREFLIGHT_SQL)).mappings().one()

    assert row["cross_org_link"] == 0
    assert row["customer_link_without_ticket_org"] == 0
    assert row["customer_link_to_null_org_customer"] == 0
