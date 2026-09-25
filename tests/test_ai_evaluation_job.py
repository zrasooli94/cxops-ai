"""Phase 1J.4 durable AI evaluation job tests.

Exercises the IntegrationJob machinery for the ``ai.evaluation`` job type:
enqueue validation + dedupe, worker dispatch to the (shared)
``execute_existing_*`` executors, tenant safety, retry/failure safety, and the
guarantee that job execution NEVER creates a second evaluation run.

Uses fake evaluators: no real LLM is ever called. The worker contract is
exercised the same way ``test_sla_escalation.py`` does: claim/execute/
mark_* against the repository directly.
"""

import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio
from app.core.database import AsyncSessionLocal
from app.models.ai_evaluation_case import AIEvaluationCase
from app.models.ai_evaluation_run import AIEvaluationRun
from app.models.integration_job import IntegrationJob
from app.models.organization import Organization
from app.repositories.ai_evaluation_repository import AIEvaluationRepository
from app.repositories.integration_job_repository import IntegrationJobRepository
from app.services.agent_evaluation_service import AgentEvaluationService
from app.services.ai_evaluation_service import AIEvaluationService
from app.services.integration_job_service import IntegrationJobService
from app.services.rag_evaluation_service import RAGEvaluationService
from sqlalchemy import select


@pytest_asyncio.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


async def _make_org(db, tag: str) -> Organization:
    org = Organization(name=f"eval-job-{tag}-{uuid.uuid4().hex[:8]}")
    db.add(org)
    await db.flush()
    return org


def _new_run_id(prefix: str) -> str:
    return f"run-{prefix}-{uuid.uuid4().hex}"


# ---------------------------------------------------------------------------
# RAG fakes


def _base_result(case: dict) -> dict:
    expected_sources = list(case.get("expected_sources", []) or [])
    expected_terms = list(case.get("expected_terms", []) or [])
    answer = f"the target team resolves {case['id']}"
    return {
        "id": case["id"],
        "question": case["question"],
        "passed": all(term in answer for term in expected_terms),
        "retrieval_hit": bool(expected_sources),
        "answer_correct": all(term in answer for term in expected_terms),
        "grounding_correct": True,
        "refusal_correct": None,
        "citation_valid": True,
        "grounded": True,
        "sources": list(expected_sources),
        "answer": answer,
        "best_similarity": 0.9,
        "latency_ms": 100.0,
        "total_tokens": 50,
        "estimated_cost_usd": 0.001,
    }


def _fake_rag_evaluate_case(overrides: dict[str, object] | None = None, calls: list | None = None):
    overrides = overrides or {}
    calls = calls if calls is not None else []

    async def evaluate_case(db, *, case, organization_id):
        calls.append({"case_id": case["id"], "organization_id": organization_id})
        stated = overrides.get(case["id"])
        if stated == "raise":
            raise RuntimeError("simulated evaluator failure")
        result = _base_result(case)
        if isinstance(stated, dict):
            for key, value in stated.items():
                result[key] = value
        return result

    return evaluate_case


def _rag_case(case_id: str, **extra) -> dict:
    payload = {
        "id": case_id,
        "question": f"Question text for {case_id}",
        "expected_sources": ["Policy"],
        "expected_terms": ["team"],
        "should_refuse": False,
    }
    payload.update(extra)
    return payload


# ---------------------------------------------------------------------------
# Agent fakes


def _agent_case(ticket_id: int, **extra) -> dict:
    payload = {
        "ticket_id": ticket_id,
        "expected_action": "respond",
        "expected_retrieval": True,
        "expected_tool": "zendesk.send_reply",
        "expected_auto_execute": False,
    }
    payload.update(extra)
    return payload


