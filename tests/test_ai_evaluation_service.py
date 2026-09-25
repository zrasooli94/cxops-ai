"""Phase 1J persisted evaluation service tests (RAG 1J.2, Agent 1J.3).

Uses fake evaluators: no real LLM is ever called.
"""

import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.ai_evaluation_case import AIEvaluationCase
from app.models.organization import Organization
from app.repositories.ai_evaluation_repository import AIEvaluationRepository
from app.services.agent_evaluation_service import AgentEvaluationService
from app.services.ai_evaluation_service import AIEvaluationService
from app.services.rag_evaluation_service import RAGEvaluationService
from sqlalchemy import select


@pytest_asyncio.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


async def _make_org(db, tag: str) -> Organization:
    org = Organization(name=f"eval-svc-{tag}-{uuid.uuid4().hex[:8]}")
    db.add(org)
    await db.flush()
    return org


def _base_result(case: dict) -> dict:
    refuse = bool(case.get("should_refuse", False))
    expected_sources = list(case.get("expected_sources", []) or [])
    expected_terms = list(case.get("expected_terms", []) or [])
    answer = f"the target team resolves {case['id']}"

    if refuse:
        retrieval_hit = len(expected_sources) == 0
        answer_correct = True
        refusal_correct = True
        grounded = False
    else:
        retrieval_hit = bool(expected_sources)
        answer_correct = all(term in answer for term in expected_terms)
        refusal_correct = None
        grounded = True

    return {
        "id": case["id"],
        "question": case["question"],
        "passed": all(
            [
                retrieval_hit,
                answer_correct,
                True,
                True,
                refusal_correct if refusal_correct is not None else True,
            ]
        ),
        "retrieval_hit": retrieval_hit,
        "answer_correct": answer_correct,
        "grounding_correct": True,
        "refusal_correct": refusal_correct,
        "citation_valid": True,
        "grounded": grounded,
        "sources": list(expected_sources),
        "answer": answer,
        "best_similarity": 0.9,
        "latency_ms": 100.0,
        "total_tokens": 50,
        "estimated_cost_usd": 0.001,
    }


def _fake_evaluate_case(overrides: dict[str, object] | None = None):
    overrides = overrides or {}

    async def evaluate_case(db, *, case, organization_id):
        stated = overrides.get(case["id"])
        if stated is None:
            return _base_result(case)
        if stated == "raise":
            raise RuntimeError("simulated evaluator failure")
        result = _base_result(case)
        for key, value in stated.items():
            result[key] = value
        return result

    return evaluate_case


def _rag_case(case_id: str, **extra) -> dict:
    payload = {
        "id": case_id,
        "question": f"Question text for {case_id}",
        "expected_sources": [] if extra.get("should_refuse") else ["Policy"],
        "expected_terms": ["team"],
        "should_refuse": False,
    }
    payload.update(extra)
    return payload


async def _cases_for_org(db, *, organization_id: int) -> list[AIEvaluationCase]:
    return list(
        (
            await db.execute(
                select(AIEvaluationCase).where(AIEvaluationCase.organization_id == organization_id)
            )
        ).scalars()
    )


# ---------------------------------------------------------------------------
# Agent evaluation fakes


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
        "actual_intent": (expected_intent),
        "intent_pass": (
            (expected_intent is not None)
            if expected_intent is not None
            else None
        ),
        "expected_specialists": expected_specialists,
        "actual_specialists": (expected_specialists),
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


def _fake_agent_evaluate_case(
    overrides: dict[int, object] | None = None,
    calls: list[dict] | None = None,
):
    overrides = overrides or {}
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
                "expected_action": expected_action,
                "expected_retrieval": expected_retrieval,
                "expected_tool": expected_tool,
                "expected_auto_execute": expected_auto_execute,
                "expected_intent": expected_intent,
                "expected_specialists": expected_specialists,
            }
        )

        stated = overrides.get(ticket_id)
        if stated == "raise":
            raise RuntimeError("simulated agent evaluator failure")

        result = _agent_base_result(
            ticket_id=ticket_id,
            expected_action=expected_action,
            expected_retrieval=expected_retrieval,
            expected_tool=expected_tool,
            expected_auto_execute=expected_auto_execute,
            expected_intent=expected_intent,
            expected_specialists=expected_specialists,
        )
        if isinstance(stated, dict):
            for key, value in stated.items():
                result[key] = value
        return result

    return evaluate_case


