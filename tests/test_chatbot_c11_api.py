"""C11 real JWT/membership/RBAC FastAPI API tests against disposable PG16.

All subjects, emails, organizations, claims and evidence are fictional.
No provider models, real customer text, credentials or production endpoints.
"""
from __future__ import annotations

import os
import time
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import select

os.environ["AUTH_MODE"] = "hs256"
os.environ["AUTH_JWT_SECRET"] = "c" * 32
os.environ["AUTH_JWT_ALGORITHM"] = "HS256"
os.environ["AUTH_JWT_ISSUER"] = "c11-synthetic-test-issuer"
os.environ["AUTH_JWT_AUDIENCE"] = "c11-synthetic-test-audience"
os.environ["AUTH_DEV_MODE"] = "False"
os.environ["ENVIRONMENT"] = "development"

from app.core.config import reset_settings_cache
from app.core.database import AsyncSessionLocal
from app.core.rbac import OrganizationRole
from app.main import app
from app.models.claim_review import ClaimReviewBatch, ClaimReviewEvent
from app.models.organization import Organization
from app.models.organization_membership import OrganizationMembership

PREFIX = "/evaluations/claim-reviews"
CLAIM_KEY = "aster-direct-contradiction:c1"
QUOTE = "Aster bicycle repairs are not free."
HEAD_TENANT = "X-CXOps-Organization-ID"


@pytest.fixture(autouse=True)
def _strict_test_config(monkeypatch):
    values = {
        "AUTH_MODE": "hs256",
        "AUTH_JWT_SECRET": "c" * 32,
        "AUTH_JWT_ALGORITHM": "HS256",
        "AUTH_JWT_ISSUER": "c11-synthetic-test-issuer",
        "AUTH_JWT_AUDIENCE": "c11-synthetic-test-audience",
        "AUTH_DEV_MODE": "False",
        "ENVIRONMENT": "development",
        "CLAIM_REVIEW_SYNTHETIC_API_ENABLED": "true",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as http:
        yield http


@pytest_asyncio.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


def token(subject: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": subject,
            "email": f"{subject}@fixture.invalid",
            "iss": "c11-synthetic-test-issuer",
            "aud": "c11-synthetic-test-audience",
            "exp": now + 3600,
            "iat": now,
        },
        "c" * 32,
        algorithm="HS256",
    )


def headers(subject: str, org_id: int | None = None) -> dict[str, str]:
    result = {"Authorization": f"Bearer {token(subject)}"}
    if org_id is not None:
        result[HEAD_TENANT] = str(org_id)
    return result


@pytest_asyncio.fixture
async def orgs(db):
    first = Organization(name=f"c11-first-{uuid.uuid4().hex[:12]}")
    second = Organization(name=f"c11-second-{uuid.uuid4().hex[:12]}")
    db.add_all([first, second])
    await db.flush()
    staff = [
        (first.id, "c11-owner", OrganizationRole.OWNER),
        (first.id, "c11-admin", OrganizationRole.ADMIN),
        (first.id, "c11-supervisor", OrganizationRole.SUPERVISOR),
        (first.id, "c11-viewer", OrganizationRole.VIEWER),
        (first.id, "c11-r-a", OrganizationRole.ADMIN),
        (first.id, "c11-r-b", OrganizationRole.SUPERVISOR),
        (first.id, "c11-adjudicator", OrganizationRole.ADMIN),
        (second.id, "c11-owner", OrganizationRole.OWNER),
        (second.id, "c11-second-member", OrganizationRole.ADMIN),
    ]
    db.add_all([
        OrganizationMembership(
            organization_id=org,
            subject=subject,
            role=role,
        )
        for org, subject, role in staff
    ])
    await db.commit()
    return first, second


async def make_batch(client, org):
    result = await client.post(
        PREFIX + "/synthetic-batches", headers=headers("c11-owner", org.id),
    )
    assert result.status_code == 201, result.text
    return result.json()["id"]


async def make_assignments(client, org, batch):
    result = await client.post(
        f"{PREFIX}/batches/{batch}/assignments",
        json={
            "reviewer_one_subject": "c11-r-a",
            "reviewer_two_subject": "c11-r-b",
        },
        headers=headers("c11-owner", org.id),
    )
    assert result.status_code == 201, result.text
    return result.json()