def _agent_base_result(
    *,
    ticket_id: int,
    expected_action: str,
    expected_retrieval: bool,
    expected_tool: str,
    expected_auto_execute: bool,
    expected_intent: str | None = None,
    expected_specialists: list[str] | None = None,
) -> dict:
    return {
        "ticket_id": ticket_id,
        "expected_action": expected_action,
        "actual_action": expected_action,
        "expected_retrieval": expected_retrieval,
        "actual_retrieval": expected_retrieval,
        "expected_tool": expected_tool,
        "actual_tools": [expected_tool],
        "expected_auto_execute": expected_auto_execute,
        "actual_auto_execute": expected_auto_execute,
        "expected_intent": expected_intent,
        "actual_intent": expected_intent,
        "intent_pass": (
            (expected_intent is not None)
            if expected_intent is not None
            else None
        ),
        "expected_specialists": expected_specialists,
        "actual_specialists": expected_specialists,
        "specialist_path_pass": (
            (expected_specialists is not None)
            if expected_specialists is not None
            else None
        ),
        "action_pass": True,
        "retrieval_pass": True,
        "tool_pass": True,
        "auto_execute_pass": True,
        "overall_pass": True,
        "latency_ms": 25.0,
        "workflow_path": ["analyze"],
        "tool_plan": [],
    }


def _fake_agent_evaluate_case(calls: list | None = None):
    calls = calls if calls is not None else []

    async def evaluate_case(
        db,
        *,
        ticket_id,
        organization_id,
        expected_action,
        expected_retrieval,
        expected_tool,
        expected_auto_execute,
        expected_intent=None,
        expected_specialists=None,
    ):
        calls.append(
            {
                "ticket_id": ticket_id,
                "organization_id": organization_id,
                "expected_tool": expected_tool,
            }
        )
        return _agent_base_result(
            ticket_id=ticket_id,
            expected_action=expected_action,
            expected_retrieval=expected_retrieval,
            expected_tool=expected_tool,
            expected_auto_execute=expected_auto_execute,
            expected_intent=expected_intent,
            expected_specialists=expected_specialists,
        )

    return evaluate_case


async def _cases_for_org(db, *, organization_id: int) -> list[AIEvaluationCase]:
    return list(
        (
            await db.execute(
                select(AIEvaluationCase).where(AIEvaluationCase.organization_id == organization_id)
            )
        ).scalars()
    )


async def _runs_for_org(db, *, organization_id: int) -> list[AIEvaluationRun]:
    return list(
        (
            await db.execute(
                select(AIEvaluationRun).where(AIEvaluationRun.organization_id == organization_id)
            )
        ).scalars()
    )


# ---------------------------------------------------------------------------
# 1 — enqueue creates an ai.evaluation job


@pytest.mark.asyncio
async def test_enqueue_creates_ai_evaluation_job(db):
    org = await _make_org(db, "enqueue")
    run_id = _new_run_id("enqueue")
    await AIEvaluationService.provision_rag_run(
        db,
        organization_id=org.id,
        cases=[_rag_case("c-1")],
        run_id=run_id,
    )

    result = await IntegrationJobService.enqueue_ai_evaluation(
        db,
        evaluation_run_id=run_id,
        target_type="rag",
        organization_id=org.id,
    )

    assert result["duplicate"] is False
    assert result["evaluation_run_id"] == run_id
    assert result["target_type"] == "rag"

    job = await IntegrationJobRepository.get_by_dedupe_key(db, f"ai-evaluation:{org.id}:{run_id}")
    assert job is not None
    assert job.id == result["job_id"]
    assert job.job_type == IntegrationJobService.AI_EVALUATION
    assert job.organization_id == org.id
    assert job.status == "pending"
    assert job.attempts == 0
    assert set(job.payload.keys()) == {"evaluation_run_id", "target_type", "request_id"}
    assert job.payload["evaluation_run_id"] == run_id
    assert job.payload["target_type"] == "rag"


# ---------------------------------------------------------------------------
# 2 — the job payload and the sanitized run.input carry no customer content