# ---------------------------------------------------------------------------
# Agent: 1, 3, 4 — run created, metrics, succeeded


@pytest.mark.asyncio
async def test_agent_run_created_persists_metrics_and_succeeds(db):
    org = await _make_org(db, "agent-ok")
    cases = [
        _agent_case(101),
        _agent_case(102),
    ]
    calls: list[dict] = []
    fake = _fake_agent_evaluate_case(
        {102: {"actual_action": "escalate", "action_pass": False, "overall_pass": False}},
        calls,
    )

    with patch.object(AgentEvaluationService, "evaluate_case", side_effect=fake):
        run = await AIEvaluationService.run_agent_evaluation(
            db,
            organization_id=org.id,
            cases=cases,
            trigger_source="manual",
            corpus_revision={"documents": 12},
        )

    assert run is not None
    assert run.target_type == "agent"
    assert run.status == "succeeded"
    assert run.trigger_source == "manual"
    assert run.model == settings.chat_model
    assert run.agent_decision_version == "2"
    assert run.tool_policy_version == 1
    assert run.corpus_revision == {"documents": 12}
    assert run.started_at is not None
    assert run.completed_at is not None
    assert run.pass_rate == 0.5

    assert run.metrics["total"] == 2
    assert run.metrics["passed"] == 1
    assert run.metrics["failed"] == 1
    assert run.metrics["pass_rate"] == 0.5
    assert run.metrics["action_accuracy"] == 0.5
    assert run.metrics["retrieval_accuracy"] == 1.0
    assert run.metrics["tool_accuracy"] == 1.0
    assert run.metrics["auto_execute_safety_accuracy"] == 1.0
    assert run.metrics["average_latency_ms"] == 25.0

    assert [c["ticket_id"] for c in calls] == [101, 102]
    assert all(c["organization_id"] == org.id for c in calls)
    assert calls[0]["expected_action"] == "respond"
    assert calls[0]["expected_retrieval"] is True
    assert calls[1]["expected_tool"] == "zendesk.send_reply"
    assert calls[1]["expected_auto_execute"] is False


# ---------------------------------------------------------------------------
# Agent: 2 — one AIEvaluationCase persisted per case, safe derived data only


@pytest.mark.asyncio
async def test_agent_case_results_persisted(db):
    org = await _make_org(db, "agent-cases")
    cases = [
        _agent_case(
            201,
            expected_action="no_action",
            expected_retrieval=False,
            expected_tool="none",
            expected_auto_execute=False,
            fingerprint="fp-201",
        ),
        _agent_case(202, expected_tool="zendesk.add_internal_note", expected_auto_execute=True),
    ]

    with patch.object(
        AgentEvaluationService,
        "evaluate_case",
        side_effect=_fake_agent_evaluate_case(),
    ):
        await AIEvaluationService.run_agent_evaluation(db, organization_id=org.id, cases=cases)

    rows = await _cases_for_org(db, organization_id=org.id)
    assert len(rows) == 2

    by_id = {row.case_id: row for row in rows}
    first = by_id["201"]
    assert first.case_type == "agent"
    assert first.fingerprint == "fp-201"
    assert first.expected["expected_action"] == "no_action"
    assert first.expected["expected_retrieval"] is False
    assert first.expected["expected_tool"] == "none"
    assert first.expected["expected_auto_execute"] is False
    assert first.actual["actual_action"] == "no_action"
    assert first.actual["actual_retrieval"] is False
    assert first.actual["actual_tools"] == ["none"]
    assert first.actual["actual_auto_execute"] is False
    assert first.dimensions["action_pass"] is True
    assert first.dimensions["retrieval_pass"] is True
    assert first.dimensions["tool_pass"] is True
    assert first.dimensions["auto_execute_pass"] is True
    assert first.dimensions["overall_pass"] is True
    assert first.latency_ms == 25.0

    for row in rows:
        for bucket in (row.expected, row.actual, row.dimensions, row.input):
            assert "subject" not in bucket
            assert "description" not in bucket
            assert "body" not in bucket
            assert "ticket" not in bucket


