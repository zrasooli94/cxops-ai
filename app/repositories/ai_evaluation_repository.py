"""Tenant-scoped persistence for AI evaluation state (run/case/baseline/decision).

Reads and writes are simple: no evaluation business logic lives here. Every
human-facing read binds ``organization_id`` in the WHERE clause, so a foreign
tenant's row is indistinguishable from a missing row.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_evaluation_baseline import AIEvaluationBaseline
from app.models.ai_evaluation_case import AIEvaluationCase
from app.models.ai_evaluation_release_decision import AIEvaluationReleaseDecision
from app.models.ai_evaluation_run import AIEvaluationRun

_RUN_LIST_DEFAULT_LIMIT = 100
_RUN_LIST_MAX_LIMIT = 200
_BASELINE_LIST_DEFAULT_LIMIT = 100
_BASELINE_LIST_MAX_LIMIT = 200
_DECISION_LIST_DEFAULT_LIMIT = 100
_DECISION_LIST_MAX_LIMIT = 200

_UPDATE_ALLOWED_FIELDS = frozenset(
    {"status", "pass_rate", "metrics", "error", "started_at", "completed_at"}
)


class AIEvaluationRepository:
    """Tenant-safe reads/writes for the evaluation database foundation."""

    # ------------------------------------------------------------------ runs

    @staticmethod
    async def create_run(
        db: AsyncSession,
        *,
        run_id: str,
        organization_id: int,
        target_type: str,
        model: str,
        embedding_model: str | None = None,
        agent_decision_version: str | None = None,
        tool_policy_version: int | None = None,
        corpus_revision: dict | None = None,
        trigger_source: str = "manual",
        requested_by_subject: str | None = None,
        status: str = "queued",
        started_at: datetime | None = None,
        input_data: dict | None = None,
    ) -> AIEvaluationRun:
        run = AIEvaluationRun(
            run_id=run_id,
            organization_id=organization_id,
            target_type=target_type,
            model=model,
            embedding_model=embedding_model,
            agent_decision_version=agent_decision_version,
            tool_policy_version=tool_policy_version,
            corpus_revision=corpus_revision,
            trigger_source=trigger_source,
            requested_by_subject=requested_by_subject,
            status=status,
            started_at=started_at,
            input=input_data or {},
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)
        return run

    @staticmethod
    async def get_run_for_tenant(
        db: AsyncSession,
        *,
        run_id: str,
        organization_id: int,
    ) -> AIEvaluationRun | None:
        result = await db.execute(
            select(AIEvaluationRun).where(
                AIEvaluationRun.run_id == run_id,
                AIEvaluationRun.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_runs_for_tenant(
        db: AsyncSession,
        *,
        organization_id: int,
        limit: int = _RUN_LIST_DEFAULT_LIMIT,
        offset: int = 0,
    ) -> list[AIEvaluationRun]:
        """Bounded, deterministic run listing for one tenant (one query)."""
        limit = max(1, min(limit, _RUN_LIST_MAX_LIMIT))
        offset = max(0, offset)
        result = await db.execute(
            select(AIEvaluationRun)
            .where(AIEvaluationRun.organization_id == organization_id)
            .order_by(AIEvaluationRun.created_at.desc(), AIEvaluationRun.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    @staticmethod
    async def count_runs_for_tenant(
        db: AsyncSession,
        *,
        organization_id: int,
    ) -> int:
        result = await db.execute(
            select(func.count())
            .select_from(AIEvaluationRun)
            .where(AIEvaluationRun.organization_id == organization_id)
        )
        return int(result.scalar_one())

    @staticmethod
    async def update_run(
        db: AsyncSession,
        *,
        run_id: str,
        organization_id: int,
        values: dict[str, Any],
    ) -> AIEvaluationRun | None:
        """Apply simple persisted-field updates to a tenant-owned run.

        Deliberately performs no lifecycle/business rules: transition
        validation is a later phase's responsibility. Only an allowlisted set
        of persisted fields can be written.
        """
        run = await AIEvaluationRepository.get_run_for_tenant(
            db, run_id=run_id, organization_id=organization_id
        )
        if run is None:
            return None

        unknown = set(values) - _UPDATE_ALLOWED_FIELDS
        if unknown:
            raise ValueError(f"Cannot update evaluation run field: {min(unknown)}")

        for key, value in values.items():
            setattr(run, key, value)

        await db.commit()
        await db.refresh(run)
        return run

    # ----------------------------------------------------------------- cases

    @staticmethod
    async def add_case(
        db: AsyncSession,
        *,
        run_id: str,
        organization_id: int,
        case_id: str,
        case_type: str,
        expected: dict | None = None,
        actual: dict | None = None,
        dimensions: dict | None = None,
        input_data: dict | None = None,
        latency_ms: float | None = None,
        total_tokens: int | None = None,
        estimated_cost_usd: float | None = None,
        fingerprint: str | None = None,
    ) -> AIEvaluationCase | None:
        """Attach a case to the tenant-owned run resolved by ``run_id``.

        The case's tenant is taken from the resolved run, never from caller
        input; the composite FK ``(run_id, organization_id)`` is the final
        safety layer. Returns None when no matching run exists.
        """
        run = await AIEvaluationRepository.get_run_for_tenant(
            db, run_id=run_id, organization_id=organization_id
        )
        if run is None:
            return None

        case = AIEvaluationCase(
            run_id=run.id,
            organization_id=run.organization_id,
            case_id=case_id,
            case_type=case_type,
            expected=expected or {},
            actual=actual or {},
            dimensions=dimensions or {},
            input=input_data or {},
            latency_ms=latency_ms,
            total_tokens=total_tokens,
            estimated_cost_usd=estimated_cost_usd,
            fingerprint=fingerprint,
        )
        db.add(case)
        await db.commit()
        await db.refresh(case)
        return case

    @staticmethod
    async def list_cases_for_run_for_tenant(
        db: AsyncSession,
        *,
        run_id: str,
        organization_id: int,
    ) -> list[AIEvaluationCase]:
        """One tenant-scoped join query for a run's cases (no per-case loop)."""
        result = await db.execute(
            select(AIEvaluationCase)
            .join(AIEvaluationRun, AIEvaluationRun.id == AIEvaluationCase.run_id)
            .where(
                AIEvaluationRun.run_id == run_id,
                AIEvaluationCase.organization_id == organization_id,
                AIEvaluationRun.organization_id == organization_id,
            )
            .order_by(AIEvaluationCase.id.asc())
        )
        return list(result.scalars().all())

    # -------------------------------------------------------------- baselines

    @staticmethod
    async def create_baseline(
        db: AsyncSession,
        *,
        organization_id: int,
        target_type: str,
        version: str,
        model: str,
        embedding_model: str | None = None,
        agent_decision_version: str | None = None,
        tool_policy_version: int | None = None,
        corpus_revision: dict | None = None,
        pass_rate: float | None = None,
        metrics: dict | None = None,
        cases_count: int = 0,
        created_by_subject: str | None = None,
        promoted: bool = False,
    ) -> AIEvaluationBaseline:
        baseline = AIEvaluationBaseline(
            organization_id=organization_id,
            target_type=target_type,
            version=version,
            model=model,
            embedding_model=embedding_model,
            agent_decision_version=agent_decision_version,
            tool_policy_version=tool_policy_version,
            corpus_revision=corpus_revision,
            pass_rate=pass_rate,
            metrics=metrics or {},
            cases_count=cases_count,
            created_by_subject=created_by_subject,
            promoted=promoted,
        )
        db.add(baseline)
        await db.commit()
        await db.refresh(baseline)
        return baseline

    @staticmethod
    async def list_baselines_for_tenant(
        db: AsyncSession,
        *,
        organization_id: int,
        target_type: str | None = None,
        limit: int = _BASELINE_LIST_DEFAULT_LIMIT,
    ) -> list[AIEvaluationBaseline]:
        """Bounded baseline history for a tenant (newest first, one query)."""
        limit = max(1, min(limit, _BASELINE_LIST_MAX_LIMIT))
        stmt = (
            select(AIEvaluationBaseline)
            .where(AIEvaluationBaseline.organization_id == organization_id)
            .order_by(
                AIEvaluationBaseline.created_at.desc(),
                AIEvaluationBaseline.id.desc(),
            )
            .limit(limit)
        )
        if target_type is not None:
            stmt = stmt.where(AIEvaluationBaseline.target_type == target_type)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_latest_baseline_for_tenant(
        db: AsyncSession,
        *,
        organization_id: int,
        target_type: str,
    ) -> AIEvaluationBaseline | None:
        result = await db.execute(
            select(AIEvaluationBaseline)
            .where(
                AIEvaluationBaseline.organization_id == organization_id,
                AIEvaluationBaseline.target_type == target_type,
            )
            .order_by(
                AIEvaluationBaseline.created_at.desc(),
                AIEvaluationBaseline.id.desc(),
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_baseline_for_tenant(
        db: AsyncSession,
        *,
        baseline_id: int,
        organization_id: int,
    ) -> AIEvaluationBaseline | None:
        result = await db.execute(
            select(AIEvaluationBaseline).where(
                AIEvaluationBaseline.id == baseline_id,
                AIEvaluationBaseline.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def count_baselines_for_tenant_target(
        db: AsyncSession,
        *,
        organization_id: int,
        target_type: str,
    ) -> int:
        result = await db.execute(
            select(func.count())
            .select_from(AIEvaluationBaseline)
            .where(
                AIEvaluationBaseline.organization_id == organization_id,
                AIEvaluationBaseline.target_type == target_type,
            )
        )
        return int(result.scalar_one())

    # ----------------------------------------------------- release decisions

    @staticmethod
    async def create_release_decision(
        db: AsyncSession,
        *,
        organization_id: int,
        candidate_run_id: int,
        baseline_id: int,
        decision: str,
        decided_by_subject: str,
        note: str | None = None,
        comparison_snapshot: dict | None = None,
    ) -> AIEvaluationReleaseDecision:
        """Persist one immutable release decision row.

        ``candidate_run_id``/``baseline_id`` are the sibling rows' database ids;
        the composite foreign keys on ``(id, organization_id)`` are the final
        crystal for tenant safety. No business rules or release policy live
        here — this repository is a plain record keeper.
        """
        record = AIEvaluationReleaseDecision(
            organization_id=organization_id,
            candidate_run_id=candidate_run_id,
            baseline_id=baseline_id,
            decision=decision,
            decided_by_subject=decided_by_subject,
            note=note,
            comparison_snapshot=comparison_snapshot or {},
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)
        return record

    @staticmethod
    async def list_release_decisions_for_tenant(
        db: AsyncSession,
        *,
        organization_id: int,
        limit: int = _DECISION_LIST_DEFAULT_LIMIT,
        offset: int = 0,
    ) -> list[AIEvaluationReleaseDecision]:
        """Bounded, deterministic decision history for one tenant (newest first)."""
        limit = max(1, min(limit, _DECISION_LIST_MAX_LIMIT))
        offset = max(0, offset)
        result = await db.execute(
            select(AIEvaluationReleaseDecision)
            .where(AIEvaluationReleaseDecision.organization_id == organization_id)
            .order_by(
                AIEvaluationReleaseDecision.created_at.desc(),
                AIEvaluationReleaseDecision.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_release_decision_for_tenant(
        db: AsyncSession,
        *,
        organization_id: int,
        decision_id: int,
    ) -> AIEvaluationReleaseDecision | None:
        result = await db.execute(
            select(AIEvaluationReleaseDecision).where(
                AIEvaluationReleaseDecision.id == decision_id,
                AIEvaluationReleaseDecision.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def count_release_decisions_for_tenant(
        db: AsyncSession,
        *,
        organization_id: int,
    ) -> int:
        result = await db.execute(
            select(func.count())
            .select_from(AIEvaluationReleaseDecision)
            .where(AIEvaluationReleaseDecision.organization_id == organization_id)
        )
        return int(result.scalar_one())