@pytest.mark.asyncio
async def test_job_payload_and_run_input_carry_no_customer_content(db):
    org = await _make_org(db, "pii")
    run_id = _new_run_id("pii")
    leaked_text = "Subject: Refund please. Body: my account password is secret"
    juicy_case = _agent_case(701, fingerprint="fp-701", expected_intent="action")
    juicy_case["subject"] = leaked_text
    juicy_case["description"] = leaked_text
    juicy_case["body"] = leaked_text

    await AIEvaluationService.provision_agent_run(
        db,
        organization_id=org.id,
        cases=[juicy_case, _agent_case(702)],
        run_id=run_id,
    )

    await IntegrationJobService.enqueue_ai_evaluation(
        db,
        evaluation_run_id=run_id,
        target_type="agent",
        organization_id=org.id,
    )

    job = await IntegrationJobRepository.get_by_dedupe_key(db, f"ai-evaluation:{org.id}:{run_id}")

    for forbidden in ("subject", "description", "body", "Secret", "Refund"):
        assert forbidden not in str(job.payload)
        assert forbidden not in str(job.payload.values())

    run = await AIEvaluationRepository.get_run_for_tenant(db, run_id=run_id, organization_id=org.id)
    assert run is not None
    stored_cases = run.input["cases"]
    assert len(stored_cases) == 2
    first = stored_cases[0]
    assert first["ticket_id"] == 701
    assert first["fingerprint"] == "fp-701"
    assert first["expected_action"] == "respond"
    # Phase 1K.2: the operator-authored intent expectation is sanitized-safe and
    # survives (a coordinator label, never raw ticket text).
    assert first["expected_intent"] == "action"
    for forbidden in ("subject", "description", "body", leaked_text):
        assert forbidden not in first
        assert forbidden not in str(run.input)
    second = stored_cases[1]
    assert set(second.keys()) == {
        "ticket_id",
        "expected_action",
        "expected_retrieval",
        "expected_tool",
        "expected_auto_execute",
    }


# ---------------------------------------------------------------------------
# 3 — dedupe: the same logical job is created once


@pytest.mark.asyncio
async def test_enqueue_deduplicates_logical_job(db):
    org = await _make_org(db, "dedupe")
    run_id = _new_run_id("dedupe")
    await AIEvaluationService.provision_rag_run(
        db, organization_id=org.id, cases=[_rag_case("c-dup")], run_id=run_id
    )

    first = await IntegrationJobService.enqueue_ai_evaluation(
        db,
        evaluation_run_id=run_id,
        target_type="rag",
        organization_id=org.id,
    )
    second = await IntegrationJobService.enqueue_ai_evaluation(
        db,
        evaluation_run_id=run_id,
        target_type="rag",
        organization_id=org.id,
    )

    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["job_id"] == first["job_id"]

    rows = (
        await db.execute(
            select(IntegrationJob).where(
                IntegrationJob.dedupe_key == f"ai-evaluation:{org.id}:{run_id}"
            )
        )
    ).scalars()
    assert len(list(rows)) == 1


# ---------------------------------------------------------------------------
# 4 — the worker's RAG dispatch executes the queued run


@pytest.mark.asyncio
async def test_rag_job_executes_queued_run(db):
    org = await _make_org(db, "rag-job")
    run_id = _new_run_id("rag-job")
    calls: list = []

    await AIEvaluationService.provision_rag_run(
        db,
        organization_id=org.id,
        cases=[_rag_case("j-r1"), _rag_case("j-r2")],
        run_id=run_id,
    )
    result = await IntegrationJobService.enqueue_ai_evaluation(
        db, evaluation_run_id=run_id, target_type="rag", organization_id=org.id
    )
    job = await IntegrationJobRepository.get_by_id_unscoped(db, result["job_id"])

    with patch.object(
        RAGEvaluationService,
        "evaluate_case",
        side_effect=_fake_rag_evaluate_case(None, calls),
    ):
        await IntegrationJobService.execute(db, job)
        await IntegrationJobRepository.mark_completed(db, job_id=job.id)

    fresh_job = await IntegrationJobRepository.get_by_id_unscoped(db, job.id)
    assert fresh_job.status == "completed"

    run = await AIEvaluationRepository.get_run_for_tenant(db, run_id=run_id, organization_id=org.id)
    assert run.status == "succeeded"
    assert run.started_at is not None
    assert run.completed_at is not None
    assert run.metrics["total_cases"] == 2
    assert run.metrics["passed_cases"] == 2

    rows = await _cases_for_org(db, organization_id=org.id)
    assert len(rows) == 2
    assert {row.case_id for row in rows} == {"j-r1", "j-r2"}
    assert all(row.run_id == run.id for row in rows)
    assert sorted(c["case_id"] for c in calls) == ["j-r1", "j-r2"]
    assert all(c["organization_id"] == org.id for c in calls)