# ---------------------------------------------------------------------------
# Agent: 1K.2 — optional Coordinator-intent dimension scores + persists


@pytest.mark.asyncio
async def test_agent_intent_dimension_scored_and_persisted(db):
    org = await _make_org(db, "agent-intent")
    cases = [
        _agent_case(301, expected_intent="action"),
        _agent_case(302, expected_intent="information"),
    ]
    calls: list[dict] = []
    fake = _fake_agent_evaluate_case(
        {302: {"actual_intent": "mixed", "intent_pass": False}},
        calls,
    )

    with patch.object(AgentEvaluationService, "evaluate_case", side_effect=fake):
        run = await AIEvaluationService.run_agent_evaluation(
            db, organization_id=org.id, cases=cases
        )

    assert run.metrics["intent_accuracy"] == 0.5
    assert [c["ticket_id"] for c in calls] == [301, 302]
    assert [c["expected_intent"] for c in calls] == ["action", "information"]

    rows = {row.case_id: row for row in await _cases_for_org(db, organization_id=org.id)}
    row = rows["302"]
    assert row.expected["expected_intent"] == "information"
    assert row.actual["actual_intent"] == "mixed"
    assert row.dimensions["intent_pass"] is False
    assert (
        "intent_pass" not in rows["301"].dimensions
        or rows["301"].dimensions["intent_pass"] is True
    )


@pytest.mark.asyncio
async def test_agent_intent_accuracy_none_when_no_expected_intent(db):
    org = await _make_org(db, "agent-no-intent")

    with patch.object(
        AgentEvaluationService,
        "evaluate_case",
        side_effect=_fake_agent_evaluate_case(),
    ):
        run = await AIEvaluationService.run_agent_evaluation(
            db,
            organization_id=org.id,
            cases=[_agent_case(401), _agent_case(402)],
        )

    assert run.metrics["intent_accuracy"] is None
    assert run.metrics["action_accuracy"] == 1.0


# ---------------------------------------------------------------------------
# Agent: 1K.2 — summary aggregates intent only over scored cases


def test_agent_summary_intent_accuracy_denominator():
    def summary(results):
        return AIEvaluationService._build_agent_summary(results)

    all_scored = [
        {
            "latency_ms": 5.0,
            "overall_pass": True,
            "action_pass": True,
            "retrieval_pass": True,
            "tool_pass": True,
            "auto_execute_pass": True,
            "expected_intent": "action",
            "actual_intent": "action",
            "intent_pass": True,
        },
        {
            "latency_ms": 5.0,
            "overall_pass": True,
            "action_pass": True,
            "retrieval_pass": True,
            "tool_pass": True,
            "auto_execute_pass": True,
            "expected_intent": "information",
            "actual_intent": "mixed",
            "intent_pass": False,
        },
    ]
    s = summary(all_scored)
    assert s["intent_accuracy"] == 0.5

    unsored_case = dict(all_scored[0])
    unsored_case["expected_intent"] = None
    unsored_case["actual_intent"] = None
    unsored_case["intent_pass"] = None
    s = summary([all_scored[0], unsored_case])
    # Only the scored case counts toward the denominator.
    assert s["intent_accuracy"] == 1.0

    s = summary([unsored_case])
    assert s["intent_accuracy"] is None


# ---------------------------------------------------------------------------
# Agent: 1K.3 — optional specialist-path dimension scores + persists


