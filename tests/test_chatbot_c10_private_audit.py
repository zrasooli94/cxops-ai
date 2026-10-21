"""C10 disposable PostgreSQL tests for synthetic reviewer assignments/audit.

DB is real Postgres (CI's *exact* disposable C2 loopback). No real model,
provider credentials or customer text. Intentionally uses real constraints
and triggers, not merely mocks of identity, tenant keys or immutability.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.rbac import AuthorizationContext, OrganizationRole
from app.models.claim_review import (
    ClaimReviewAssignment,
    ClaimReviewBatch,
    ClaimReviewEvent,
    ClaimReviewItem,
)
from app.models.organization import Organization
from app.models.organization_membership import OrganizationMembership
from app.services.private_claim_review_service import PrivateClaimReviewService as C10

_QUOTE = "Aster bicycle repairs are not free."
_KEY = "aster-direct-contradiction:c1"
_C6_DATASET = "c6-fictional-faq-20261021"


def _auth(org: int, subject: str, role: OrganizationRole) -> AuthorizationContext:
    return AuthorizationContext(
        organization_id=org, subject=subject, role=role,
    )


async def _org(db, label: str) -> int:
    record = Organization(name=f"c10-{label}-{uuid.uuid4().hex[:12]}")
    db.add(record)
    await db.flush()
    return record.id


async def _staff(db, org: int, *profiles: tuple[str, OrganizationRole]) -> None:
    db.add_all([
        OrganizationMembership(
            organization_id=org, subject=subject, role=role,
        )
        for subject, role in profiles
    ])
    await db.flush()


async def _setup(db, label: str):
    org_id = await _org(db, label)
    await _staff(
        db, org_id,
        ("owner", OrganizationRole.OWNER),
        ("reviewer-a", OrganizationRole.ADMIN),
        ("reviewer-b", OrganizationRole.SUPERVISOR),
        ("adjudicator-c", OrganizationRole.ADMIN),
        ("viewer", OrganizationRole.VIEWER),
    )
    batch = await C10.create_synthetic_batch(
        db, actor=_auth(org_id, "owner", OrganizationRole.OWNER),
    )
    return org_id, batch.id


async def _assigned(db, label: str):
    org, batch = await _setup(db, label)
    assignments = await C10.assign_reviewers(
        db, actor=_auth(org, "owner", OrganizationRole.OWNER),
        batch_id=batch,
        reviewer_one_subject="reviewer-a",
        reviewer_two_subject="reviewer-b",
    )
    assert [x.slot for x in assignments] == [1, 2]
    return org, batch, assignments


async def _review(db, org, batch, subject, role, label="contradicted"):
    return await C10.submit_review(
        db,
        actor=_auth(org, subject, role),
        batch_id=batch,
        review_key=_KEY,
        label=label,
        evidence_excerpt=_QUOTE if label in {"supported", "contradicted"} else None,
        rationale="Cited source does not support this claim." if label in {
            "unsupported", "unclear",
        } else None,
    )


@pytest.mark.asyncio
async def test_batch_stores_exact_fictional_claim_fingerprints_only(db):
    org, batch_id = await _setup(db, "snapshot")
    batch = (
        await db.execute(
            select(ClaimReviewBatch).where(
                ClaimReviewBatch.organization_id == org,
                ClaimReviewBatch.id == batch_id,
            )
        )
    ).scalar_one()
    assert batch.dataset_id == _C6_DATASET
    assert len(batch.dataset_digest) == 64
    items = (
        await db.execute(
            select(ClaimReviewItem).where(
                ClaimReviewItem.organization_id == org,
                ClaimReviewItem.batch_id == batch_id,
            )
        )
    ).scalars().all()
    assert len(items) == 12
    assert len({x.review_key for x in items}) == 12
    assert all(len(x.fingerprint) == 64 for x in items)
    assert all(len(x.source_digest) == 64 for x in items)
    assert all(x.source_revisions for x in items)
    assert _QUOTE not in repr(items[0].source_revisions)
    await db.commit()


@pytest.mark.asyncio
async def test_assignment_is_private_current_member_only_and_two_distinct_people(db):
    org, batch, assigned = await _assigned(db, "assignment")
    assert [a.reviewer_subject for a in assigned] == [
        "reviewer-a", "reviewer-b",
    ]
    assert await C10.assigned_queue(
        db, actor=_auth(org, "reviewer-a", OrganizationRole.ADMIN),
        batch_id=batch,
    )
    assert await C10.assigned_queue(
        db, actor=_auth(org, "adjudicator-c", OrganizationRole.ADMIN),
        batch_id=batch,
    ) == []
    with pytest.raises(PermissionError):
        await C10.assigned_queue(
            db, actor=_auth(org, "viewer", OrganizationRole.VIEWER),
            batch_id=batch,
        )
    with pytest.raises(ValueError, match="assigned reviewers"):
        await C10.assign_reviewers(
            db, actor=_auth(org, "owner", OrganizationRole.OWNER),
            batch_id=batch,
            reviewer_one_subject="reviewer-b",
            reviewer_two_subject="reviewer-a",
        )
    await db.commit()


@pytest.mark.asyncio
async def test_invalid_reviewer_separation_or_unassigned_subject_rejected(db):
    org, batch_id = await _setup(db, "separation")
    actor = _auth(org, "owner", OrganizationRole.OWNER)
    with pytest.raises(ValueError, match="Two distinct"):
        await C10.assign_reviewers(
            db, actor=actor, batch_id=batch_id,
            reviewer_one_subject="reviewer-a",
            reviewer_two_subject="reviewer-a",
        )
    with pytest.raises(ValueError, match="evaluation-management"):
        await C10.assign_reviewers(
            db, actor=actor, batch_id=batch_id,
            reviewer_one_subject="reviewer-a",
            reviewer_two_subject="viewer",
        )
    with pytest.raises(ValueError, match="current tenant member"):
        await C10.assign_reviewers(
            db, actor=actor, batch_id=batch_id,
            reviewer_one_subject="reviewer-a",
            reviewer_two_subject="other-tenant-rev",
        )
    await db.commit()


@pytest.mark.asyncio
async def test_submitted_review_is_append_only_private_and_digest_only(db):
    org, batch, assigned = await _assigned(db, "submit")
    first = await _review(
        db, org, batch, "reviewer-a", OrganizationRole.ADMIN,
    )
    assert first.event_kind == "review"
    assert first.assignment_id == assigned[0].id
    assert first.actor_subject == "reviewer-a"
    assert first.label == "contradicted"
    assert len(first.evidence_digest) == 64
    assert first.rationale_digest is None
    assert _QUOTE not in str(first.__dict__)
    with pytest.raises(ValueError, match="already exists"):
        await _review(db, org, batch, "reviewer-a", OrganizationRole.ADMIN)
    await db.commit()


@pytest.mark.asyncio
async def test_cannot_claim_truth_without_an_exact_cited_source_excerpt(db):
    org, batch, _ = await _assigned(db, "citation")
    actor = _auth(org, "reviewer-a", OrganizationRole.ADMIN)
    for unsupported_quote in (
        "S1 source ID", "Aster bicycle repairs are free.",
        "Something unrelated to any cited passage.",
    ):
        with pytest.raises(ValueError, match="exact cited"):
            await C10.submit_review(
                db, actor=actor, batch_id=batch, review_key=_KEY,
                label="supported", evidence_excerpt=unsupported_quote,
            )
    with pytest.raises(ValueError, match="bounded rationale"):
        await C10.submit_review(
            db, actor=actor, batch_id=batch, review_key=_KEY,
            label="unclear", rationale="short",
        )
    with pytest.raises(ValueError, match="Unknown claim"):
        await C10.submit_review(
            db, actor=actor, batch_id=batch, review_key=_KEY,
            label="approved", evidence_excerpt=_QUOTE,
        )
    await db.commit()


@pytest.mark.asyncio
async def test_adjudicator_is_third_member_and_only_resolves_disagreement(db):
    org, batch, _ = await _assigned(db, "adjudication")
    await _review(db, org, batch, "reviewer-a", OrganizationRole.ADMIN)
    await _review(
        db, org, batch, "reviewer-b", OrganizationRole.SUPERVISOR,
        label="unsupported",
    )
    with pytest.raises(PermissionError, match="third"):
        await C10.adjudicate(
            db, actor=_auth(org, "reviewer-a", OrganizationRole.ADMIN),
            batch_id=batch, review_key=_KEY, label="contradicted",
            evidence_excerpt=_QUOTE,
        )
    audit = await C10.adjudicate(
        db, actor=_auth(org, "adjudicator-c", OrganizationRole.ADMIN),
        batch_id=batch, review_key=_KEY, label="contradicted",
        evidence_excerpt=_QUOTE,
    )
    assert audit.event_kind == "adjudication"
    assert audit.assignment_id is None
    assert audit.actor_subject == "adjudicator-c"
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(ClaimReviewEvent(
                organization_id=org, batch_id=batch, item_id=audit.item_id,
                assignment_id=None, event_kind="adjudication",
                actor_subject="adjudicator-c", label="contradicted",
                evidence_digest=audit.evidence_digest,
            ))
            await db.flush()
    await db.commit()


@pytest.mark.asyncio
async def test_matching_reviews_cannot_get_an_unnecessary_adjudicator(db):
    org, batch, _ = await _assigned(db, "agreement")
    await _review(db, org, batch, "reviewer-a", OrganizationRole.ADMIN)
    await _review(db, org, batch, "reviewer-b", OrganizationRole.SUPERVISOR)
    with pytest.raises(ValueError, match="disputed"):
        await C10.adjudicate(
            db, actor=_auth(org, "adjudicator-c", OrganizationRole.ADMIN),
            batch_id=batch, review_key=_KEY,
            label="contradicted", evidence_excerpt=_QUOTE,
        )
    await db.commit()


@pytest.mark.asyncio
async def test_other_tenant_cannot_read_batch_or_insert_cross_tenant_fk(db):
    _, batch_a, a = await _assigned(db, "tenant-a")
    org_b, batch_b, _ = await _assigned(db, "tenant-b")
    assert batch_a != batch_b
    with pytest.raises(ValueError, match="not found"):
        await C10.assigned_queue(
            db, actor=_auth(org_b, "reviewer-a", OrganizationRole.ADMIN),
            batch_id=batch_a,
        )
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(ClaimReviewAssignment(
                organization_id=org_b, batch_id=batch_a, slot=1,
                reviewer_subject="fake", assigned_by_subject="fake",
            ))
            await db.flush()
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(ClaimReviewEvent(
                organization_id=org_b, batch_id=batch_b,
                item_id=a[0].id, assignment_id=None,
                event_kind="adjudication",
                actor_subject="adjudicator-c", label="unclear",
                rationale_digest="0" * 64,
            ))
            await db.flush()
    await db.commit()


@pytest.mark.asyncio
async def test_database_refuses_forged_reviewer_assignment_actor(db):
    org, batch, assignments = await _assigned(db, "forged")
    item = (
        await db.execute(select(ClaimReviewItem).where(
            ClaimReviewItem.batch_id == batch, ClaimReviewItem.review_key == _KEY,
        ))
    ).scalar_one()
    with pytest.raises(DBAPIError, match="actor mismatch"):
        async with db.begin_nested():
            db.add(ClaimReviewEvent(
                organization_id=org, batch_id=batch, item_id=item.id,
                assignment_id=assignments[0].id,
                event_kind="review", actor_subject="reviewer-b",
                label="contradicted", evidence_digest="0" * 64,
            ))
            await db.flush()
    await db.commit()


@pytest.mark.asyncio
async def test_postgres_triggers_reject_updates_or_deletes_to_all_four_tables(db):
    org, batch, _ = await _assigned(db, "immutable")
    event = await _review(db, org, batch, "reviewer-a", OrganizationRole.ADMIN)
    item = (
        await db.execute(select(ClaimReviewItem).where(
            ClaimReviewItem.organization_id == org,
            ClaimReviewItem.batch_id == batch, ClaimReviewItem.review_key == _KEY,
        ))
    ).scalar_one()
    actions = [
        update(ClaimReviewBatch).where(
            ClaimReviewBatch.id == batch,
        ).values(dataset_id="other"),
        delete(ClaimReviewBatch).where(ClaimReviewBatch.id == batch),
        update(ClaimReviewItem).where(
            ClaimReviewItem.id == item.id,
        ).values(review_key="forged:c1"),
        delete(ClaimReviewItem).where(ClaimReviewItem.id == item.id),
        update(ClaimReviewAssignment).where(
            ClaimReviewAssignment.batch_id == batch,
        ).values(reviewer_subject="forged"),
        delete(ClaimReviewAssignment).where(
            ClaimReviewAssignment.batch_id == batch,
        ),
        update(ClaimReviewEvent).where(
            ClaimReviewEvent.id == event.id,
        ).values(label="supported"),
        delete(ClaimReviewEvent).where(ClaimReviewEvent.id == event.id),
    ]
    for statement in actions:
        with pytest.raises(DBAPIError, match="append-only"):
            async with db.begin_nested():
                await db.execute(statement)
    await db.commit()


@pytest.mark.asyncio
async def test_revoked_or_downgraded_reviewers_are_denied_at_write_time(db):
    org, batch, _ = await _assigned(db, "downgrade")
    membership = (
        await db.execute(select(OrganizationMembership).where(
            OrganizationMembership.organization_id == org,
            OrganizationMembership.subject == "reviewer-a",
        ))
    ).scalar_one()
    membership.role = OrganizationRole.VIEWER
    await db.flush()
    with pytest.raises(PermissionError):
        await _review(db, org, batch, "reviewer-a", OrganizationRole.ADMIN)
    await db.commit()


@pytest.mark.asyncio
async def test_suspended_organization_never_accepts_new_private_reviews(db):
    org, batch, _ = await _assigned(db, "suspended")
    org_obj = (
        await db.execute(select(Organization).where(Organization.id == org))
    ).scalar_one()
    org_obj.lifecycle_status = "suspended"
    await db.flush()
    with pytest.raises(PermissionError):
        await _review(db, org, batch, "reviewer-a", OrganizationRole.ADMIN)
    await db.commit()


@pytest.mark.asyncio
async def test_db_check_denies_non_synthetic_dataset_even_with_hash(db):
    org, _, _ = await _assigned(db, "unsafe-dataset")
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(ClaimReviewBatch(
                organization_id=org, dataset_id="real-customer-transcripts",
                dataset_digest="a" * 64, created_by_subject="owner",
            ))
            await db.flush()
    await db.commit()


@pytest.mark.asyncio
async def test_schema_has_single_head_after_c10_migration(db):
    version = (await db.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
    assert version == "1r0a0001"
