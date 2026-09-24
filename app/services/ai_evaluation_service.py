"""Persisted AI evaluation runs.

Phase 1J scope: RAG (1J.2) and Agent (1J.3) evaluation via one coordinator.
This service is the coordinator:

    create run
    → call the existing evaluator per case
    → persist one AIEvaluationCase per evaluated case
    → persist aggregate run metrics

It does NOT re-implement evaluation scoring; ``RAGEvaluationService.evaluate_case``
and ``AgentEvaluationService.evaluate_case`` remain the single source of truth
for per-case results.

Phase 1J.4 durable job: runs may be provisioned in the ``queued`` state with a
sanitized/synthetic snapshot of the operator-authored case definitions stored in
``run.input`` (never customer body/email/text, never secrets), then executed
later by the worker via ``execute_existing_rag_run``/``execute_existing_agent_run``.
Those paths never create a second run; they resume the provisioned one.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.ai_evaluation_baseline import AIEvaluationBaseline
from app.models.ai_evaluation_release_decision import AIEvaluationReleaseDecision
from app.models.ai_evaluation_run import AIEvaluationRun
from app.repositories.ai_evaluation_repository import AIEvaluationRepository
from app.schemas.evaluation import (
    EvaluationIdentitySnapshot,
    EvaluationMetricComparison,
    EvaluationReleaseGateIssue,
    EvaluationReleaseGateResult,
    EvaluationRunComparison,
    MetricDirection,
)
from app.services.agent_evaluation_service import AgentEvaluationService
from app.services.agent_workflow_service import AGENT_DECISION_VERSION
from app.services.rag_evaluation_service import RAGEvaluationService
from app.services.tool_authorization_service import ToolAuthorizationService

_ANSWER_PREVIEW_MAX = 500

_DECISION_NOTE_MAX = 1000

_DECISION_VALUES = frozenset({"approved", "rejected"})

_APPROVAL_BLOCKED_ERROR = "Approval blocked by critical evaluation regression."

_RAG_CRITICAL_METRICS = frozenset({"citation_validity", "grounding_accuracy"})

_AGENT_CRITICAL_METRICS = frozenset({"auto_execute_safety_accuracy"})

_CRITICAL_METRICS_BY_TARGET = {
    "rag": _RAG_CRITICAL_METRICS,
    "agent": _AGENT_CRITICAL_METRICS,
}

_METRIC_KEYS = (
    "total_cases",
    "passed_cases",
    "pass_rate",
    "retrieval_accuracy",
    "answer_correctness",
    "grounding_accuracy",
    "citation_validity",
    "refusal_accuracy",
    "avg_latency_ms",
    "total_tokens",
    "estimated_cost_usd",
)

_AGENT_METRIC_KEYS = (
    "total",
    "passed",
    "failed",
    "pass_rate",
    "action_accuracy",
    "retrieval_accuracy",
    "tool_accuracy",
    "auto_execute_safety_accuracy",
    "average_latency_ms",
)

_EMPTY_CASES_ERROR = "No RAG cases were provided for the evaluation run."

_AGENT_EMPTY_CASES_ERROR = "No Agent cases were provided for the evaluation run."


class AIEvaluationService:
    """Coordinates persisted evaluation runs (RAG + Agent in this phase)."""

    @staticmethod
    def _require_organization_id(organization_id: int | None) -> int:
        if organization_id is None:
            raise ValueError("organization_id is required for AI evaluation")
        return organization_id

    @staticmethod
    def _sanitize_rag_case(case: dict) -> dict:
        """Keep only the operator-authored synthetic fields a RAG run needs.

        Drops anything that could carry customer content (subject, description,
        imported conversation text) or secrets, so the durable-job snapshot in
        ``run.input`` stays PII-free.
        """
        sanitized: dict = {
            "id": str(case["id"]),
            "question": str(case["question"]),
        }
        if case.get("expected_sources") is not None:
            sanitized["expected_sources"] = list(case["expected_sources"])
        if case.get("expected_terms") is not None:
            sanitized["expected_terms"] = list(case["expected_terms"])
        if case.get("should_refuse") is not None:
            sanitized["should_refuse"] = bool(case["should_refuse"])
        if case.get("fingerprint"):
            sanitized["fingerprint"] = str(case["fingerprint"])
        return sanitized

    @staticmethod
    def _sanitize_agent_case(case: dict) -> dict:
        """Keep only the operator-authored synthetic fields an Agent run needs."""
        sanitized = {
            "ticket_id": case["ticket_id"],
            "expected_action": case["expected_action"],
            "expected_retrieval": case["expected_retrieval"],
            "expected_tool": case["expected_tool"],
            "expected_auto_execute": case["expected_auto_execute"],
        }
        if case.get("fingerprint"):
            sanitized["fingerprint"] = str(case["fingerprint"])
        return sanitized

    @staticmethod
    def _rag_input_payload(cases: list[dict]) -> dict:
        return {"cases": [AIEvaluationService._sanitize_rag_case(c) for c in cases]}

    @staticmethod
    def _agent_input_payload(cases: list[dict]) -> dict:
        return {"cases": [AIEvaluationService._sanitize_agent_case(c) for c in cases]}

    @staticmethod
    async def _get_run_or_raise(
        db: AsyncSession,
        *,
        run_id: str,
        organization_id: int,
    ) -> AIEvaluationRun:
        run = await AIEvaluationRepository.get_run_for_tenant(
            db, run_id=run_id, organization_id=organization_id
        )
        if run is None:
            raise ValueError(f"Evaluation run {run_id} was not found.")
        return run

    @staticmethod
    async def _create_run_record(
        db: AsyncSession,
        *,
        run_id: str,
        organization_id: int,
        target_type: str,
        status: str,
        trigger_source: str,
        requested_by_subject: str | None,
        model: str,
        input_data: dict,
        **version_identity: Any,
    ) -> None:
        """Create a run with an explicit state and a sanitized case snapshot.

        ``running`` runs get a started_at stamp; ``queued`` runs stay unstamped
        until the worker actually executes them.
        """
        await AIEvaluationRepository.create_run(
            db,
            run_id=run_id,
            organization_id=organization_id,
            target_type=target_type,
            status=status,
            model=model,
            trigger_source=trigger_source,
            requested_by_subject=requested_by_subject,
            started_at=(datetime.now(UTC) if status == "running" else None),
            input_data=input_data,
            **version_identity,
        )

    @staticmethod
    def _build_summary(
        results: list[dict],
        cases: list[dict],
    ) -> dict:
        """Aggregate per-case results using the evaluate_rag metric set.

        Mirrors the summary produced by ``RAGEvaluationService.evaluate`` so
        persisted run metrics keep the same names/formulas without re-running
        the evaluator.
        """

        def rate(values: list[bool]) -> float:
            if not values:
                return 0.0
            return sum(values) / len(values)

        total = len(results)
        answerable_results = [
            result
            for result, case in zip(results, cases, strict=True)
            if not case.get("should_refuse", False)
        ]
        refusal_results = [
            result
            for result, case in zip(results, cases, strict=True)
            if case.get("should_refuse", False)
        ]

        return {
            "total_cases": total,
            "passed_cases": sum(result["passed"] for result in results),
            "pass_rate": rate([result["passed"] for result in results]),
            "retrieval_accuracy": rate([result["retrieval_hit"] for result in answerable_results]),
            "answer_correctness": rate([result["answer_correct"] for result in answerable_results]),
            "grounding_accuracy": rate([result["grounding_correct"] for result in results]),
            "citation_validity": rate([result["citation_valid"] for result in answerable_results]),
            "refusal_accuracy": rate(
                [bool(result["refusal_correct"]) for result in refusal_results]
            ),
            "avg_latency_ms": (
                sum(result["latency_ms"] for result in results) / total if total else 0.0
            ),
            "total_tokens": sum(result["total_tokens"] for result in results),
            "estimated_cost_usd": sum(result["estimated_cost_usd"] for result in results),
        }

    @staticmethod
    async def _fail_run(
        db: AsyncSession,
        *,
        run_id: str,
        organization_id: int,
        error: str,
    ) -> None:
        await AIEvaluationRepository.update_run(
            db,
            run_id=run_id,
            organization_id=organization_id,
            values={
                "status": "failed",
                "error": error,
                "completed_at": datetime.now(UTC),
            },
        )

    @staticmethod
    async def _execute_rag_run(
        db: AsyncSession,
        *,
        run_id: str,
        organization_id: int,
        cases: list[dict],
    ) -> AIEvaluationRun:
        """Evaluate one persisted RAG run: guard, transition, execute, record.

        Shared by the synchronous ``run_rag_evaluation`` path and the durable
        job path (``execute_existing_rag_run``). The status guard makes a stale
        succeeded run a safe no-op and lets a queued run transition to running
        exactly once; persisted case rows are skipped on retry by
        ``(run_id, case_id)`` so a re-run records each case at most once.
        """
        run = await AIEvaluationRepository.get_run_for_tenant(
            db, run_id=run_id, organization_id=organization_id
        )
        if run is None:
            raise ValueError(f"Evaluation run {run_id} was not found.")
        if run.status == "succeeded":
            return run
        if run.status != "running":
            await AIEvaluationRepository.update_run(
                db,
                run_id=run_id,
                organization_id=organization_id,
                values={
                    "status": "running",
                    "started_at": run.started_at or datetime.now(UTC),
                },
            )

        if not cases:
            await AIEvaluationService._fail_run(
                db,
                run_id=run_id,
                organization_id=organization_id,
                error=_EMPTY_CASES_ERROR,
            )
            raise ValueError(_EMPTY_CASES_ERROR)

        existing_case_ids = {
            case.case_id
            for case in await AIEvaluationRepository.list_cases_for_run_for_tenant(
                db, run_id=run_id, organization_id=organization_id
            )
        }

        results: list[dict] = []
        current_case: dict | None = None

        try:
            for case in cases:
                current_case = case
                result = await RAGEvaluationService.evaluate_case(
                    db=db,
                    case=case,
                    organization_id=organization_id,
                )
                results.append(result)

                case_id = str(result["id"])
                if case_id in existing_case_ids:
                    continue

                await AIEvaluationRepository.add_case(
                    db,
                    run_id=run_id,
                    organization_id=organization_id,
                    case_id=case_id,
                    case_type="rag",
                    expected={
                        "expected_sources": case.get("expected_sources", []),
                        "expected_terms": case.get("expected_terms", []),
                        "should_refuse": bool(case.get("should_refuse", False)),
                    },
                    actual={
                        "answer_preview": str(result["answer"])[:_ANSWER_PREVIEW_MAX],
                        "sources": list(result["sources"]),
                    },
                    dimensions={
                        "passed": bool(result["passed"]),
                        "retrieval_hit": bool(result["retrieval_hit"]),
                        "answer_correct": bool(result["answer_correct"]),
                        "grounding_correct": bool(result["grounding_correct"]),
                        "refusal_correct": result["refusal_correct"],
                        "citation_valid": bool(result["citation_valid"]),
                        "grounded": bool(result["grounded"]),
                        "best_similarity": result["best_similarity"],
                    },
                    latency_ms=result["latency_ms"],
                    total_tokens=result["total_tokens"],
                    estimated_cost_usd=result["estimated_cost_usd"],
                    fingerprint=case.get("fingerprint"),
                )
        except Exception as exc:
            case_id = current_case.get("id", "unknown") if current_case else "unknown"
            await AIEvaluationService._fail_run(
                db,
                run_id=run_id,
                organization_id=organization_id,
                error=(f"RAG evaluation failed on case {case_id}: {type(exc).__name__}"),
            )
            raise

        summary = AIEvaluationService._build_summary(results, cases)
        metrics = {key: summary[key] for key in _METRIC_KEYS}

        await AIEvaluationRepository.update_run(
            db,
            run_id=run_id,
            organization_id=organization_id,
            values={
                "status": "succeeded",
                "pass_rate": summary["pass_rate"],
                "metrics": metrics,
                "completed_at": datetime.now(UTC),
            },
        )

        return await AIEvaluationService._get_run_or_raise(
            db, run_id=run_id, organization_id=organization_id
        )

    @staticmethod
    async def _execute_agent_run(
        db: AsyncSession,
        *,
        run_id: str,
        organization_id: int,
        cases: list[dict],
    ) -> AIEvaluationRun:
        """Evaluate one persisted Agent run (mirror of ``_execute_rag_run``)."""
        run = await AIEvaluationRepository.get_run_for_tenant(
            db, run_id=run_id, organization_id=organization_id
        )
        if run is None:
            raise ValueError(f"Evaluation run {run_id} was not found.")
        if run.status == "succeeded":
            return run
        if run.status != "running":
            await AIEvaluationRepository.update_run(
                db,
                run_id=run_id,
                organization_id=organization_id,
                values={
                    "status": "running",
                    "started_at": run.started_at or datetime.now(UTC),
                },
            )

        if not cases:
            await AIEvaluationService._fail_run(
                db,
                run_id=run_id,
                organization_id=organization_id,
                error=_AGENT_EMPTY_CASES_ERROR,
            )
            raise ValueError(_AGENT_EMPTY_CASES_ERROR)

        existing_case_ids = {
            case.case_id
            for case in await AIEvaluationRepository.list_cases_for_run_for_tenant(
                db, run_id=run_id, organization_id=organization_id
            )
        }

        results: list[dict] = []
        current_case: dict | None = None

        try:
            for case in cases:
                current_case = case
                case_id = AIEvaluationService._agent_case_id(case)
                result = await AgentEvaluationService.evaluate_case(
                    db=db,
                    ticket_id=case["ticket_id"],
                    organization_id=organization_id,
                    expected_action=case["expected_action"],
                    expected_retrieval=case["expected_retrieval"],
                    expected_tool=case["expected_tool"],
                    expected_auto_execute=case["expected_auto_execute"],
                )
                results.append(result)

                if case_id in existing_case_ids:
                    continue

                await AIEvaluationRepository.add_case(
                    db,
                    run_id=run_id,
                    organization_id=organization_id,
                    case_id=case_id,
                    case_type="agent",
                    expected={
                        "expected_action": result["expected_action"],
                        "expected_retrieval": result["expected_retrieval"],
                        "expected_tool": result["expected_tool"],
                        "expected_auto_execute": result["expected_auto_execute"],
                    },
                    actual={
                        "actual_action": result["actual_action"],
                        "actual_retrieval": result["actual_retrieval"],
                        "actual_tools": list(result["actual_tools"]),
                        "actual_auto_execute": result["actual_auto_execute"],
                    },
                    dimensions={
                        "action_pass": bool(result["action_pass"]),
                        "retrieval_pass": bool(result["retrieval_pass"]),
                        "tool_pass": bool(result["tool_pass"]),
                        "auto_execute_pass": bool(result["auto_execute_pass"]),
                        "overall_pass": bool(result["overall_pass"]),
                    },
                    latency_ms=result["latency_ms"],
                    total_tokens=result.get("total_tokens"),
                    estimated_cost_usd=result.get("estimated_cost_usd"),
                    fingerprint=case.get("fingerprint"),
                )
        except Exception as exc:
            case_id = (
                AIEvaluationService._agent_case_id(current_case) if current_case else "unknown"
            )
            await AIEvaluationService._fail_run(
                db,
                run_id=run_id,
                organization_id=organization_id,
                error=f"Agent evaluation failed on case {case_id}: {type(exc).__name__}",
            )
            raise

        summary = AIEvaluationService._build_agent_summary(results)
        metrics = {key: summary[key] for key in _AGENT_METRIC_KEYS}

        await AIEvaluationRepository.update_run(
            db,
            run_id=run_id,
            organization_id=organization_id,
            values={
                "status": "succeeded",
                "pass_rate": summary["pass_rate"],
                "metrics": metrics,
                "completed_at": datetime.now(UTC),
            },
        )

        return await AIEvaluationService._get_run_or_raise(
            db, run_id=run_id, organization_id=organization_id
        )

    @staticmethod
    async def run_rag_evaluation(
        db: AsyncSession,
        *,
        organization_id: int,
        cases: list[dict],
        trigger_source: str = "manual",
        requested_by_subject: str | None = None,
        run_id: str | None = None,
        model: str | None = None,
        embedding_model: str | None = None,
        corpus_revision: dict | None = None,
    ) -> AIEvaluationRun:
        """Run a persisted RAG evaluation against the existing evaluator.

        ``organization_id`` comes from the trusted caller and is used for the
        run AND every case row; case input can never choose another tenant.
        """
        organization_id = AIEvaluationService._require_organization_id(organization_id)

        resolved_run_id = run_id or uuid4().hex

        await AIEvaluationService._create_run_record(
            db,
            run_id=resolved_run_id,
            organization_id=organization_id,
            target_type="rag",
            status="running",
            trigger_source=trigger_source,
            requested_by_subject=requested_by_subject,
            model=model or settings.chat_model,
            input_data=AIEvaluationService._rag_input_payload(cases),
            embedding_model=embedding_model or settings.embedding_model,
            corpus_revision=corpus_revision,
        )

        return await AIEvaluationService._execute_rag_run(
            db,
            run_id=resolved_run_id,
            organization_id=organization_id,
            cases=cases,
        )

    @staticmethod
    async def provision_rag_run(
        db: AsyncSession,
        *,
        organization_id: int,
        cases: list[dict],
        trigger_source: str = "manual",
        requested_by_subject: str | None = None,
        run_id: str | None = None,
        model: str | None = None,
        embedding_model: str | None = None,
        corpus_revision: dict | None = None,
    ) -> AIEvaluationRun:
        """Provision a queued RAG evaluation run for the durable job.

        Persists a sanitized/synthetic snapshot of ``cases`` in ``run.input``
        (operator-authored expectations only; no customer text) so the worker
        can reload it later. Re-provisioning the same run is a safe no-op.
        """
        organization_id = AIEvaluationService._require_organization_id(organization_id)

        resolved_run_id = run_id or uuid4().hex

        existing = await AIEvaluationRepository.get_run_for_tenant(
            db, run_id=resolved_run_id, organization_id=organization_id
        )
        if existing is not None:
            return existing

        await AIEvaluationService._create_run_record(
            db,
            run_id=resolved_run_id,
            organization_id=organization_id,
            target_type="rag",
            status="queued",
            trigger_source=trigger_source,
            requested_by_subject=requested_by_subject,
            model=model or settings.chat_model,
            input_data=AIEvaluationService._rag_input_payload(cases),
            embedding_model=embedding_model or settings.embedding_model,
            corpus_revision=corpus_revision,
        )

        return await AIEvaluationService._get_run_or_raise(
            db, run_id=resolved_run_id, organization_id=organization_id
        )

    @staticmethod
    async def execute_existing_rag_run(
        db: AsyncSession,
        *,
        organization_id: int,
        run_id: str,
    ) -> AIEvaluationRun:
        """Execute a previously provisioned RAG evaluation run (worker path).

        Never creates a second run: the queued run — with its sanitized case
        snapshot in ``run.input`` — is resumed and marked running→succeeded.
        Failures mark the run failed (safe, type-name-only error) and re-raise
        to the job scheduler so it can apply its retry/failure policy.
        """
        organization_id = AIEvaluationService._require_organization_id(organization_id)

        run = await AIEvaluationRepository.get_run_for_tenant(
            db, run_id=run_id, organization_id=organization_id
        )
        if run is None:
            raise ValueError(f"Evaluation run {run_id} was not found.")
        if run.organization_id != organization_id:
            raise ValueError("Evaluation run is not owned by the executing organization.")
        if run.target_type != "rag":
            raise ValueError(f"Evaluation run {run_id} is not a RAG evaluation run.")

        cases = list((run.input or {}).get("cases", []))

        return await AIEvaluationService._execute_rag_run(
            db,
            run_id=run_id,
            organization_id=organization_id,
            cases=cases,
        )

    # ------------------------------------------------------------------ agent

    @staticmethod
    def _agent_case_id(case: dict) -> str:
        """Stable per-case identifier: the ticket id, else a case reference."""
        for key in ("ticket_id", "id"):
            value = case.get(key)
            if value is not None:
                return str(value)
        return "unknown"

    @staticmethod
    def _build_agent_summary(results: list[dict]) -> dict:
        """Aggregate per-case results using the evaluate_agent metric set.

        Mirrors the summary produced by ``scripts/evaluate_agent.py`` so
        persisted run metrics keep the same names/formulas.
        """

        def rate(values: list[bool]) -> float:
            if not values:
                return 0.0
            return sum(values) / len(values)

        total = len(results)
        total_latency = sum(result["latency_ms"] for result in results)

        return {
            "total": total,
            "passed": sum(bool(result["overall_pass"]) for result in results),
            "failed": total - sum(bool(result["overall_pass"]) for result in results),
            "pass_rate": rate([bool(result["overall_pass"]) for result in results]),
            "action_accuracy": rate([bool(result["action_pass"]) for result in results]),
            "retrieval_accuracy": rate([bool(result["retrieval_pass"]) for result in results]),
            "tool_accuracy": rate([bool(result["tool_pass"]) for result in results]),
            "auto_execute_safety_accuracy": rate(
                [bool(result["auto_execute_pass"]) for result in results]
            ),
            "average_latency_ms": (round(total_latency / total, 2) if total else 0.0),
        }

    @staticmethod
    async def run_agent_evaluation(
        db: AsyncSession,
        *,
        organization_id: int,
        cases: list[dict],
        trigger_source: str = "manual",
        requested_by_subject: str | None = None,
        run_id: str | None = None,
        model: str | None = None,
        agent_decision_version: str | None = None,
        tool_policy_version: int | None = None,
        corpus_revision: dict | None = None,
    ) -> AIEvaluationRun:
        """Run a persisted Agent evaluation against the existing evaluator.

        ``organization_id`` comes from the trusted caller and is used for the
        run AND every case row; case input can never choose another tenant.
        Version identity defaults to the real agent decision/tool policy
        versions the workflow itself uses; nothing is invented.
        """
        organization_id = AIEvaluationService._require_organization_id(organization_id)

        resolved_run_id = run_id or uuid4().hex

        await AIEvaluationService._create_run_record(
            db,
            run_id=resolved_run_id,
            organization_id=organization_id,
            target_type="agent",
            status="running",
            trigger_source=trigger_source,
            requested_by_subject=requested_by_subject,
            model=model or settings.chat_model,
            input_data=AIEvaluationService._agent_input_payload(cases),
            agent_decision_version=agent_decision_version or AGENT_DECISION_VERSION,
            tool_policy_version=(
                tool_policy_version
                if tool_policy_version is not None
                else ToolAuthorizationService.TOOL_POLICY_VERSION
            ),
            corpus_revision=corpus_revision,
        )

        return await AIEvaluationService._execute_agent_run(
            db,
            run_id=resolved_run_id,
            organization_id=organization_id,
            cases=cases,
        )

    @staticmethod
    async def provision_agent_run(
        db: AsyncSession,
        *,
        organization_id: int,
        cases: list[dict],
        trigger_source: str = "manual",
        requested_by_subject: str | None = None,
        run_id: str | None = None,
        model: str | None = None,
        agent_decision_version: str | None = None,
        tool_policy_version: int | None = None,
        corpus_revision: dict | None = None,
    ) -> AIEvaluationRun:
        """Provision a queued Agent evaluation run for the durable job."""
        organization_id = AIEvaluationService._require_organization_id(organization_id)

        resolved_run_id = run_id or uuid4().hex

        existing = await AIEvaluationRepository.get_run_for_tenant(
            db, run_id=resolved_run_id, organization_id=organization_id
        )
        if existing is not None:
            return existing

        await AIEvaluationService._create_run_record(
            db,
            run_id=resolved_run_id,
            organization_id=organization_id,
            target_type="agent",
            status="queued",
            trigger_source=trigger_source,
            requested_by_subject=requested_by_subject,
            model=model or settings.chat_model,
            input_data=AIEvaluationService._agent_input_payload(cases),
            agent_decision_version=agent_decision_version or AGENT_DECISION_VERSION,
            tool_policy_version=(
                tool_policy_version
                if tool_policy_version is not None
                else ToolAuthorizationService.TOOL_POLICY_VERSION
            ),
            corpus_revision=corpus_revision,
        )

        return await AIEvaluationService._get_run_or_raise(
            db, run_id=resolved_run_id, organization_id=organization_id
        )

    @staticmethod
    async def execute_existing_agent_run(
        db: AsyncSession,
        *,
        organization_id: int,
        run_id: str,
    ) -> AIEvaluationRun:
        """Execute a previously provisioned Agent evaluation run (worker path).

        Mirrors ``execute_existing_rag_run``: resumes the provisioned run, never
        creates a second one, and re-raises to the job scheduler on failure.
        """
        organization_id = AIEvaluationService._require_organization_id(organization_id)

        run = await AIEvaluationRepository.get_run_for_tenant(
            db, run_id=run_id, organization_id=organization_id
        )
        if run is None:
            raise ValueError(f"Evaluation run {run_id} was not found.")
        if run.organization_id != organization_id:
            raise ValueError("Evaluation run is not owned by the executing organization.")
        if run.target_type != "agent":
            raise ValueError(f"Evaluation run {run_id} is not an Agent evaluation run.")

        cases = list((run.input or {}).get("cases", []))

        return await AIEvaluationService._execute_agent_run(
            db,
            run_id=run_id,
            organization_id=organization_id,
            cases=cases,
        )

    # -------------------------------------------------- baseline + comparison

    @staticmethod
    def _numeric_metric(value: object) -> bool:
        """True for a comparable numeric metric (int/float, never a bool)."""
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    @staticmethod
    def _direction(candidate: float, baseline: float) -> MetricDirection:
        if candidate > baseline:
            return "improved"
        if candidate < baseline:
            return "regressed"
        return "same"

    @staticmethod
    async def create_baseline_from_run(
        db: AsyncSession,
        *,
        organization_id: int,
        run_id: str,
        created_by_subject: str | None = None,
    ) -> AIEvaluationBaseline:
        """Promote a succeeded run to the tenant's next baseline.

        Phase 1J.7A rules:
        - the run must belong to ``organization_id`` and be ``succeeded``
        - the baseline copies the run's pass rate, metrics, and version identity
        - ``cases_count`` comes from the persisted case rows, not the input count
        - ``version`` is the deterministic next integer for
          (organization_id, target_type), starting at 1
        - history is one never-deleting series per target type: the new baseline
          is promoted, older baselines are kept untouched
        """
        run = await AIEvaluationRepository.get_run_for_tenant(
            db, run_id=run_id, organization_id=organization_id
        )
        if run is None:
            raise ValueError(f"Evaluation run {run_id} was not found.")
        if run.status != "succeeded":
            raise ValueError(
                f"Cannot promote evaluation run {run_id} with status {run.status!r}; "
                "only succeeded runs can become a baseline."
            )

        cases = await AIEvaluationRepository.list_cases_for_run_for_tenant(
            db, run_id=run_id, organization_id=organization_id
        )
        baseline_count = await AIEvaluationRepository.count_baselines_for_tenant_target(
            db, organization_id=organization_id, target_type=run.target_type
        )

        return await AIEvaluationRepository.create_baseline(
            db,
            organization_id=organization_id,
            target_type=run.target_type,
            version=str(baseline_count + 1),
            model=run.model,
            embedding_model=run.embedding_model,
            agent_decision_version=run.agent_decision_version,
            tool_policy_version=run.tool_policy_version,
            corpus_revision=run.corpus_revision,
            pass_rate=run.pass_rate,
            metrics=run.metrics,
            cases_count=len(cases),
            created_by_subject=created_by_subject,
            promoted=True,
        )

    @staticmethod
    async def compare_run_to_baseline(
        db: AsyncSession,
        *,
        organization_id: int,
        run_id: str,
        baseline_id: int,
    ) -> EvaluationRunComparison:
        """Compare a candidate run against a baseline owned by the same tenant.

        Phase 1J.7A rules:
        - the run and baseline must both belong to ``organization_id``; a
          foreign/missing run or baseline is indistinguishable safe error
        - candidate and baseline must have the same ``target_type``
        - only metrics present on BOTH sides (numeric values) are compared
        - each metric and the pass rate gets a bare delta and a direction
        - version identity for both sides is included without judging
        - NO aggregate or magic score is produced
        """
        run = await AIEvaluationRepository.get_run_for_tenant(
            db, run_id=run_id, organization_id=organization_id
        )
        baseline = await AIEvaluationRepository.get_baseline_for_tenant(
            db, baseline_id=baseline_id, organization_id=organization_id
        )
        if run is None or baseline is None:
            raise ValueError("The evaluation run or baseline was not found for this organization.")
        if run.target_type != baseline.target_type:
            raise ValueError(
                "Cannot compare an evaluation run against a baseline of a different target type."
            )

        candidate_metrics = run.metrics or {}
        baseline_metrics = baseline.metrics or {}

        metric_comparisons: list[EvaluationMetricComparison] = []
        for key in sorted(set(candidate_metrics) & set(baseline_metrics)):
            candidate_value = candidate_metrics[key]
            baseline_value = baseline_metrics[key]
            if not (
                AIEvaluationService._numeric_metric(candidate_value)
                and AIEvaluationService._numeric_metric(baseline_value)
            ):
                continue
            candidate_num = float(candidate_value)
            baseline_num = float(baseline_value)
            metric_comparisons.append(
                EvaluationMetricComparison(
                    metric=key,
                    baseline=baseline_num,
                    candidate=candidate_num,
                    delta=round(candidate_num - baseline_num, 6),
                    direction=AIEvaluationService._direction(candidate_num, baseline_num),
                )
            )

        candidate_pass_rate = run.pass_rate
        baseline_pass_rate = baseline.pass_rate
        pass_rate_delta: float | None = None
        if candidate_pass_rate is not None and baseline_pass_rate is not None:
            pass_rate_delta = round(float(candidate_pass_rate) - float(baseline_pass_rate), 6)

        return EvaluationRunComparison(
            target_type=run.target_type,
            candidate_pass_rate=candidate_pass_rate,
            baseline_pass_rate=baseline_pass_rate,
            pass_rate_delta=pass_rate_delta,
            metrics=metric_comparisons,
            baseline=EvaluationIdentitySnapshot(
                model=baseline.model,
                embedding_model=baseline.embedding_model,
                agent_decision_version=baseline.agent_decision_version,
                tool_policy_version=baseline.tool_policy_version,
                corpus_revision=baseline.corpus_revision,
            ),
            candidate=EvaluationIdentitySnapshot(
                model=run.model,
                embedding_model=run.embedding_model,
                agent_decision_version=run.agent_decision_version,
                tool_policy_version=run.tool_policy_version,
                corpus_revision=run.corpus_revision,
            ),
        )

    @staticmethod
    async def evaluate_release_gate(
        db: AsyncSession,
        *,
        organization_id: int,
        run_id: str,
        baseline_id: int,
    ) -> EvaluationReleaseGateResult:
        """Deterministic read-only release-gate check (Phase 1J.9A).

        Reuses ``compare_run_to_baseline`` as the single source of truth for the
        comparison: tenant safety (foreign/missing run or baseline is the same
        safe not-found error), the same-target-type rule, and the
        both-sides-numeric comparison all inherit from it. The gate policy is
        computed by ``_release_gate_result``.

        This method is pure read/check: it never writes rows, never creates
        release decisions or jobs, never changes runs or baselines, and does not
        block or enforce anything itself.
        """
        comparison = await AIEvaluationService.compare_run_to_baseline(
            db,
            organization_id=organization_id,
            run_id=run_id,
            baseline_id=baseline_id,
        )
        return AIEvaluationService._release_gate_result(comparison)

    @staticmethod
    def _release_gate_result(
        comparison: EvaluationRunComparison,
    ) -> EvaluationReleaseGateResult:
        """Compute the release-gate policy from an existing comparison.

        Pure function over ``compare_run_to_baseline`` output so callers that
        already hold a comparison (``record_release_decision``) never run the
        comparison twice:

        - a required critical metric absent from the comparison (missing on
          either side, or non-numeric) is a blocker and is never treated as zero
        - a critical metric where candidate < baseline is a blocker;
          candidate == baseline or candidate > baseline is never a blocker
        - ordinary (non-critical) metric regressions and a pass-rate regression
          are reported as warnings and never set ``blocked`` in this phase
        - no aggregate or magic quality score is produced
        """
        critical_metrics = _CRITICAL_METRICS_BY_TARGET.get(comparison.target_type, ())

        compared_by_metric = {comp.metric: comp for comp in comparison.metrics}

        critical_regressions: list[EvaluationReleaseGateIssue] = []
        for metric in critical_metrics:
            comp = compared_by_metric.get(metric)
            if comp is None:
                critical_regressions.append(
                    EvaluationReleaseGateIssue(
                        metric=metric,
                        kind="missing",
                        baseline=None,
                        candidate=None,
                        message=f"Required critical metric {metric} is missing.",
                    )
                )
                continue
            if comp.direction == "regressed":
                critical_regressions.append(
                    EvaluationReleaseGateIssue(
                        metric=metric,
                        kind="critical",
                        baseline=comp.baseline,
                        candidate=comp.candidate,
                        message=f"Critical metric {metric} regressed.",
                    )
                )

        warnings: list[EvaluationReleaseGateIssue] = []
        if comparison.pass_rate_delta is not None and comparison.pass_rate_delta < 0:
            warnings.append(
                EvaluationReleaseGateIssue(
                    metric="pass_rate",
                    kind="warning",
                    baseline=comparison.baseline_pass_rate,
                    candidate=comparison.candidate_pass_rate,
                    message="Metric pass_rate regressed.",
                )
            )
        for comp in comparison.metrics:
            if comp.metric in critical_metrics:
                continue
            if comp.direction == "regressed":
                warnings.append(
                    EvaluationReleaseGateIssue(
                        metric=comp.metric,
                        kind="warning",
                        baseline=comp.baseline,
                        candidate=comp.candidate,
                        message=f"Metric {comp.metric} regressed.",
                    )
                )

        return EvaluationReleaseGateResult(
            blocked=len(critical_regressions) > 0,
            target_type=comparison.target_type,
            critical_regressions=critical_regressions,
            warnings=warnings,
        )

    @staticmethod
    async def record_release_decision(
        db: AsyncSession,
        *,
        organization_id: int,
        candidate_run_id: str,
        baseline_id: int,
        decision: str,
        decided_by_subject: str,
        note: str | None = None,
    ) -> AIEvaluationReleaseDecision:
        """Record one immutable human release decision (Phase 1J.8A).

        Rules:
        - the candidate run and baseline must both belong to ``organization_id``;
          a foreign/missing run or baseline is an indistinguishable safe error
        - the candidate run must be ``succeeded``
        - candidate and baseline must share a ``target_type``
        - ``decision`` is ``approved`` or ``rejected``; ``note`` is bounded
        - the comparison snapshot comes from ``compare_run_to_baseline`` (derived
          metrics and version identity only) — raw run input, customer/ticket
          text, and secrets are never copied into the snapshot
        - an ``approved`` decision runs the release gate first (Phase 1J.9B):
          if the gate is blocked, NO decision row is created and a stable
          ValueError is raised; ``rejected`` decisions are never gate-blocked
          so a reviewer can always reject a candidate

        The comparison is computed exactly once and reused for both the gate
        check and the stored audit snapshot.
        """
        run = await AIEvaluationRepository.get_run_for_tenant(
            db, run_id=candidate_run_id, organization_id=organization_id
        )
        baseline = await AIEvaluationRepository.get_baseline_for_tenant(
            db, baseline_id=baseline_id, organization_id=organization_id
        )
        if run is None or baseline is None:
            raise ValueError("The evaluation run or baseline was not found for this organization.")
        if run.status != "succeeded":
            raise ValueError(
                f"Cannot record a release decision for evaluation run "
                f"{run.run_id!r} with status {run.status!r}; only succeeded runs "
                "can be decided."
            )
        if run.target_type != baseline.target_type:
            raise ValueError(
                "Cannot record a release decision referencing a run and baseline "
                "of different target types."
            )
        if decision not in _DECISION_VALUES:
            raise ValueError("decision must be 'approved' or 'rejected'.")
        if note is not None and len(note) > _DECISION_NOTE_MAX:
            raise ValueError("note must be at most 1000 characters.")

        comparison = await AIEvaluationService.compare_run_to_baseline(
            db,
            organization_id=organization_id,
            run_id=run.run_id,
            baseline_id=baseline.id,
        )

        if decision == "approved" and AIEvaluationService._release_gate_result(comparison).blocked:
            raise ValueError(_APPROVAL_BLOCKED_ERROR)

        return await AIEvaluationRepository.create_release_decision(
            db,
            organization_id=organization_id,
            candidate_run_id=run.id,
            baseline_id=baseline.id,
            decision=decision,
            decided_by_subject=decided_by_subject,
            note=note,
            comparison_snapshot=comparison.model_dump(),
        )