@pytest.mark.asyncio
async def test_agent_specialist_path_dimension_scored_and_persisted(db):
    org = await _make_org(db, "agent-path")
    cases = [
        _agent_case(601, expected_specialists=["coordinator", "knowledge", "action"]),
        _agent_case(602, expected_specialists=["coordinator", "action"]),
    ]
    calls: list[dict] = []
    fake = _fake_agent_evaluate_case(
        {
            602: {
                "actual_specialists": ["coordinator", "knowledge", "action"],
                "specialist_path_pass": False,
            }
        },
        calls,
    )

    with patch.object(AgentEvaluationService, "evaluate_case", side_effect=fake):
        run = await AIEvaluationService.run_agent_evaluation(
            db, organization_id=org.id, cases=cases
        )

    assert run.metrics["specialist_path_accuracy"] == 0.5
    assert [c["ticket_id"] for c in calls] == [601, 602]
    assert [c["expected_specialists"] for c in calls] == [
        ["coordinator", "knowledge", "action"],
        ["coordinator", "action"],
    ]

    rows = {row.case_id: row for row in await _cases_for_org(db, organization_id=org.id)}
    row = rows["602"]
    assert row.expected["expected_specialists"] == ["coordinator", "action"]
    assert row.actual["actual_specialists"] == [
        "coordinator",
        "knowledge",
        "action",
    ]
    assert row.dimensions["specialist_path_pass"] is False


@pytest.mark.asyncio
async def test_agent_specialist_path_accuracy_none_when_no_expected_specialists(db):
    org = await _make_org(db, "agent-no-path")

    with patch.object(
        AgentEvaluationService,
        "evaluate_case",
        side_effect=_fake_agent_evaluate_case(),
    ):
        run = await AIEvaluationService.run_agent_evaluation(
            db,
            organization_id=org.id,
            cases=[_agent_case(701), _agent_case(702)],
        )

    assert run.metrics["specialist_path_accuracy"] is None
    assert run.metrics["action_accuracy"] == 1.0


# ---------------------------------------------------------------------------
# Agent: 1K.3 — summary aggregates specialist path only over scored cases


def test_agent_summary_specialist_path_accuracy_denominator():
    def summary(results):
        return AIEvaluationService._build_agent_summary(results)

    def base_case(overrides):
        case = {
            "latency_ms": 5.0,
            "overall_pass": True,
            "action_pass": True,
            "retrieval_pass": True,
            "tool_pass": True,
            "auto_execute_pass": True,
            "expected_specialists": ["coordinator", "action"],
            "actual_specialists": ["coordinator", "action"],
            "specialist_path_pass": True,
        }
        case.update(overrides)
        return case

    all_scored = [
        base_case({}),
        base_case(
            {
                "expected_specialists": ["coordinator", "action"],
                "actual_specialists": ["coordinator", "knowledge", "action"],
                "specialist_path_pass": False,
            }
        ),
    ]
    s = summary(all_scored)
    assert s["specialist_path_accuracy"] == 0.5

    unscored_case = base_case(
        {
            "expected_specialists": None,
            "actual_specialists": ["coordinator", "action"],
            "specialist_path_pass": None,
        }
    )
    s = summary([all_scored[0], unscored_case])
    # Only the scored case counts toward the denominator.
    assert s["specialist_path_accuracy"] == 1.0

    s = summary([unscored_case])
    assert s["specialist_path_accuracy"] is None


# ---------------------------------------------------------------------------
# Agent: 1K.3 — durable input only keeps the bounded specialist labels


def test_agent_sanitize_case_whitelists_expected_specialists():
    dirty = {
        "ticket_id": 42,
        "expected_action": "respond",
        "expected_retrieval": True,
        "expected_tool": "zendesk.send_reply",
        "expected_auto_execute": False,
        "expected_specialists": [
            "coordinator",
            "knowledge",
            "action",
            "intruder-prompt",
            "SELECT * FROM cases",
        ],
        "subject": "please ignore",
        "description": "secret body",
    }
    sanitized = AIEvaluationService._sanitize_agent_case(dirty)
    assert sanitized["expected_specialists"] == [
        "coordinator",
        "knowledge",
        "action",
    ]
    assert "subject" not in sanitized
    assert "description" not in sanitized


