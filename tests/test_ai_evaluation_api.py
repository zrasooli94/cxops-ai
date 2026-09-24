"""Evaluation API + RBAC + tenant-isolation + queue-behavior tests (Phase 1J.5).

Two synthetic organizations (Org A → user-alpha OWNER, user-viewer VIEWER;
Org B → user-beta OWNER) with no shared membership prove every
``/evaluations`` surface is bounded by the resolved organization and by the
``evaluation.read`` / ``evaluation.manage`` capabilities:

- reads (list / detail / cases) require ``evaluation.read``
- starting RAG / Agent evaluations requires ``evaluation.manage``
- no role grants read-without-manage (verified at capability-matrix level), so
  the deny-manage case is exercised with VIEWER, which lacks manage entirely
- tenant is derived from ``X-CXOps-Organization-ID`` + membership only; request
  bodies cannot opt into another tenant
- POST does not run evaluators: it provisions a queued run and enqueues one
  durable ``ai.evaluation`` job; no case rows are written by the request
- response payloads never expose the run ``input`` snapshot or customer data

No evaluator or decision LLM is invoked; all reads/writes go through
tenant-scoped repository calls.
"""

import os
import time
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import delete, select

os.environ["AUTH_MODE"] = "hs256"
os.environ["AUTH_JWT_SECRET"] = "z" * 32
os.environ["AUTH_JWT_ALGORITHM"] = "HS256"
os.environ["AUTH_JWT_ISSUER"] = "test-eval-api-issuer"
os.environ["AUTH_JWT_AUDIENCE"] = "test-eval-api-audience"
os.environ["AUTH_DEV_MODE"] = "False"
os.environ["ENVIRONMENT"] = "development"

from app.core.config import reset_settings_cache
from app.core.database import AsyncSessionLocal
from app.core.rbac import (
    ROLE_CAPABILITIES,
    Capability,
    OrganizationRole,
)
from app.main import app
from app.models.ai_evaluation_baseline import AIEvaluationBaseline
from app.models.ai_evaluation_case import AIEvaluationCase
from app.models.ai_evaluation_release_decision import AIEvaluationReleaseDecision
from app.models.ai_evaluation_run import AIEvaluationRun
from app.models.integration_job import IntegrationJob
from app.models.organization import Organization
from app.models.organization_membership import OrganizationMembership
from app.repositories.ai_evaluation_repository import AIEvaluationRepository
from app.services.ai_evaluation_service import AIEvaluationService

TEST_SECRET = "z" * 32
TEST_ISSUER = "test-eval-api-issuer"
TEST_AUDIENCE = "test-eval-api-audience"

USER_ALPHA = "user-alpha"
USER_BETA = "user-beta"
USER_VIEWER = "user-viewer"

X_TENANT = "X-CXOps-Organization-ID"

_RAG_SAFE_METRICS = {"citation_validity": 0.85, "grounding_accuracy": 0.85}