async def submit(client, org, batch, subject, label, **kwargs):
    return await client.post(
        f"{PREFIX}/batches/{batch}/reviews/{CLAIM_KEY}",
        json={"label": label, **kwargs},
        headers=headers(subject, org.id),
    )


@pytest.mark.asyncio
async def test_default_off_even_for_authorized_owner(client, orgs, monkeypatch):
    first, _ = orgs
    monkeypatch.setenv("CLAIM_REVIEW_SYNTHETIC_API_ENABLED", "false")
    reset_settings_cache()
    result = await client.post(
        PREFIX + "/synthetic-batches", headers=headers("c11-owner", first.id),
    )
    assert result.status_code == 404
    assert result.json()["detail"] == "Not found"
    monkeypatch.setenv("CLAIM_REVIEW_SYNTHETIC_API_ENABLED", "true")
    reset_settings_cache()


@pytest.mark.asyncio
async def test_disabled_in_any_non_development_environment(client, orgs, monkeypatch):
    first, _ = orgs
    # Not "production" to avoid exercising unrelated production auth secrets.
    monkeypatch.setenv("ENVIRONMENT", "staging")
    reset_settings_cache()
    result = await client.post(
        PREFIX + "/synthetic-batches", headers=headers("c11-owner", first.id),
    )
    assert result.status_code == 404


@pytest.mark.asyncio
async def test_real_auth_requires_token_and_membership_before_writes(client, orgs):
    first, second = orgs
    assert (
        await client.post(PREFIX + "/synthetic-batches")
    ).status_code == 401
    invalid = await client.post(
        PREFIX + "/synthetic-batches",
        headers={"Authorization": "Bearer not-a-real-jwt", HEAD_TENANT: str(first.id)},
    )
    assert invalid.status_code == 401

    selected_wrong_tenant = await client.post(
        PREFIX + "/synthetic-batches",
        headers=headers("c11-admin", second.id),
    )
    assert selected_wrong_tenant.status_code == 403

    viewer = await client.post(
        PREFIX + "/synthetic-batches", headers=headers("c11-viewer", first.id),
    )
    assert viewer.status_code == 403


@pytest.mark.asyncio
async def test_create_assign_admin_only_and_no_body_tenant_spoofing(client, orgs):
    first, second = orgs
    supervisor = await client.post(
        PREFIX + "/synthetic-batches",
        headers=headers("c11-supervisor", first.id),
    )
    assert supervisor.status_code == 403  # member.manage needed too
    spoofed = await client.post(
        PREFIX + "/synthetic-batches",
        json={"organization_id": second.id, "created_by_subject": "other"},
        headers=headers("c11-owner", first.id),
    )
    # No request body schema: no caller-controlled tenant identity.
    assert spoofed.status_code in {201, 422}
    if spoofed.status_code == 201:
        assert spoofed.json()["dataset_id"] == "c6-fictional-faq-20261021"
    batch = await make_batch(client, first)
    queue_before = await client.get(
        f"{PREFIX}/batches/{batch}/my-queue",
        headers=headers("c11-admin", first.id),
    )
    assert queue_before.status_code == 200
    assert queue_before.json()["items"] == []

    assigned = await make_assignments(client, first, batch)
    assert assigned == {"batch_id": batch, "assigned_slots": 2}
    duplicate = await client.post(
        f"{PREFIX}/batches/{batch}/assignments",
        json={"reviewer_one_subject": "c11-r-b", "reviewer_two_subject": "c11-r-a"},
        headers=headers("c11-owner", first.id),
    )
    assert duplicate.status_code == 409
    assert "c11-r-" not in duplicate.text


@pytest.mark.asyncio
async def test_assignment_body_cannot_choose_actor_tenant_or_freeform_dataset(client, orgs):
    first, second = orgs
    batch = await make_batch(client, first)
    attempts = [
        {"reviewer_one_subject": "c11-r-a",
         "reviewer_two_subject": "c11-r-b", "organization_id": second.id},
        {"reviewer_one_subject": "c11-r-a",
         "reviewer_two_subject": "c11-r-b", "actor_subject": "c11-admin"},
        {"reviewer_one_subject": "c11-r-a",
         "reviewer_two_subject": "c11-r-b", "dataset": {"real": "content"}},
    ]
    for body in attempts:
        response = await client.post(
            f"{PREFIX}/batches/{batch}/assignments",
            json=body, headers=headers("c11-owner", first.id),
        )
        assert response.status_code == 422, response.text

    wrong_reviewer = await client.post(
        f"{PREFIX}/batches/{batch}/assignments",
        json={"reviewer_one_subject": "c11-r-a",
              "reviewer_two_subject": "c11-second-member"},
        headers=headers("c11-owner", first.id),
    )
    assert wrong_reviewer.status_code == 400
    assert "c11-second-member" not in wrong_reviewer.text
    assert (await make_assignments(client, first, batch))["assigned_slots"] == 2