def test_agent_sanitize_case_drops_expected_specialists_when_absent():
    clean = {
        "ticket_id": 43,
        "expected_action": "no_action",
        "expected_retrieval": False,
        "expected_tool": "none",
        "expected_auto_execute": False,
    }
    sanitized = AIEvaluationService._sanitize_agent_case(clean)
    assert "expected_specialists" not in sanitized


# ---------------------------------------------------------------------------
# Agent: 5 — failure marks the run failed and re-raises


@pytest.mark.asyncio
async def test_agent_failed_case_marks_run_failed(db):
    org = await _make_org(db, "agent-fail")
    failed_run_id = f"run-agent-fail-{uuid.uuid4().hex}"

    with (
        patch.object(
            AgentEvaluationService,
            "evaluate_case",
            side_effect=_fake_agent_evaluate_case({303: "raise"}),
        ),
        pytest.raises(RuntimeError),
    ):
        await AIEvaluationService.run_agent_evaluation(
            db,
            organization_id=org.id,
            cases=[_agent_case(303)],
            run_id=failed_run_id,
        )

    run = await AIEvaluationRepository.get_run_for_tenant(
        db, run_id=failed_run_id, organization_id=org.id
    )
    assert run is not None
    assert run.status == "failed"
    assert run.completed_at is not None
    assert run.error == "Agent evaluation failed on case 303: RuntimeError"
    assert "simulated agent evaluator failure" not in run.error

    rows = await _cases_for_org(db, organization_id=org.id)
    assert rows == []


# ---------------------------------------------------------------------------
# Agent: 6 — organization_id preserved across run + cases


@pytest.mark.asyncio
async def test_agent_organization_id_preserved_across_run_and_cases(db):
    org_a = await _make_org(db, "agent-a")
    org_b = await _make_org(db, "agent-b")
    calls: list[dict] = []

    with patch.object(
        AgentEvaluationService,
        "evaluate_case",
        side_effect=_fake_agent_evaluate_case(None, calls),
    ):
        run = await AIEvaluationService.run_agent_evaluation(
            db,
            organization_id=org_a.id,
            cases=[_agent_case(401), _agent_case(402)],
        )

    assert run.organization_id == org_a.id
    assert all(c["organization_id"] == org_a.id for c in calls)
    rows_a = await _cases_for_org(db, organization_id=org_a.id)
    assert len(rows_a) == 2
    assert all(row.organization_id == org_a.id for row in rows_a)

    rows_b = await _cases_for_org(db, organization_id=org_b.id)
    assert rows_b == []
    foreign = await AIEvaluationRepository.get_run_for_tenant(
        db, run_id=run.run_id, organization_id=org_b.id
    )
    assert foreign is None


# ---------------------------------------------------------------------------
# Agent: 7 — a case cannot switch tenant


@pytest.mark.asyncio
async def test_agent_case_input_cannot_choose_another_tenant(db):
    org_a = await _make_org(db, "agent-t-a")
    org_b = await _make_org(db, "agent-t-b")
    sneaky = _agent_case(501)
    sneaky["organization_id"] = org_b.id
    calls: list[dict] = []

    with patch.object(
        AgentEvaluationService,
        "evaluate_case",
        side_effect=_fake_agent_evaluate_case(None, calls),
    ):
        run = await AIEvaluationService.run_agent_evaluation(
            db,
            organization_id=org_a.id,
            cases=[sneaky],
        )

    assert run.organization_id == org_a.id
    assert calls and all(c["organization_id"] == org_a.id for c in calls)
    rows = await _cases_for_org(db, organization_id=org_a.id)
    assert len(rows) == 1
    assert rows[0].organization_id == org_a.id
    assert rows[0].run_id == run.id


