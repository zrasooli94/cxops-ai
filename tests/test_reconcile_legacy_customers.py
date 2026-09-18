"""Database-backed tests for the legacy customer/ticket tenant reconciliation tool.

Covers the exact production condition (33 NULL-org customer<->ticket pairs) and
the strictness guarantees required before migration 1e2a0001 may run:

  - dry-run never mutates
  - correct target-org backfill keeps customers and tickets in sync
  - every conflict property aborts the ENTIRE apply (no partial assignment)
  - apply is atomic and idempotent
  - unrelated legacy rows are untouched
  - reports contain no customer PII
"""

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import delete, text

os.environ["ENVIRONMENT"] = "development"

from app.core.database import AsyncSessionLocal
from app.models.customer import Customer
from app.models.organization import Organization
from app.models.ticket import Ticket

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "reconcile_legacy_customer_ticket_tenants.py"
)


def _load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "reconcile_legacy_customer_ticket_tenants_tool",
        _SCRIPT_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tool = _load_tool_module()

_FK = "fk_tickets_customer_id_organization_id_customers"


@pytest_asyncio.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def tracked(db):
    tracked_rows = {"tickets": [], "customers": [], "orgs": []}
    yield tracked_rows

    await db.rollback()
    if tracked_rows["tickets"]:
        await db.execute(delete(Ticket).where(Ticket.id.in_(tracked_rows["tickets"])))
    if tracked_rows["customers"]:
        await db.execute(
            delete(Customer).where(Customer.id.in_(tracked_rows["customers"]))
        )
    if tracked_rows["orgs"]:
        await db.execute(
            delete(Organization).where(Organization.id.in_(tracked_rows["orgs"]))
        )
    await db.commit()


async def _new_org(db, tracked, tag: str) -> int:
    org = Organization(name=f"rl-{tag}-{uuid.uuid4().hex[:8]}")
    db.add(org)
    await db.commit()
    await db.refresh(org)
    tracked["orgs"].append(org.id)
    return org.id


async def _new_customer(
    db,
    tracked,
    organization_id: int | None,
    *,
    email: str | None = None,
    external_id: str | None = None,
    tag: str = "c",
) -> int:
    customer = Customer(
        name=f"{tag}-customer",
        email=email or f"{tag}-{uuid.uuid4().hex[:8]}@example.com",
        external_id=external_id,
        organization_id=organization_id,
    )
    db.add(customer)
    await db.commit()
    await db.refresh(customer)
    tracked["customers"].append(customer.id)
    return customer.id


async def _new_legacy_ticket(
    db,
    tracked,
    customer_id: int,
    *,
    subject: str = "legacy ticket",
    source: str = "import",
    external_id: str | None = None,
) -> int:
    ticket = Ticket(
        subject=subject,
        description="pre-tenant reconciliation row",
        organization_id=None,
        customer_id=customer_id,
        source=source,
        external_id=external_id,
    )
    db.add(ticket)
    await db.commit()
    await db.refresh(ticket)
    tracked["tickets"].append(ticket.id)
    return ticket.id


async def _drop_ticket_fk(db):
    # IF EXISTS: on a pre-1E.2 schema the composite FK is created by migration
    # 1e2a0001 and legitimately absent; on a post-1E.2 schema it exists and must
    # be dropped so the hazard rows can be created. Either way the change lives
    # in the test transaction and is rolled back in the caller's `finally`.
    await db.execute(text(f"ALTER TABLE tickets DROP CONSTRAINT IF EXISTS {_FK}"))
    await db.flush()


# ===================================================================
# 1. Dry-run never mutates
# ===================================================================
@pytest.mark.asyncio
async def test_dry_run_never_mutates(db, tracked):
    org = await _new_org(db, tracked, "a")
    customer = await _new_customer(db, tracked, None, tag="legacy")
    ticket = await _new_legacy_ticket(db, tracked, customer)

    snapshot = await tool.collect_legacy_snapshot(db)
    assert snapshot.ticket_ids == [ticket]
    assert snapshot.customer_ids == [customer]
    assert org in [entry["id"] for entry in snapshot.organizations]
    render_dry_run_output = tool.render_dry_run(snapshot)
    assert "already clean" not in render_dry_run_output

    row = (
        await db.execute(
            text("SELECT organization_id FROM customers WHERE id = :id"),
            {"id": customer},
        )
    ).scalar_one()
    assert row is None
    assert (await db.get(Ticket, ticket)).organization_id is None