@pytest.fixture(autouse=True)
def _configure_auth(monkeypatch):
    values = {
        "AUTH_MODE": "hs256",
        "AUTH_JWT_SECRET": TEST_SECRET,
        "AUTH_JWT_ALGORITHM": "HS256",
        "AUTH_JWT_ISSUER": TEST_ISSUER,
        "AUTH_JWT_AUDIENCE": TEST_AUDIENCE,
        "AUTH_DEV_MODE": "False",
        "ENVIRONMENT": "development",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    reset_settings_cache()
    yield


@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


@pytest_asyncio.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


def _build_token(*, sub: str, secret: str = TEST_SECRET) -> str:
    now = int(time.time())
    payload = {
        "sub": sub,
        "email": f"{sub}@example.com",
        "iss": TEST_ISSUER,
        "aud": TEST_AUDIENCE,
        "exp": now + 3600,
        "iat": now,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def _auth_headers(sub: str, *, tenant_id: int | None = None) -> dict:
    headers = {"Authorization": f"Bearer {_build_token(sub=sub)}"}
    if tenant_id is not None:
        headers[X_TENANT] = str(tenant_id)
    return headers


@pytest_asyncio.fixture
async def two_orgs(db):
    """Org A (user-alpha OWNER, user-viewer VIEWER) and Org B (user-beta OWNER)."""
    org_ids: list[int] = []

    async def make_org(name: str) -> Organization:
        org = Organization(name=name)
        db.add(org)
        await db.flush()
        org_ids.append(org.id)
        return org

    async def add_membership(subject: str, organization_id: int, role: OrganizationRole) -> None:
        db.add(
            OrganizationMembership(
                subject=subject,
                organization_id=organization_id,
                role=role,
            )
        )
        await db.commit()

    async def make_run(organization_id: int, run_id: str | None = None) -> AIEvaluationRun:
        run = await AIEvaluationRepository.create_run(
            db,
            run_id=run_id or uuid.uuid4().hex,
            organization_id=organization_id,
            target_type="rag",
            model="test-model",
            trigger_source="manual",
            requested_by_subject="test-subject",
            input_data={
                "cases": [
                    {
                        "id": "c-1",
                        "question": "How do I reset my password?",
                        "expected_terms": ["reset"],
                    }
                ]
            },
        )
        return run

    org_a = await make_org(f"org-a-{uuid.uuid4().hex[:8]}")
    org_b = await make_org(f"org-b-{uuid.uuid4().hex[:8]}")
    await add_membership(USER_ALPHA, org_a.id, OrganizationRole.OWNER)
    await add_membership(USER_VIEWER, org_a.id, OrganizationRole.VIEWER)
    await add_membership(USER_BETA, org_b.id, OrganizationRole.OWNER)

    yield {
        "org_a": org_a,
        "org_b": org_b,
        "make_run": make_run,
    }

    if org_ids:
        await db.execute(
            delete(AIEvaluationReleaseDecision).where(
                AIEvaluationReleaseDecision.organization_id.in_(org_ids)
            )
        )
        await db.execute(
            delete(AIEvaluationCase).where(AIEvaluationCase.organization_id.in_(org_ids))
        )
        await db.execute(
            delete(AIEvaluationRun).where(AIEvaluationRun.organization_id.in_(org_ids))
        )
        await db.execute(
            delete(AIEvaluationBaseline).where(AIEvaluationBaseline.organization_id.in_(org_ids))
        )
        await db.execute(delete(IntegrationJob).where(IntegrationJob.organization_id.in_(org_ids)))
        await db.execute(
            delete(OrganizationMembership).where(
                OrganizationMembership.organization_id.in_(org_ids)
            )
        )
        await db.execute(delete(Organization).where(Organization.id.in_(org_ids)))
    await db.commit()


async def _make_succeeded_run(
    db,
    *,
    organization_id: int,
    target_type: str = "rag",
    pass_rate: float = 0.9,
    metrics: dict | None = None,
    case_count: int = 2,
    input_data: dict | None = None,
) -> AIEvaluationRun:
    run = await AIEvaluationRepository.create_run(
        db,
        run_id=uuid.uuid4().hex,
        organization_id=organization_id,
        target_type=target_type,
        model="test-model",
        embedding_model="test-embed",
        agent_decision_version="2",
        tool_policy_version=1,
        corpus_revision={"documents": 3},
        status="running",
        input_data=input_data,
    )
    for index in range(case_count):
        await AIEvaluationRepository.add_case(
            db,
            run_id=run.run_id,
            organization_id=organization_id,
            case_id=f"c-{index}-{uuid.uuid4().hex[:6]}",
            case_type=target_type,
        )
    updated = await AIEvaluationRepository.update_run(
        db,
        run_id=run.run_id,
        organization_id=organization_id,
        values={
            "status": "succeeded",
            "pass_rate": pass_rate,
            "metrics": metrics if metrics is not None else _RAG_SAFE_METRICS,
            "completed_at": datetime.now(UTC),
        },
    )
    assert updated is not None
    return updated


# ------------------------------------------------------------------ capability matrix


def test_roles_with_manage_also_have_read():
    """No role grants read-without-manage; VIEWER lacks both.

    The deny-manage API cases below are exercised with VIEWER (which cannot
    start evaluations), and a dedicated capability-matrix test in test_rbac.py
    pins the exact role mappings.
    """
    for caps in ROLE_CAPABILITIES.values():
        if Capability.EVALUATION_MANAGE in caps:
            assert Capability.EVALUATION_READ in caps
    assert Capability.EVALUATION_READ not in ROLE_CAPABILITIES[OrganizationRole.VIEWER]
    assert Capability.EVALUATION_MANAGE not in ROLE_CAPABILITIES[OrganizationRole.VIEWER]


# ------------------------------------------------------------------ reads: list / detail / cases


@pytest.mark.asyncio
async def test_list_runs_sees_only_own_tenant(client, two_orgs):
    org_a, org_b = two_orgs["org_a"], two_orgs["org_b"]
    await two_orgs["make_run"](org_a.id)
    await two_orgs["make_run"](org_a.id)
    await two_orgs["make_run"](org_b.id)

    resp = await client.get(
        "/evaluations/runs",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_a.id),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert all(item["organization_id"] == org_a.id for item in body["items"])
    assert all("input" not in item for item in body["items"])


@pytest.mark.asyncio
async def test_list_runs_viewer_denied(client, two_orgs):
    org_a = two_orgs["org_a"]
    resp = await client.get(
        "/evaluations/runs",
        headers=_auth_headers(USER_VIEWER, tenant_id=org_a.id),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Insufficient permissions"


@pytest.mark.asyncio
async def test_get_foreign_tenant_run_is_404(client, two_orgs):
    org_a, org_b = two_orgs["org_a"], two_orgs["org_b"]
    run_a = await two_orgs["make_run"](org_a.id)

    resp = await client.get(
        f"/evaluations/runs/{run_a.run_id}",
        headers=_auth_headers(USER_BETA, tenant_id=org_b.id),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_unknown_run_is_404(client, two_orgs):
    org_a = two_orgs["org_a"]
    resp = await client.get(
        "/evaluations/runs/does-not-exist",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_a.id),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_run_detail_omits_input(client, two_orgs):
    org_a = two_orgs["org_a"]
    run_a = await two_orgs["make_run"](org_a.id)

    resp = await client.get(
        f"/evaluations/runs/{run_a.run_id}",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_a.id),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == run_a.run_id
    assert body["organization_id"] == org_a.id
    assert body["status"] == "queued"
    assert "input" not in body
    text = resp.text.lower()
    assert "how do i reset my password?" not in text


@pytest.mark.asyncio
async def test_run_cases_own_tenant(client, two_orgs, db):
    org_a = two_orgs["org_a"]
    run_a = await two_orgs["make_run"](org_a.id)

    await AIEvaluationRepository.add_case(
        db,
        run_id=run_a.run_id,
        organization_id=org_a.id,
        case_id="c-1",
        case_type="rag",
        expected={"expected_terms": ["reset"]},
        actual={"answer_preview": "Go to settings > reset.", "retrieval_hit": True},
        dimensions={"correctness_pass": True},
    )

    resp = await client.get(
        f"/evaluations/runs/{run_a.run_id}/cases",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_a.id),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["case_id"] == "c-1"
    assert "answer_preview" in body["items"][0]["actual"]


@pytest.mark.asyncio
async def test_run_cases_foreign_tenant_is_404(client, two_orgs, db):
    org_a, org_b = two_orgs["org_a"], two_orgs["org_b"]
    run_a = await two_orgs["make_run"](org_a.id)
    await AIEvaluationRepository.add_case(
        db,
        run_id=run_a.run_id,
        organization_id=org_a.id,
        case_id="c-1",
        case_type="rag",
    )

    resp = await client.get(
        f"/evaluations/runs/{run_a.run_id}/cases",
        headers=_auth_headers(USER_BETA, tenant_id=org_b.id),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_run_cases_viewer_denied(client, two_orgs):
    org_a = two_orgs["org_a"]
    run_a = await two_orgs["make_run"](org_a.id)

    resp = await client.get(
        f"/evaluations/runs/{run_a.run_id}/cases",
        headers=_auth_headers(USER_VIEWER, tenant_id=org_a.id),
    )
    assert resp.status_code == 403


# ------------------------------------------------------------------ start RAG / Agent evaluations


RAG_CASE = {
    "id": "c-ok",
    "question": "Where is the reset button?",
    "expected_sources": ["help/reset.md"],
    "expected_terms": ["reset button"],
    "should_refuse": False,
}

AGENT_CASE = {
    "ticket_id": 1001,
    "expected_action": "reply",
    "expected_retrieval": True,
    "expected_tool": "sn_dev",
    "expected_auto_execute": False,
}


@pytest.mark.asyncio
async def test_start_rag_queues_run_and_job(client, two_orgs, db):
    org_a = two_orgs["org_a"]

    resp = await client.post(
        "/evaluations/runs/rag",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_a.id),
        json={"cases": [RAG_CASE]},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["target_type"] == "rag"
    assert body["status"] == "queued"
    assert body["organization_id"] == org_a.id
    assert "input" not in body
    assert body["job_status"] in {"pending", "queued", "running"}
    assert isinstance(body["job_id"], int)

    run = await AIEvaluationRepository.get_run_for_tenant(
        db, run_id=body["run_id"], organization_id=org_a.id
    )
    assert run is not None
    assert run.status == "queued"
    assert run.requested_by_subject == USER_ALPHA

    job = (
        (await db.execute(select(IntegrationJob).where(IntegrationJob.organization_id == org_a.id)))
        .scalars()
        .all()
    )
    assert len(job) == 1
    assert job[0].job_type == "ai.evaluation"
    assert job[0].payload["evaluation_run_id"] == run.run_id
    assert job[0].payload["target_type"] == "rag"

    case_rows = (
        (
            await db.execute(
                select(AIEvaluationCase).where(AIEvaluationCase.organization_id == org_a.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(case_rows) == 0


@pytest.mark.asyncio
async def test_start_agent_queues_run_and_job(client, two_orgs, db):
    org_a = two_orgs["org_a"]

    resp = await client.post(
        "/evaluations/runs/agent",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_a.id),
        json={"cases": [AGENT_CASE]},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["target_type"] == "agent"
    assert body["status"] == "queued"

    run = await AIEvaluationRepository.get_run_for_tenant(
        db, run_id=body["run_id"], organization_id=org_a.id
    )
    assert run is not None
    assert run.target_type == "agent"

    job = (
        (await db.execute(select(IntegrationJob).where(IntegrationJob.organization_id == org_a.id)))
        .scalars()
        .all()
    )
    assert len(job) == 1
    assert job[0].job_type == "ai.evaluation"
    assert job[0].payload["target_type"] == "agent"

    case_rows = (
        (
            await db.execute(
                select(AIEvaluationCase).where(AIEvaluationCase.organization_id == org_a.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(case_rows) == 0


@pytest.mark.asyncio
async def test_start_requires_manage_viewer_denied(client, two_orgs):
    org_a = two_orgs["org_a"]

    for path in ("/evaluations/runs/rag", "/evaluations/runs/agent"):
        resp = await client.post(
            path,
            headers=_auth_headers(USER_VIEWER, tenant_id=org_a.id),
            json={"cases": [RAG_CASE]},
        )
        assert resp.status_code == 403, path
        assert resp.json()["detail"] == "Insufficient permissions"


@pytest.mark.asyncio
async def test_start_tenant_never_from_body(client, two_orgs, db):
    org_a, org_b = two_orgs["org_a"], two_orgs["org_b"]

    resp = await client.post(
        "/evaluations/runs/rag",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_a.id),
        json={"cases": [RAG_CASE], "organization_id": org_b.id},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_repeated_start_provisions_independent_runs(client, two_orgs, db):
    org_a = two_orgs["org_a"]
    payload = {"cases": [RAG_CASE]}

    resp1 = await client.post(
        "/evaluations/runs/rag",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_a.id),
        json=payload,
    )
    assert resp1.status_code == 202
    run_id_1 = resp1.json()["run_id"]

    resp2 = await client.post(
        "/evaluations/runs/rag",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_a.id),
        json=payload,
    )
    assert resp2.status_code == 202
    assert resp2.json()["run_id"] != run_id_1

    runs = (
        (
            await db.execute(
                select(AIEvaluationRun).where(AIEvaluationRun.organization_id == org_a.id)
            )
        )
        .scalars()
        .all()
    )
    jobs = (
        (await db.execute(select(IntegrationJob).where(IntegrationJob.organization_id == org_a.id)))
        .scalars()
        .all()
    )
    assert len(runs) == 2
    assert len(jobs) == 2


# ------------------------------------------------------------------ input limits + extra fields


@pytest.mark.asyncio
async def test_rejects_zero_cases(client, two_orgs):
    org_a = two_orgs["org_a"]

    for path in ("/evaluations/runs/rag", "/evaluations/runs/agent"):
        resp = await client.post(
            path,
            headers=_auth_headers(USER_ALPHA, tenant_id=org_a.id),
            json={"cases": []},
        )
        assert resp.status_code == 422, path


@pytest.mark.asyncio
async def test_rejects_oversized_case_list(client, two_orgs):
    org_a = two_orgs["org_a"]
    many_cases = [dict(RAG_CASE, id=f"c-{i}") for i in range(101)]

    resp = await client.post(
        "/evaluations/runs/rag",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_a.id),
        json={"cases": many_cases},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_rejects_extra_fields_fail_closed(client, two_orgs):
    org_a = two_orgs["org_a"]

    customer_poisoned = dict(RAG_CASE)
    customer_poisoned["email"] = "customer@example.com"
    customer_poisoned["phone"] = "+15551234567"
    resp = await client.post(
        "/evaluations/runs/rag",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_a.id),
        json={"cases": [customer_poisoned]},
    )
    assert resp.status_code == 422

    agent_poisoned = dict(AGENT_CASE)
    agent_poisoned["ticket_body"] = "secret customer message"
    resp = await client.post(
        "/evaluations/runs/agent",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_a.id),
        json={"cases": [agent_poisoned]},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_no_pii_in_response_or_queue(client, two_orgs, db):
    org_a = two_orgs["org_a"]
    poisoned = dict(RAG_CASE, question="My secret: reset every Friday 9am.")

    resp = await client.post(
        "/evaluations/runs/rag",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_a.id),
        json={"cases": [poisoned]},
    )
    assert resp.status_code == 202

    run_id = resp.json()["run_id"]
    run = await AIEvaluationRepository.get_run_for_tenant(
        db, run_id=run_id, organization_id=org_a.id
    )
    assert run is not None
    payload = run.input.get("cases", [{}])[0]
    assert payload.keys() <= {
        "id",
        "question",
        "expected_sources",
        "expected_terms",
        "should_refuse",
        "fingerprint",
    }

    stacked = (
        (await db.execute(select(IntegrationJob).where(IntegrationJob.organization_id == org_a.id)))
        .scalars()
        .all()
    )
    job_payload = stacked[0].payload
    assert job_payload.keys() <= {"evaluation_run_id", "target_type", "request_id"}

    resp = await client.get(
        f"/evaluations/runs/{run_id}",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_a.id),
    )
    assert resp.status_code == 200
    assert "input" not in resp.json()


# ------------------------------------------------------------------ baselines + comparison (1J.7B)


@pytest.mark.asyncio
async def test_list_baselines_sees_only_own_tenant(client, two_orgs, db):
    org_a, org_b = two_orgs["org_a"], two_orgs["org_b"]
    run_a = await _make_succeeded_run(db, organization_id=org_a.id, pass_rate=0.9)
    await AIEvaluationService.create_baseline_from_run(
        db, organization_id=org_a.id, run_id=run_a.run_id
    )
    run_b = await _make_succeeded_run(db, organization_id=org_b.id, pass_rate=0.9)
    await AIEvaluationService.create_baseline_from_run(
        db, organization_id=org_b.id, run_id=run_b.run_id
    )

    resp = await client.get(
        "/evaluations/baselines",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_a.id),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["organization_id"] == org_a.id
    assert item["version"] == "1"
    assert item["promoted"] is True


@pytest.mark.asyncio
async def test_list_baselines_filters_by_target_type(client, two_orgs, db):
    org_a = two_orgs["org_a"]
    rag_run = await _make_succeeded_run(db, organization_id=org_a.id, target_type="rag")
    await AIEvaluationService.create_baseline_from_run(
        db, organization_id=org_a.id, run_id=rag_run.run_id
    )
    agent_run = await _make_succeeded_run(db, organization_id=org_a.id, target_type="agent")
    await AIEvaluationService.create_baseline_from_run(
        db, organization_id=org_a.id, run_id=agent_run.run_id
    )

    resp = await client.get(
        "/evaluations/baselines?target_type=agent",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_a.id),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["target_type"] == "agent"


@pytest.mark.asyncio
async def test_list_baselines_viewer_denied(client, two_orgs):
    org_a = two_orgs["org_a"]
    resp = await client.get(
        "/evaluations/baselines",
        headers=_auth_headers(USER_VIEWER, tenant_id=org_a.id),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Insufficient permissions"


@pytest.mark.asyncio
async def test_create_baseline_from_succeeded_run(client, two_orgs, db):
    org_a = two_orgs["org_a"]
    run_a = await _make_succeeded_run(
        db,
        organization_id=org_a.id,
        pass_rate=0.92,
        metrics={"retrieval_accuracy": 1.0},
        case_count=3,
    )

    resp = await client.post(
        f"/evaluations/runs/{run_a.run_id}/baseline",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_a.id),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["organization_id"] == org_a.id
    assert body["target_type"] == "rag"
    assert body["version"] == "1"
    assert body["model"] == "test-model"
    assert body["pass_rate"] == 0.92
    assert body["metrics"] == {"retrieval_accuracy": 1.0}
    assert body["cases_count"] == 3
    assert body["promoted"] is True
    assert body["created_by_subject"] == USER_ALPHA
    assert "input" not in body


@pytest.mark.asyncio
async def test_create_baseline_queued_run_is_400(client, two_orgs):
    org_a = two_orgs["org_a"]
    queued = await two_orgs["make_run"](org_a.id)

    resp = await client.post(
        f"/evaluations/runs/{queued.run_id}/baseline",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_a.id),
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_baseline_failed_run_is_400(client, two_orgs, db):
    org_a = two_orgs["org_a"]
    failed = await AIEvaluationRepository.create_run(
        db,
        run_id=uuid.uuid4().hex,
        organization_id=org_a.id,
        target_type="rag",
        model="test-model",
        status="failed",
    )

    resp = await client.post(
        f"/evaluations/runs/{failed.run_id}/baseline",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_a.id),
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_baseline_viewer_denied(client, two_orgs, db):
    org_a = two_orgs["org_a"]
    run_a = await _make_succeeded_run(db, organization_id=org_a.id)

    resp = await client.post(
        f"/evaluations/runs/{run_a.run_id}/baseline",
        headers=_auth_headers(USER_VIEWER, tenant_id=org_a.id),
    )
    assert resp.status_code == 403

    remaining = await AIEvaluationRepository.list_baselines_for_tenant(db, organization_id=org_a.id)
    assert remaining == []


@pytest.mark.asyncio
async def test_create_baseline_versions_increment_and_history_kept(client, two_orgs, db):
    org_a = two_orgs["org_a"]
    run_one = await _make_succeeded_run(db, organization_id=org_a.id, pass_rate=0.8)
    run_two = await _make_succeeded_run(db, organization_id=org_a.id, pass_rate=0.9)

    resp_one = await client.post(
        f"/evaluations/runs/{run_one.run_id}/baseline",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_a.id),
    )
    assert resp_one.status_code == 201
    assert resp_one.json()["version"] == "1"

    resp_two = await client.post(
        f"/evaluations/runs/{run_two.run_id}/baseline",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_a.id),
    )
    assert resp_two.status_code == 201
    assert resp_two.json()["version"] == "2"

    history = await client.get(
        "/evaluations/baselines",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_a.id),
    )
    assert history.status_code == 200
    assert sorted(b["version"] for b in history.json()["items"]) == ["1", "2"]


@pytest.mark.asyncio
async def test_create_baseline_foreign_run_is_404(client, two_orgs, db):
    org_a, org_b = two_orgs["org_a"], two_orgs["org_b"]
    run_b = await _make_succeeded_run(db, organization_id=org_b.id)

    resp = await client.post(
        f"/evaluations/runs/{run_b.run_id}/baseline",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_a.id),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_compare_same_target_type_returns_comparison(client, two_orgs, db):
    org_a = two_orgs["org_a"]
    baseline_run = await _make_succeeded_run(
        db,
        organization_id=org_a.id,
        pass_rate=0.80,
        metrics={"retrieval_accuracy": 0.9, "answer_correctness": 0.9},
    )
    baseline = await AIEvaluationService.create_baseline_from_run(
        db, organization_id=org_a.id, run_id=baseline_run.run_id
    )
    candidate = await _make_succeeded_run(
        db,
        organization_id=org_a.id,
        pass_rate=0.90,
        metrics={"retrieval_accuracy": 1.0, "answer_correctness": 0.9},
    )

    resp = await client.get(
        f"/evaluations/runs/{candidate.run_id}/compare/{baseline.id}",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_a.id),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["target_type"] == "rag"
    assert body["candidate_pass_rate"] == 0.90
    assert body["baseline_pass_rate"] == 0.80
    assert body["pass_rate_delta"] == 0.10
    by_metric = {m["metric"]: m for m in body["metrics"]}
    assert by_metric["retrieval_accuracy"]["direction"] == "improved"
    assert by_metric["answer_correctness"]["direction"] == "same"
    assert body["baseline"]["model"] == "test-model"
    assert body["baseline"]["agent_decision_version"] == "2"
    assert body["baseline"]["tool_policy_version"] == 1
    assert body["baseline"]["corpus_revision"] == {"documents": 3}
    assert body["candidate"]["model"] == "test-model"
    assert "input" not in body
    assert "run.input" not in resp.text


@pytest.mark.asyncio
async def test_compare_viewer_denied(client, two_orgs, db):
    org_a = two_orgs["org_a"]
    baseline_run = await _make_succeeded_run(db, organization_id=org_a.id)
    baseline = await AIEvaluationService.create_baseline_from_run(
        db, organization_id=org_a.id, run_id=baseline_run.run_id
    )
    candidate = await _make_succeeded_run(db, organization_id=org_a.id)

    resp = await client.get(
        f"/evaluations/runs/{candidate.run_id}/compare/{baseline.id}",
        headers=_auth_headers(USER_VIEWER, tenant_id=org_a.id),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_compare_different_target_type_is_400(client, two_orgs, db):
    org_a = two_orgs["org_a"]
    rag_run = await _make_succeeded_run(db, organization_id=org_a.id, target_type="rag")
    baseline = await AIEvaluationService.create_baseline_from_run(
        db, organization_id=org_a.id, run_id=rag_run.run_id
    )
    agent_run = await _make_succeeded_run(db, organization_id=org_a.id, target_type="agent")

    resp = await client.get(
        f"/evaluations/runs/{agent_run.run_id}/compare/{baseline.id}",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_a.id),
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_compare_unknown_run_is_404(client, two_orgs, db):
    org_a = two_orgs["org_a"]
    baseline_run = await _make_succeeded_run(db, organization_id=org_a.id)
    baseline = await AIEvaluationService.create_baseline_from_run(
        db, organization_id=org_a.id, run_id=baseline_run.run_id
    )

    resp = await client.get(
        f"/evaluations/runs/does-not-exist/compare/{baseline.id}",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_a.id),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_compare_foreign_run_is_404(client, two_orgs, db):
    org_a, org_b = two_orgs["org_a"], two_orgs["org_b"]
    baseline_run_a = await _make_succeeded_run(db, organization_id=org_a.id)
    baseline_a = await AIEvaluationService.create_baseline_from_run(
        db, organization_id=org_a.id, run_id=baseline_run_a.run_id
    )
    run_b = await _make_succeeded_run(db, organization_id=org_b.id)

    resp = await client.get(
        f"/evaluations/runs/{run_b.run_id}/compare/{baseline_a.id}",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_a.id),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_compare_foreign_baseline_is_404(client, two_orgs, db):
    org_a, org_b = two_orgs["org_a"], two_orgs["org_b"]
    run_a = await _make_succeeded_run(db, organization_id=org_a.id)
    baseline_run_b = await _make_succeeded_run(db, organization_id=org_b.id)
    baseline_b = await AIEvaluationService.create_baseline_from_run(
        db, organization_id=org_b.id, run_id=baseline_run_b.run_id
    )

    resp = await client.get(
        f"/evaluations/runs/{run_a.run_id}/compare/{baseline_b.id}",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_a.id),
    )
    assert resp.status_code == 404


# ------------------------------------------------------- release decisions (1J.8B)


async def _make_baseline_for_org(
    db,
    *,
    organization_id: int,
    run: AIEvaluationRun | None = None,
) -> AIEvaluationBaseline:
    run = run or await _make_succeeded_run(db, organization_id=organization_id)
    return await AIEvaluationService.create_baseline_from_run(
        db, organization_id=organization_id, run_id=run.run_id
    )


async def _post_release_decision(
    client,
    *,
    run,
    baseline,
    organization_id: int,
    decision: str = "approved",
    sub: str = USER_ALPHA,
    note: str | None = None,
    **body_overrides,
):
    payload: dict = {"baseline_id": baseline.id, "decision": decision}
    if note is not None:
        payload["note"] = note
    payload.update(body_overrides)
    return await client.post(
        f"/evaluations/runs/{run.run_id}/release-decision",
        headers=_auth_headers(sub, tenant_id=organization_id),
        json=payload,
    )


@pytest.mark.asyncio
async def test_manage_can_approve_release_decision(client, two_orgs, db):
    org_a = two_orgs["org_a"]
    run_a = await _make_succeeded_run(db, organization_id=org_a.id, pass_rate=0.9)
    baseline = await _make_baseline_for_org(db, organization_id=org_a.id, run=run_a)

    resp = await _post_release_decision(
        client, run=run_a, baseline=baseline, organization_id=org_a.id
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["decision"] == "approved"
    assert body["baseline_id"] == baseline.id
    assert body["candidate_run_id"] == run_a.id
    assert body["organization_id"] == org_a.id
    assert body["decided_by_subject"] == USER_ALPHA
    assert body["comparison_snapshot"]["candidate_pass_rate"] == 0.9
    assert "input" not in body


@pytest.mark.asyncio
async def test_manage_can_reject_release_decision_with_note(client, two_orgs, db):
    org_a = two_orgs["org_a"]
    run_a = await _make_succeeded_run(db, organization_id=org_a.id)
    baseline = await _make_baseline_for_org(db, organization_id=org_a.id, run=run_a)

    resp = await _post_release_decision(
        client,
        run=run_a,
        baseline=baseline,
        organization_id=org_a.id,
        decision="rejected",
        note="Retrieval metric regressed below threshold.",
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["decision"] == "rejected"
    assert body["note"] == "Retrieval metric regressed below threshold."


@pytest.mark.asyncio
async def test_read_only_role_cannot_create_release_decision(client, two_orgs, db):
    org_a = two_orgs["org_a"]
    run_a = await _make_succeeded_run(db, organization_id=org_a.id)
    baseline = await _make_baseline_for_org(db, organization_id=org_a.id, run=run_a)

    resp = await _post_release_decision(
        client, run=run_a, baseline=baseline, organization_id=org_a.id, sub=USER_VIEWER
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Insufficient permissions"


@pytest.mark.asyncio
async def test_viewer_cannot_create_and_no_decision_written(client, two_orgs, db):
    org_a = two_orgs["org_a"]
    run_a = await _make_succeeded_run(db, organization_id=org_a.id)
    baseline = await _make_baseline_for_org(db, organization_id=org_a.id, run=run_a)

    resp = await _post_release_decision(
        client, run=run_a, baseline=baseline, organization_id=org_a.id, sub=USER_VIEWER
    )
    assert resp.status_code == 403

    remaining = await AIEvaluationRepository.list_release_decisions_for_tenant(
        db, organization_id=org_a.id
    )
    assert remaining == []


@pytest.mark.asyncio
async def test_decided_by_subject_comes_from_auth_context(client, two_orgs, db):
    org_a = two_orgs["org_a"]
    run_a = await _make_succeeded_run(db, organization_id=org_a.id)
    baseline = await _make_baseline_for_org(db, organization_id=org_a.id, run=run_a)

    resp = await _post_release_decision(
        client, run=run_a, baseline=baseline, organization_id=org_a.id
    )
    assert resp.status_code == 201
    assert resp.json()["decided_by_subject"] == "user-alpha"


@pytest.mark.asyncio
async def test_client_cannot_override_decided_by_subject(client, two_orgs, db):
    org_a = two_orgs["org_a"]
    run_a = await _make_succeeded_run(db, organization_id=org_a.id)
    baseline = await _make_baseline_for_org(db, organization_id=org_a.id, run=run_a)

    resp = await _post_release_decision(
        client,
        run=run_a,
        baseline=baseline,
        organization_id=org_a.id,
        decided_by_subject="impostor@example.com",
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_client_cannot_send_organization_id(client, two_orgs, db):
    org_a, org_b = two_orgs["org_a"], two_orgs["org_b"]
    run_a = await _make_succeeded_run(db, organization_id=org_a.id)
    baseline = await _make_baseline_for_org(db, organization_id=org_a.id, run=run_a)

    resp = await client.post(
        f"/evaluations/runs/{run_a.run_id}/release-decision",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_a.id),
        json={"baseline_id": baseline.id, "decision": "approved", "organization_id": org_b.id},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_client_cannot_send_comparison_snapshot(client, two_orgs, db):
    org_a = two_orgs["org_a"]
    run_a = await _make_succeeded_run(db, organization_id=org_a.id)
    baseline = await _make_baseline_for_org(db, organization_id=org_a.id, run=run_a)

    resp = await _post_release_decision(
        client,
        run=run_a,
        baseline=baseline,
        organization_id=org_a.id,
        comparison_snapshot={"forged": True},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_foreign_candidate_is_404(client, two_orgs, db):
    org_a, org_b = two_orgs["org_a"], two_orgs["org_b"]
    run_a = await _make_succeeded_run(db, organization_id=org_a.id)
    baseline = await _make_baseline_for_org(db, organization_id=org_a.id, run=run_a)
    run_b = await _make_succeeded_run(db, organization_id=org_b.id)

    resp = await _post_release_decision(
        client, run=run_b, baseline=baseline, organization_id=org_a.id
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_foreign_baseline_is_404(client, two_orgs, db):
    org_a, org_b = two_orgs["org_a"], two_orgs["org_b"]
    run_a = await _make_succeeded_run(db, organization_id=org_a.id)
    baseline_b = await _make_baseline_for_org(db, organization_id=org_b.id)

    resp = await _post_release_decision(
        client, run=run_a, baseline=baseline_b, organization_id=org_a.id
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_queued_candidate_is_400(client, two_orgs, db):
    org_a = two_orgs["org_a"]
    queued_run = await two_orgs["make_run"](org_a.id)
    baseline_run = await _make_succeeded_run(db, organization_id=org_a.id)
    baseline = await _make_baseline_for_org(db, organization_id=org_a.id, run=baseline_run)

    resp = await _post_release_decision(
        client, run=queued_run, baseline=baseline, organization_id=org_a.id
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_different_target_type_is_400(client, two_orgs, db):
    org_a = two_orgs["org_a"]
    rag_run = await _make_succeeded_run(
        db, organization_id=org_a.id, target_type="rag", pass_rate=0.5
    )
    baseline = await _make_baseline_for_org(db, organization_id=org_a.id, run=rag_run)
    agent_run = await _make_succeeded_run(
        db, organization_id=org_a.id, target_type="agent", pass_rate=0.5
    )

    resp = await _post_release_decision(
        client, run=agent_run, baseline=baseline, organization_id=org_a.id
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_invalid_decision_is_422(client, two_orgs, db):
    org_a = two_orgs["org_a"]
    run_a = await _make_succeeded_run(db, organization_id=org_a.id)
    baseline = await _make_baseline_for_org(db, organization_id=org_a.id, run=run_a)

    resp = await _post_release_decision(
        client, run=run_a, baseline=baseline, organization_id=org_a.id, decision="maybe"
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_note_too_long_is_422(client, two_orgs, db):
    org_a = two_orgs["org_a"]
    run_a = await _make_succeeded_run(db, organization_id=org_a.id)
    baseline = await _make_baseline_for_org(db, organization_id=org_a.id, run=run_a)

    resp = await _post_release_decision(
        client,
        run=run_a,
        baseline=baseline,
        organization_id=org_a.id,
        note="x" * 1001,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_release_decisions_sees_only_own_tenant(client, two_orgs, db):
    org_a, org_b = two_orgs["org_a"], two_orgs["org_b"]
    run_a = await _make_succeeded_run(db, organization_id=org_a.id, pass_rate=0.8)
    baseline_a = await _make_baseline_for_org(db, organization_id=org_a.id, run=run_a)
    run_b = await _make_succeeded_run(db, organization_id=org_b.id, pass_rate=0.7)
    baseline_b = await _make_baseline_for_org(db, organization_id=org_b.id, run=run_b)

    await _post_release_decision(client, run=run_a, baseline=baseline_a, organization_id=org_a.id)
    await _post_release_decision(
        client, run=run_b, baseline=baseline_b, organization_id=org_b.id, sub=USER_BETA
    )

    resp = await client.get(
        "/evaluations/release-decisions",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_a.id),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert all(item["organization_id"] == org_a.id for item in body["items"])


@pytest.mark.asyncio
async def test_old_decisions_remain_after_second_decision(client, two_orgs, db):
    org_a = two_orgs["org_a"]
    run_a = await _make_succeeded_run(db, organization_id=org_a.id)
    baseline = await _make_baseline_for_org(db, organization_id=org_a.id, run=run_a)

    first = await _post_release_decision(
        client, run=run_a, baseline=baseline, organization_id=org_a.id
    )
    assert first.status_code == 201
    second = await _post_release_decision(
        client,
        run=run_a,
        baseline=baseline,
        organization_id=org_a.id,
        decision="rejected",
        note="Second look after fixing retrieval.",
    )
    assert second.status_code == 201

    history = await client.get(
        "/evaluations/release-decisions",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_a.id),
    )
    assert history.status_code == 200
    items = history.json()["items"]
    assert history.json()["total"] == 2
    assert {item["id"] for item in items} == {first.json()["id"], second.json()["id"]}
    assert {item["decision"] for item in items} == {"approved", "rejected"}


@pytest.mark.asyncio
async def test_list_release_decisions_viewer_denied(client, two_orgs):
    org_a = two_orgs["org_a"]
    resp = await client.get(
        "/evaluations/release-decisions",
        headers=_auth_headers(USER_VIEWER, tenant_id=org_a.id),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_release_decision_detail_and_foreign_404(client, two_orgs, db):
    org_a, org_b = two_orgs["org_a"], two_orgs["org_b"]
    run_a = await _make_succeeded_run(db, organization_id=org_a.id)
    baseline = await _make_baseline_for_org(db, organization_id=org_a.id, run=run_a)
    created = await _post_release_decision(
        client, run=run_a, baseline=baseline, organization_id=org_a.id
    )
    created_id = created.json()["id"]

    resp = await client.get(
        f"/evaluations/release-decisions/{created_id}",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_a.id),
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == created_id
    assert resp.json()["decision"] == "approved"

    foreign = await client.get(
        f"/evaluations/release-decisions/{created_id}",
        headers=_auth_headers(USER_BETA, tenant_id=org_b.id),
    )
    assert foreign.status_code == 404

    missing = await client.get(
        "/evaluations/release-decisions/99999999",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_a.id),
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_release_decision_response_exposes_no_run_input_or_pii(client, two_orgs, db):
    org_a = two_orgs["org_a"]
    secret_question = "My secret: reset every Friday 9am."
    run_a = await _make_succeeded_run(
        db,
        organization_id=org_a.id,
        pass_rate=0.9,
        input_data={
            "cases": [
                {
                    "id": "c-1",
                    "question": secret_question,
                    "expected_terms": ["reset"],
                }
            ]
        },
    )
    baseline = await _make_baseline_for_org(db, organization_id=org_a.id, run=run_a)

    resp = await _post_release_decision(
        client, run=run_a, baseline=baseline, organization_id=org_a.id
    )
    assert resp.status_code == 201
    assert "input" not in resp.json()
    assert secret_question.lower() not in resp.text.lower()

    history = await client.get(
        "/evaluations/release-decisions",
        headers=_auth_headers(USER_ALPHA, tenant_id=org_a.id),
    )
    assert history.status_code == 200
    assert "input" not in history.json()["items"][0]
    assert secret_question.lower() not in history.text.lower()


# ------------------------------------------- gate enforcement on approve (1J.9B)


async def _blocked_rag_run_and_baseline(db, *, organization_id: int):
    """A baseline with safe critical metrics plus a candidate that regresses
    ``citation_validity`` below it (the gate must block this approval)."""
    baseline_run = await _make_succeeded_run(
        db, organization_id=organization_id, metrics=dict(_RAG_SAFE_METRICS)
    )
    baseline = await AIEvaluationService.create_baseline_from_run(
        db, organization_id=organization_id, run_id=baseline_run.run_id
    )
    regressed = dict(_RAG_SAFE_METRICS)
    regressed["citation_validity"] = 0.5
    candidate = await _make_succeeded_run(db, organization_id=organization_id, metrics=regressed)
    return candidate, baseline


@pytest.mark.asyncio
async def test_approve_with_critical_regression_is_409(client, two_orgs, db):
    org_a = two_orgs["org_a"]
    candidate, baseline = await _blocked_rag_run_and_baseline(db, organization_id=org_a.id)

    resp = await _post_release_decision(
        client, run=candidate, baseline=baseline, organization_id=org_a.id
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_blocked_approve_response_contains_safe_stable_message(client, two_orgs, db):
    org_a = two_orgs["org_a"]
    candidate, baseline = await _blocked_rag_run_and_baseline(db, organization_id=org_a.id)

    resp = await _post_release_decision(
        client, run=candidate, baseline=baseline, organization_id=org_a.id
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Approval blocked by critical evaluation regression."
    assert "citation_validity" not in resp.text
    assert "regressed" not in resp.text
    assert "0.5" not in resp.text
    assert "0.85" not in resp.text


@pytest.mark.asyncio
async def test_blocked_approve_writes_no_decision(client, two_orgs, db):
    org_a = two_orgs["org_a"]
    candidate, baseline = await _blocked_rag_run_and_baseline(db, organization_id=org_a.id)

    resp = await _post_release_decision(
        client, run=candidate, baseline=baseline, organization_id=org_a.id
    )
    assert resp.status_code == 409

    remaining = await AIEvaluationRepository.list_release_decisions_for_tenant(
        db, organization_id=org_a.id
    )
    assert remaining == []


@pytest.mark.asyncio
async def test_reject_on_same_blocked_comparison_is_201(client, two_orgs, db):
    org_a = two_orgs["org_a"]
    candidate, baseline = await _blocked_rag_run_and_baseline(db, organization_id=org_a.id)

    resp = await _post_release_decision(
        client,
        run=candidate,
        baseline=baseline,
        organization_id=org_a.id,
        decision="rejected",
        note="Reviewer rejects despite or because of the regression.",
    )
    assert resp.status_code == 201
    assert resp.json()["decision"] == "rejected"

    remaining = await AIEvaluationRepository.list_release_decisions_for_tenant(
        db, organization_id=org_a.id
    )
    assert len(remaining) == 1


@pytest.mark.asyncio
async def test_blocked_approve_authorization_unchanged(client, two_orgs, db):
    org_a = two_orgs["org_a"]
    candidate, baseline = await _blocked_rag_run_and_baseline(db, organization_id=org_a.id)

    resp = await _post_release_decision(
        client,
        run=candidate,
        baseline=baseline,
        organization_id=org_a.id,
        sub=USER_VIEWER,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Insufficient permissions"

    remaining = await AIEvaluationRepository.list_release_decisions_for_tenant(
        db, organization_id=org_a.id
    )
    assert remaining == []