# ---------------------------------------------------------------------------
# Agent: 8 — empty case list handled safely


@pytest.mark.asyncio
async def test_agent_empty_case_list_marks_run_failed(db):
    org = await _make_org(db, "agent-empty")
    empty_run_id = f"run-agent-empty-{uuid.uuid4().hex}"

    with pytest.raises(ValueError):
        await AIEvaluationService.run_agent_evaluation(
            db, organization_id=org.id, cases=[], run_id=empty_run_id
        )

    run = await AIEvaluationRepository.get_run_for_tenant(
        db, run_id=empty_run_id, organization_id=org.id
    )
    assert run is not None
    assert run.status == "failed"
    assert run.error == "No Agent cases were provided for the evaluation run."
    assert run.completed_at is not None

    rows = await _cases_for_org(db, organization_id=org.id)
    assert rows == []


# ---------------------------------------------------------------------------
# 1, 3, 4 — run created, metrics saved, succeeded


@pytest.mark.asyncio
async def test_rag_run_created_persists_metrics_and_succeeds(db):
    org = await _make_org(db, "ok")
    cases = [
        _rag_case("c-ok"),
        _rag_case("c-fail", expected_terms=["unmatched-term"]),
    ]
    fake = _fake_evaluate_case(
        {
            "c-fail": {"answer_correct": False, "passed": False},
        }
    )

    with patch.object(RAGEvaluationService, "evaluate_case", side_effect=fake):
        run = await AIEvaluationService.run_rag_evaluation(
            db,
            organization_id=org.id,
            cases=cases,
            trigger_source="ci",
            requested_by_subject="tester@example.com",
            corpus_revision={"documents": 12},
        )

    assert run is not None
    assert run.target_type == "rag"
    assert run.status == "succeeded"
    assert run.trigger_source == "ci"
    assert run.requested_by_subject == "tester@example.com"
    assert run.model == settings.chat_model
    assert run.embedding_model == settings.embedding_model
    assert run.corpus_revision == {"documents": 12}
    assert run.started_at is not None
    assert run.completed_at is not None
    assert run.pass_rate == 0.5

    assert run.metrics["total_cases"] == 2
    assert run.metrics["passed_cases"] == 1
    assert run.metrics["pass_rate"] == 0.5
    assert run.metrics["retrieval_accuracy"] == 1.0
    assert run.metrics["answer_correctness"] == 0.5
    assert run.metrics["grounding_accuracy"] == 1.0
    assert run.metrics["citation_validity"] == 1.0
    assert run.metrics["refusal_accuracy"] == 0.0
    assert run.metrics["avg_latency_ms"] == 100.0
    assert run.metrics["total_tokens"] == 100
    assert run.metrics["estimated_cost_usd"] == 0.002


# ---------------------------------------------------------------------------
# 2 — one AIEvaluationCase persisted per evaluated case


@pytest.mark.asyncio
async def test_case_results_persisted(db):
    org = await _make_org(db, "cases")
    cases = [_rag_case("c-1"), _rag_case("c-refuse", should_refuse=True)]

    with patch.object(
        RAGEvaluationService,
        "evaluate_case",
        side_effect=_fake_evaluate_case(),
    ):
        await AIEvaluationService.run_rag_evaluation(db, organization_id=org.id, cases=cases)

    rows = await _cases_for_org(db, organization_id=org.id)
    assert len(rows) == 2

    by_id = {row.case_id: row for row in rows}
    first = by_id["c-1"]
    assert first.case_type == "rag"
    assert first.expected["expected_sources"] == ["Policy"]
    assert first.actual["answer_preview"].startswith("the target team")
    assert first.actual["sources"] == ["Policy"]
    assert first.dimensions["passed"] is True
    assert first.dimensions["best_similarity"] == 0.9
    assert first.latency_ms == 100.0
    assert first.total_tokens == 50
    assert first.estimated_cost_usd == 0.001

    refused = by_id["c-refuse"]
    assert refused.dimensions["refusal_correct"] is True
    assert refused.dimensions["grounded"] is False