@pytest.mark.asyncio
async def test_queue_only_shows_own_completed_claims_not_other_votes(client, orgs):
    first, _ = orgs
    batch = await make_batch(client, first)
    await make_assignments(client, first, batch)
    url = f"{PREFIX}/batches/{batch}/my-queue"
    before = await client.get(url, headers=headers("c11-r-a", first.id))
    assert before.status_code == 200
    assert before.json()["total"] == 12
    assert all(not row["completed"] for row in before.json()["items"])
    assert "actor_subject" not in before.text
    assert "c11-r-b" not in before.text
    assert "source_revisions" not in before.text
    assert "evidence_excerpt" not in before.text

    review = await submit(
        client, first, batch, "c11-r-a", "contradicted", evidence_excerpt=QUOTE,
    )
    assert review.status_code == 201, review.text
    record = review.json()
    assert set(record) == {
        "id", "batch_id", "review_key", "event_kind", "label", "created_at",
    }
    assert record["label"] == "contradicted"
    assert QUOTE not in review.text
    assert "c11-r-a" not in review.text

    me = await client.get(url, headers=headers("c11-r-a", first.id))
    other = await client.get(url, headers=headers("c11-r-b", first.id))
    assert me.status_code == other.status_code == 200
    assert sum(row["completed"] for row in me.json()["items"]) == 1
    assert not any(row["completed"] for row in other.json()["items"])
    assert "contradicted" not in me.text + other.text

    duplicate = await submit(
        client, first, batch, "c11-r-a", "supported", evidence_excerpt=QUOTE,
    )
    assert duplicate.status_code == 409


@pytest.mark.asyncio
async def test_unassigned_or_read_only_cannot_submit_review(client, orgs):
    first, _ = orgs
    batch = await make_batch(client, first)
    await make_assignments(client, first, batch)
    unassigned = await submit(
        client, first, batch, "c11-admin", "contradicted", evidence_excerpt=QUOTE,
    )
    assert unassigned.status_code == 403
    viewer = await submit(
        client, first, batch, "c11-viewer", "contradicted",
        evidence_excerpt=QUOTE,
    )
    assert viewer.status_code == 403


@pytest.mark.asyncio
async def test_wrong_tenant_batch_404_and_no_foreign_record_leak(client, orgs):
    first, second = orgs
    batch = await make_batch(client, first)
    paths = [
        ("get", f"{PREFIX}/batches/{batch}/my-queue", None),
        ("post", f"{PREFIX}/batches/{batch}/assignments", {
            "reviewer_one_subject": "c11-owner",
            "reviewer_two_subject": "c11-second-member",
        }),
        ("post", f"{PREFIX}/batches/{batch}/reviews/{CLAIM_KEY}", {
            "label": "contradicted", "evidence_excerpt": QUOTE,
        }),
        ("post", f"{PREFIX}/batches/{batch}/adjudications/{CLAIM_KEY}", {
            "label": "contradicted", "evidence_excerpt": QUOTE,
        }),
    ]
    for method, uri, body in paths:
        response = await client.request(
            method.upper(),
            uri,
            json=body,
            headers=headers("c11-owner", second.id),
        )
        assert response.status_code == 404, response.text
        assert "c11" not in response.text.casefold()


@pytest.mark.asyncio
async def test_invalid_citation_and_arbitrary_fields_refused(client, orgs):
    first, _ = orgs
    batch = await make_batch(client, first)
    await make_assignments(client, first, batch)
    wrong = await submit(
        client, first, batch, "c11-r-a", "supported",
        evidence_excerpt="That made-up claim is free of charge.",
    )
    assert wrong.status_code == 400
    short = await submit(
        client, first, batch, "c11-r-a", "unclear", rationale="no",
    )
    assert short.status_code == 400
    for extra in (
        {"actor_subject": "c11-owner"},
        {"organization_id": 99999},
        {"customer_message": "do not record me"},
    ):
        response = await submit(
            client, first, batch, "c11-r-a", "contradicted",
            evidence_excerpt=QUOTE, **extra,
        )
        assert response.status_code == 422, response.text
    invalid = await submit(
        client, first, batch, "c11-r-a", "approved", evidence_excerpt=QUOTE,
    )
    assert invalid.status_code == 422
    oversized = await submit(
        client, first, batch, "c11-r-a", "supported",
        evidence_excerpt="a" * 1001,
    )
    assert oversized.status_code == 422