# ===================================================================
# 2. Apply backfills the declared org to customers and tickets
# ===================================================================
@pytest.mark.asyncio
async def test_apply_backfills_declared_org(db, tracked):
    org = await _new_org(db, tracked, "a")
    customer = await _new_customer(db, tracked, None, tag="legacy")
    ticket = await _new_legacy_ticket(db, tracked, customer)

    result = await tool.run_apply(db, org)
    assert result["already_clean"] is False
    assert result["assigned_customers"] == 1
    assert result["assigned_tickets"] == 1

    assert (
        await db.get(Customer, customer)
    ).organization_id == org
    assert (await db.get(Ticket, ticket)).organization_id == org


# ===================================================================
# 3. After apply, customers and their linked tickets share the org
# ===================================================================
@pytest.mark.asyncio
async def test_after_apply_customers_and_tickets_share_org(db, tracked):
    org = await _new_org(db, tracked, "a")
    customers = [
        await _new_customer(db, tracked, None, tag="l1"),
        await _new_customer(db, tracked, None, tag="l2"),
    ]
    tickets = [
        await _new_legacy_ticket(db, tracked, customers[0], source="import"),
        await _new_legacy_ticket(db, tracked, customers[1], source="api"),
    ]

    await tool.run_apply(db, org)

    for customer_id in customers:
        customer = await db.get(Customer, customer_id)
        assert customer.organization_id == org
        assert customer.email is not None
    for ticket_id in tickets:
        ticket = await db.get(Ticket, ticket_id)
        assert ticket.organization_id == org
        assert ticket.customer_id in customers

    remaining = await tool._preflight_counts(db)
    assert remaining == {"cross_org_link": 0, "customer_link_without_ticket_org": 0, "customer_link_to_null_org_customer": 0}


# ===================================================================
# 4. Email uniqueness conflict with a target-org customer aborts
# ===================================================================
@pytest.mark.asyncio
async def test_email_conflict_with_existing_target_customer_aborts(db, tracked):
    target = await _new_org(db, tracked, "target")
    shared_email = f"dup-{uuid.uuid4().hex[:8]}@example.com"
    await _new_customer(db, tracked, target, email=shared_email, tag="existing")
    legacy = await _new_customer(db, tracked, None, email=shared_email, tag="legacy")
    await _new_legacy_ticket(db, tracked, legacy)

    with pytest.raises(tool.ReconciliationAbort) as excinfo:
        await tool.run_apply(db, target)
    assert "aborting entirely" in str(excinfo.value)

    assert (await db.get(Customer, legacy)).organization_id is None


# ===================================================================
# 5. external_id uniqueness conflict with a target-org customer aborts
# ===================================================================
@pytest.mark.asyncio
async def test_external_id_conflict_with_existing_target_customer_aborts(db, tracked):
    target = await _new_org(db, tracked, "target")
    shared_ext = f"EXT-DUP-{uuid.uuid4().hex[:8]}"
    await _new_customer(db, tracked, target, external_id=shared_ext, tag="existing")
    legacy = await _new_customer(db, tracked, None, external_id=shared_ext, tag="legacy")
    await _new_legacy_ticket(db, tracked, legacy, external_id=f"T-{shared_ext}")

    with pytest.raises(tool.ReconciliationAbort):
        await tool.run_apply(db, target)

    assert (await db.get(Customer, legacy)).organization_id is None