# ---------------------------------------------------------------------------
# 5 — failure marks the run failed and re-raises


@pytest.mark.asyncio
async def test_failed_evaluation_marks_run_failed(db):
    org = await _make_org(db, "fail")
    cases = [_rag_case("explode")]
    failed_run_id = f"run-fail-{uuid.uuid4().hex}"

    with (
        patch.object(
            RAGEvaluationService,
            "evaluate_case",
            side_effect=_fake_evaluate_case({"explode": "raise"}),
        ),
        pytest.raises(RuntimeError),
    ):
        await AIEvaluationService.run_rag_evaluation(
            db, organization_id=org.id, cases=cases, run_id=failed_run_id
        )

    run = await AIEvaluationRepository.get_run_for_tenant(
        db, run_id=failed_run_id, organization_id=org.id
    )
    assert run is not None
    assert run.status == "failed"
    assert run.completed_at is not None
    assert run.error == "RAG evaluation failed on case explode: RuntimeError"
    assert "Question text" not in run.error


# ---------------------------------------------------------------------------
# 6 — organization_id preserved across run + cases


@pytest.mark.asyncio
async def test_organization_id_preserved_across_run_and_cases(db):
    org_a = await _make_org(db, "a")
    org_b = await _make_org(db, "b")

    with patch.object(
        RAGEvaluationService,
        "evaluate_case",
        side_effect=_fake_evaluate_case(),
    ):
        run = await AIEvaluationService.run_rag_evaluation(
            db,
            organization_id=org_a.id,
            cases=[_rag_case("c-1"), _rag_case("c-2")],
        )

    assert run.organization_id == org_a.id
    rows_a = await _cases_for_org(db, organization_id=org_a.id)
    assert len(rows_a) == 2
    assert all(row.organization_id == org_a.id for row in rows_a)

    rows_b = await _cases_for_org(db, organization_id=org_b.id)
    assert rows_b == []
    foreign = await AIEvaluationRepository.get_run_for_tenant(
        db, run_id=run.run_id, organization_id=org_b.id
    )
    assert foreign is None


# ---------------------------------------------------------------------------
# 7 — a case cannot switch tenant


@pytest.mark.asyncio
async def test_case_input_cannot_choose_another_tenant(db):
    org_a = await _make_org(db, "a")
    org_b = await _make_org(db, "b")
    sneaky = _rag_case("sneaky")
    sneaky["organization_id"] = org_b.id

    with patch.object(
        RAGEvaluationService,
        "evaluate_case",
        side_effect=_fake_evaluate_case(),
    ):
        run = await AIEvaluationService.run_rag_evaluation(
            db,
            organization_id=org_a.id,
            cases=[sneaky],
        )

    assert run.organization_id == org_a.id
    rows = await _cases_for_org(db, organization_id=org_a.id)
    assert len(rows) == 1
    assert rows[0].organization_id == org_a.id
    assert rows[0].run_id == run.id


# ---------------------------------------------------------------------------
# 8 — empty case list handled safely


@pytest.mark.asyncio
async def test_empty_case_list_marks_run_failed(db):
    org = await _make_org(db, "empty")
    empty_run_id = f"run-empty-{uuid.uuid4().hex}"

    with pytest.raises(ValueError):
        await AIEvaluationService.run_rag_evaluation(
            db, organization_id=org.id, cases=[], run_id=empty_run_id
        )

    run = await AIEvaluationRepository.get_run_for_tenant(
        db, run_id=empty_run_id, organization_id=org.id
    )
    assert run is not None
    assert run.status == "failed"
    assert run.error == "No RAG cases were provided for the evaluation run."
    assert run.completed_at is not None

    rows = await _cases_for_org(db, organization_id=org.id)
    assert rows == []