@pytest.mark.asyncio
async def test_third_person_adjudication_only_for_two_disagreeing_reviews(client, orgs):
    first, _ = orgs
    batch = await make_batch(client, first)
    await make_assignments(client, first, batch)
    pending = await client.post(
        f"{PREFIX}/batches/{batch}/adjudications/{CLAIM_KEY}",
        json={"label": "contradicted", "evidence_excerpt": QUOTE},
        headers=headers("c11-adjudicator", first.id),
    )
    assert pending.status_code == 409
    assert (await submit(
        client, first, batch, "c11-r-a", "contradicted",
        evidence_excerpt=QUOTE,
    )).status_code == 201
    assert (await submit(
        client, first, batch, "c11-r-b", "unsupported",
        rationale="The cited policy directly opposes a free repair claim.",
    )).status_code == 201
    self_adjudicate = await client.post(
        f"{PREFIX}/batches/{batch}/adjudications/{CLAIM_KEY}",
        json={"label": "contradicted", "evidence_excerpt": QUOTE},
        headers=headers("c11-r-a", first.id),
    )
    assert self_adjudicate.status_code == 403
    final = await client.post(
        f"{PREFIX}/batches/{batch}/adjudications/{CLAIM_KEY}",
        json={"label": "contradicted", "evidence_excerpt": QUOTE},
        headers=headers("c11-adjudicator", first.id),
    )
    assert final.status_code == 201, final.text
    assert final.json()["event_kind"] == "adjudication"
    assert "actor_subject" not in final.text
    again = await client.post(
        f"{PREFIX}/batches/{batch}/adjudications/{CLAIM_KEY}",
        json={"label": "unclear", "rationale": "This synthetic claim remains disputed."},
        headers=headers("c11-adjudicator", first.id),
    )
    assert again.status_code == 409


@pytest.mark.asyncio
async def test_stale_membership_and_suspended_org_denied_by_service(client, db, orgs):
    first, _ = orgs
    batch = await make_batch(client, first)
    await make_assignments(client, first, batch)
    membership = (
        await db.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == first.id,
                OrganizationMembership.subject == "c11-r-a",
            )
        )
    ).scalar_one()
    membership.role = OrganizationRole.VIEWER
    await db.commit()
    denied = await submit(
        client, first, batch, "c11-r-a", "contradicted",
        evidence_excerpt=QUOTE,
    )
    assert denied.status_code == 403

    current = (
        await db.execute(select(Organization).where(Organization.id == first.id))
    ).scalar_one()
    current.lifecycle_status = "suspended"
    await db.commit()
    blocked = await client.post(
        PREFIX + "/synthetic-batches", headers=headers("c11-owner", first.id),
    )
    assert blocked.status_code == 403


@pytest.mark.asyncio
async def test_database_stores_only_digests_not_raw_evidence(client, db, orgs):
    first, _ = orgs
    batch = await make_batch(client, first)
    await make_assignments(client, first, batch)
    result = await submit(
        client, first, batch, "c11-r-a", "contradicted",
        evidence_excerpt=QUOTE,
    )
    assert result.status_code == 201
    records = (
        await db.execute(
            select(ClaimReviewEvent).where(
                ClaimReviewEvent.organization_id == first.id,
                ClaimReviewEvent.batch_id == batch,
            )
        )
    ).scalars().all()
    assert len(records) == 1
    assert len(records[0].evidence_digest) == 64
    assert QUOTE not in str(records[0].__dict__)
    assert "customer_message" not in str(records[0].__dict__)
    assert "reviewer" not in result.text
    batches = (
        await db.execute(select(ClaimReviewBatch).where(
            ClaimReviewBatch.organization_id == first.id,
            ClaimReviewBatch.id == batch,
        ))
    ).scalars().all()
    assert len(batches) == 1
    assert batches[0].dataset_id == "c6-fictional-faq-20261021"