# ---------------------------------------------------------------------------
# 5 — the worker's Agent dispatch executes the queued run


@pytest.mark.asyncio
async def test_agent_job_executes_queued_run(db):
    org = await _make_org(db, "agent-job")
    run_id = _new_run_id("agent-job")
    calls: list = []

    await AIEvaluationService.provision_agent_run(
        db,
        organization_id=org.id,
        cases=[_agent_case(801), _agent_case(802)],
        run_id=run_id,
    )
    result = await IntegrationJobService.enqueue_ai_evaluation(
        db, evaluation_run_id=run_id, target_type="agent", organization_id=org.id
    )
    job = await IntegrationJobRepository.get_by_id_unscoped(db, result["job_id"])

    with patch.object(
        AgentEvaluationService,
        "evaluate_case",
        side_effect=_fake_agent_evaluate_case(calls),
    ):
        await IntegrationJobService.execute(db, job)
        await IntegrationJobRepository.mark_completed(db, job_id=job.id)

    fresh_job = await IntegrationJobRepository.get_by_id_unscoped(db, job.id)
    assert fresh_job.status == "completed"

    run = await AIEvaluationRepository.get_run_for_tenant(db, run_id=run_id, organization_id=org.id)
    assert run.status == "succeeded"
    assert run.metrics["total"] == 2
    assert run.metrics["passed"] == 2
    assert run.agent_decision_version == "2"

    rows = await _cases_for_org(db, organization_id=org.id)
    assert len(rows) == 2
    assert {row.case_id for row in rows} == {"801", "802"}
    assert sorted(c["ticket_id"] for c in calls) == [801, 802]
    assert all(c["organization_id"] == org.id for c in calls)


# ---------------------------------------------------------------------------
# 6 — tenant safety: a foreign run can never execute


@pytest.mark.asyncio
async def test_foreign_run_cannot_execute_or_enqueue(db):
    org_a = await _make_org(db, "tenant-a")
    org_b = await _make_org(db, "tenant-b")
    foreign_run_id = _new_run_id("tenant")

    await AIEvaluationService.provision_rag_run(
        db,
        organization_id=org_b.id,
        cases=[_rag_case("secret-case")],
        run_id=foreign_run_id,
    )

    # Enqueuing org_b's run on behalf of org_a is rejected before any job.
    with pytest.raises(ValueError):
        await IntegrationJobService.enqueue_ai_evaluation(
            db,
            evaluation_run_id=foreign_run_id,
            target_type="rag",
            organization_id=org_a.id,
        )
    assert (
        await IntegrationJobRepository.get_by_dedupe_key(
            db, f"ai-evaluation:{org_a.id}:{foreign_run_id}"
        )
        is None
    )

    # A craftable cross-tenant job (org_a's job pointing at org_b's run) cannot
    # execute the foreign run: it is invisible to org_a.
    crafted = IntegrationJob(
        dedupe_key=f"ai-evaluation:{org_a.id}:{foreign_run_id}:crafted",
        job_type=IntegrationJobService.AI_EVALUATION,
        organization_id=org_a.id,
        payload={
            "evaluation_run_id": foreign_run_id,
            "target_type": "rag",
            "request_id": None,
        },
    )
    await IntegrationJobRepository.create(db, job=crafted)
    foreign_before = await AIEvaluationRepository.get_run_for_tenant(
        db, run_id=foreign_run_id, organization_id=org_b.id
    )

    with pytest.raises(RuntimeError):
        await IntegrationJobService.execute(db, crafted)

    foreign_after = await AIEvaluationRepository.get_run_for_tenant(
        db, run_id=foreign_run_id, organization_id=org_b.id
    )
    assert foreign_after.status == "queued"
    assert foreign_after.error is None
    assert foreign_after.completed_at is None
    assert {x.case_id for x in await _cases_for_org(db, organization_id=org_b.id)} == set()
    assert foreign_before.id == foreign_after.id
    # The crafted job's failure stayed out of the foreign run entirely.
    assert (
        await IntegrationJobRepository.get_by_dedupe_key(
            db, f"ai-evaluation:{org_a.id}:{foreign_run_id}:crafted"
        )
    ).last_error is None