# ===================================================================
# 6. A ticket owned by another org referencing a legacy customer aborts.
#    The composite FK is dropped inside the test transaction so the
#    pre-migration shape can be created; NOTHING is committed while the
#    constraint is dropped and the final rollback restores it.
# ===================================================================
@pytest.mark.asyncio
async def test_cross_org_ticket_referencing_legacy_customer_aborts(db, tracked):
    target = await _new_org(db, tracked, "target")
    other = await _new_org(db, tracked, "other")
    legacy_customer = await _new_customer(db, tracked, None, tag="legacy")

    try:
        await _drop_ticket_fk(db)

        legacy_pair = Ticket(
            subject="legacy pair",
            description="pre-tenant reconciliation row",
            organization_id=None,
            customer_id=legacy_customer,
            source="import",
        )
        db.add(legacy_pair)
        await db.flush()
        tracked["tickets"].append(legacy_pair.id)

        cross = Ticket(
            subject="cross org ticket",
            description="owned by other org, references a NULL-org customer",
            organization_id=other,
            customer_id=legacy_customer,
            source="import",
        )
        db.add(cross)
        await db.flush()
        tracked["tickets"].append(cross.id)

        with pytest.raises(tool.ReconciliationAbort) as excinfo:
            await tool.run_apply(db, target)
        assert "aborting entirely" in str(excinfo.value)
    finally:
        await db.rollback()


# ===================================================================
# 7. Mixed owned/unowned: an owned-ticket-in-TARGET link is unambiguous
#    (allowed), but an owned-ticket-in-OTHER-org link is ambiguous and
#    aborts the entire apply.
# ===================================================================
@pytest.mark.asyncio
async def test_mixed_owned_unowned_ambiguous_aborts_but_target_owned_ok(db, tracked):
    target = await _new_org(db, tracked, "target")
    other = await _new_org(db, tracked, "other")
    clean_customer = await _new_customer(db, tracked, None, tag="clean")
    ambiguous_customer = await _new_customer(db, tracked, None, tag="ambig")

    try:
        await _drop_ticket_fk(db)

        # Unambiguous: legacy pair + owned target-org ticket for same customer.
        await db.flush()
        db.add(
            Ticket(
                subject="clean legacy",
                description="pre-tenant reconciliation row",
                organization_id=None,
                customer_id=clean_customer,
                source="import",
            )
        )
        await db.flush()
        target_owned = Ticket(
            subject="target owned",
            description="same customer, unambiguous target ownership",
            organization_id=target,
            customer_id=clean_customer,
            source="api",
        )
        db.add(target_owned)
        await db.flush()
        tracked["tickets"].append(target_owned.id)

        # Ambiguous: legacy pair + owned OTHER-org ticket for another customer.
        db.add(
            Ticket(
                subject="ambiguous legacy",
                description="pre-tenant reconciliation row",
                organization_id=None,
                customer_id=ambiguous_customer,
                source="import",
            )
        )
        await db.flush()
        other_owned = Ticket(
            subject="other owned",
            description="same customer, different org - ambiguous ownership",
            organization_id=other,
            customer_id=ambiguous_customer,
            source="import",
        )
        db.add(other_owned)
        await db.flush()
        tracked["tickets"].append(other_owned.id)

        with pytest.raises(tool.ReconciliationAbort):
            await tool.run_apply(db, target)
    finally:
        await db.rollback()


# ===================================================================
# 8. Nonexistent target org aborts
# ===================================================================
@pytest.mark.asyncio
async def test_nonexistent_target_org_aborts(db, tracked):
    legacy = await _new_customer(db, tracked, None, tag="legacy")
    await _new_legacy_ticket(db, tracked, legacy)

    with pytest.raises(tool.ReconciliationAbort) as excinfo:
        await tool.run_apply(db, 2_147_483_600)
    assert "does not exist" in str(excinfo.value)

    assert (await db.get(Customer, legacy)).organization_id is None


# ===================================================================
# 9. A conflicting apply is atomic: nothing is assigned, even when some
#    rows would otherwise have been safe to assign.
# ===================================================================
@pytest.mark.asyncio
async def test_atomic_abort_leaves_no_changes(db, tracked):
    target = await _new_org(db, tracked, "target")
    shared_email = f"dup2-{uuid.uuid4().hex[:8]}@example.com"
    await _new_customer(db, tracked, target, email=shared_email, tag="existing")

    safe_customer = await _new_customer(db, tracked, None, tag="safe")
    blocking_customer = await _new_customer(db, tracked, None, email=shared_email, tag="blocking")

    await _new_legacy_ticket(db, tracked, safe_customer, subject="would be safe")
    await _new_legacy_ticket(db, tracked, blocking_customer, subject="blocks")

    with pytest.raises(tool.ReconciliationAbort):
        await tool.run_apply(db, target)

    pre = await tool.collect_legacy_snapshot(db)
    assert sorted(pre.customer_ids) == sorted([safe_customer, blocking_customer])

    for customer_id in (safe_customer, blocking_customer):
        assert (await db.get(Customer, customer_id)).organization_id is None


# ===================================================================
# 10. Second apply when already clean is a no-op
# ===================================================================
@pytest.mark.asyncio
async def test_second_apply_is_idempotent(db, tracked):
    org = await _new_org(db, tracked, "a")
    customer = await _new_customer(db, tracked, None, tag="legacy")
    ticket = await _new_legacy_ticket(db, tracked, customer)

    first = await tool.run_apply(db, org)
    assert first["assigned_customers"] == 1

    pre = await tool.collect_legacy_snapshot(db)
    assert pre.ticket_ids == []
    assert pre.customer_ids == []

    second = await tool.run_apply(db, org)
    assert second["already_clean"] is True
    assert second["assigned_customers"] == 0

    assert (await db.get(Customer, customer)).organization_id == org
    assert (await db.get(Ticket, ticket)).organization_id == org


# ===================================================================
# 11. Unrelated NULL-org ticket with NULL customer_id is untouched
# ===================================================================
@pytest.mark.asyncio
async def test_unrelated_null_customer_null_org_ticket_untouched(db, tracked):
    org = await _new_org(db, tracked, "a")
    legacy_customer = await _new_customer(db, tracked, None, tag="legacy")
    legacy = await _new_legacy_ticket(db, tracked, legacy_customer)

    unrelated = Ticket(
        subject="unrelated",
        description="no customer, stays legacy",
        organization_id=None,
        customer_id=None,
        source="manual",
    )
    db.add(unrelated)
    await db.commit()
    await db.refresh(unrelated)
    tracked["tickets"].append(unrelated.id)

    result = await tool.run_apply(db, org)
    assert result["assigned_tickets"] == 1
    assert (await db.get(Ticket, legacy)).organization_id == org
    assert (await db.get(Ticket, unrelated.id)).organization_id is None
    assert (await db.get(Ticket, unrelated.id)).customer_id is None


# ===================================================================
# 12. Reports/logs expose no customer PII (emails, names, phones, ticket
#     text are invisible in output)
# ===================================================================
@pytest.mark.asyncio
async def test_report_contains_no_customer_pii(db, tracked):
    pii_email = f"pii-{uuid.uuid4().hex[:8]}@privated.example.com"
    pii_name = f"PRIVATE-NAME-{uuid.uuid4().hex[:10]}"
    pii_phone = f"+1-555-{uuid.uuid4().hex[:4]}"
    secret_subject = f"TOP-SECRET-SUBJECT-{uuid.uuid4().hex[:8]}"

    target = await _new_org(db, tracked, "target")
    customer = Customer(
        name=pii_name,
        email=pii_email,
        phone=pii_phone,
        organization_id=None,
    )
    db.add(customer)
    await db.commit()
    await db.refresh(customer)
    tracked["customers"].append(customer.id)

    ticket = Ticket(
        subject=secret_subject,
        description="REFUND PLAN 42 -- internal details here",
        organization_id=None,
        customer_id=customer.id,
        source="import",
    )
    db.add(ticket)
    await db.commit()
    await db.refresh(ticket)
    tracked["tickets"].append(ticket.id)

    snapshot = await tool.collect_legacy_snapshot(db)
    report = tool.render_dry_run(snapshot)
    apply_result = await tool.run_apply(db, target)

    for leaked in (
        pii_email,
        pii_name,
        pii_phone,
        secret_subject,
        "REFUND PLAN",
    ):
        assert leaked not in report, f"PII leaked into dry-run report: {leaked}"
        assert leaked not in str(apply_result), f"PII leaked into apply result: {leaked}"