# ---------------------------------------------------------------------------
# 7 — a succeeded run re-dispatched is a safe no-op


@pytest.mark.asyncio
async def test_succeeded_run_redispatch_is_safe_noop(db):
    org = await _make_org(db, "noop")
    run_id = _new_run_id("noop")
    calls: list = []

    await AIEvaluationService.provision_rag_run(
        db,
        organization_id=org.id,
        cases=[_rag_case("n-1"), _rag_case("n-2")],
        run_id=run_id,
    )
    result = await IntegrationJobService.enqueue_ai_evaluation(
        db, evaluation_run_id=run_id, target_type="rag", organization_id=org.id
    )
    job = await IntegrationJobRepository.get_by_id_unscoped(db, result["job_id"])

    with patch.object(
        RAGEvaluationService,
        "evaluate_case",
        side_effect=_fake_rag_evaluate_case(None, calls),
    ):
        await IntegrationJobService.execute(db, job)
        first_call_count = len(calls)

        # Simulate the worker reclaiming the same job (retry / stale recovery).
        await IntegrationJobService.execute(db, job)

    assert len(calls) == first_call_count == 2
    run = await AIEvaluationRepository.get_run_for_tenant(db, run_id=run_id, organization_id=org.id)
    assert run.status == "succeeded"
    rows = await _cases_for_org(db, organization_id=org.id)
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# 8 — failure marks the run failed and routes into the existing retry path


@pytest.mark.asyncio
async def test_failure_marks_run_failed_and_job_retries(db):
    org = await _make_org(db, "fail")
    run_id = _new_run_id("fail")

    await AIEvaluationService.provision_rag_run(
        db,
        organization_id=org.id,
        cases=[_rag_case("boom")],
        run_id=run_id,
    )
    result = await IntegrationJobService.enqueue_ai_evaluation(
        db, evaluation_run_id=run_id, target_type="rag", organization_id=org.id
    )
    job = await IntegrationJobRepository.get_by_id_unscoped(db, result["job_id"])

    with (
        patch.object(
            RAGEvaluationService,
            "evaluate_case",
            side_effect=_fake_rag_evaluate_case({"boom": "raise"}),
        ),
        pytest.raises(RuntimeError) as excinfo,
    ):
        await IntegrationJobService.execute(db, job)

    # Run: failed with a safe, type-name-only error.
    run = await AIEvaluationRepository.get_run_for_tenant(db, run_id=run_id, organization_id=org.id)
    assert run.status == "failed"
    assert run.error == "RAG evaluation failed on case boom: RuntimeError"
    assert "simulated evaluator failure" not in run.error
    assert "Question text" not in run.error
    assert await _cases_for_org(db, organization_id=org.id) == []

    # Job: the worker applies its normal retry policy to the sanitized error.
    updated = await IntegrationJobRepository.mark_failed(
        db,
        job_id=job.id,
        error_message=str(excinfo.value),
    )
    assert updated.status == "retry"
    assert "simulated evaluator failure" not in (updated.last_error or "")
    assert "Question text" not in (updated.last_error or "")
    assert "ai.evaluation run" in (updated.last_error or "")
    assert "RuntimeError" in (updated.last_error or "")


# ---------------------------------------------------------------------------
# 9 — job execution never creates a second evaluation run


@pytest.mark.asyncio
async def test_job_execution_does_not_create_second_run(db):
    org = await _make_org(db, "one-run")
    run_id = _new_run_id("one-run")

    await AIEvaluationService.provision_rag_run(
        db,
        organization_id=org.id,
        cases=[_rag_case("o-1")],
        run_id=run_id,
    )
    result = await IntegrationJobService.enqueue_ai_evaluation(
        db, evaluation_run_id=run_id, target_type="rag", organization_id=org.id
    )
    job = await IntegrationJobRepository.get_by_id_unscoped(db, result["job_id"])

    with patch.object(
        RAGEvaluationService,
        "evaluate_case",
        side_effect=_fake_rag_evaluate_case(),
    ):
        await IntegrationJobService.execute(db, job)

    runs = await _runs_for_org(db, organization_id=org.id)
    assert len(runs) == 1
    assert runs[0].run_id == run_id
    assert runs[0].status == "succeeded"
