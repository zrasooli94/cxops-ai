from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_run import AgentRun
from app.models.integration_job import (
    IntegrationJob,
)
from app.schemas.observability import (
    AgentObservabilitySummary,
    IntegrationJobObservabilitySummary,
)


class AgentObservabilityService:

    @staticmethod
    def percentage(
        value: int,
        total: int,
    ) -> float:

        if total == 0:
            return 0.0

        return round(
            (value / total) * 100,
            2,
        )

    @staticmethod
    async def total_runs(
        db: AsyncSession,
    ) -> int:

        result = await db.execute(
            select(
                func.count(
                    AgentRun.id
                )
            )
        )

        return int(
            result.scalar_one()
        )

    @staticmethod
    async def action_distribution(
        db: AsyncSession,
    ) -> dict[str, int]:

        result = await db.execute(
            select(
                AgentRun.action,
                func.count(
                    AgentRun.id
                ),
            )
            .group_by(
                AgentRun.action
            )
            .order_by(
                AgentRun.action
            )
        )

        return {
            str(action): int(count)
            for action, count
            in result.all()
        }

    @staticmethod
    async def status_distribution(
        db: AsyncSession,
    ) -> dict[str, int]:

        result = await db.execute(
            select(
                AgentRun.status,
                func.count(
                    AgentRun.id
                ),
            )
            .group_by(
                AgentRun.status
            )
            .order_by(
                AgentRun.status
            )
        )

        return {
            str(status): int(count)
            for status, count
            in result.all()
        }

    @staticmethod
    async def count_runs(
        db: AsyncSession,
        *conditions,
    ) -> int:

        result = await db.execute(
            select(
                func.count(
                    AgentRun.id
                )
            )
            .where(
                *conditions
            )
        )

        return int(
            result.scalar_one()
        )

    @staticmethod
    async def tool_metrics(
        db: AsyncSession,
    ) -> dict:

        result = await db.execute(
            select(
                AgentRun.tool_plan
            )
        )

        plans = result.scalars().all()

        tool_usage = Counter()
        risk_levels = Counter()

        approval_gated_tools = 0
        authorized_tools = 0

        auto_execution_eligible_runs = 0

        for plan in plans:

            if not plan:
                continue

            executable_tools = []

            for tool in plan:

                if not isinstance(
                    tool,
                    dict,
                ):
                    continue

                tool_name = str(
                    tool.get(
                        "tool",
                        "unknown",
                    )
                )

                if tool_name != "none":

                    tool_usage[
                        tool_name
                    ] += 1

                risk_level = str(
                    tool.get(
                        "risk_level",
                        "unknown",
                    )
                )

                risk_levels[
                    risk_level
                ] += 1

                if (
                    tool.get(
                        "requires_approval"
                    )
                    is True
                ):

                    approval_gated_tools += 1

                if (
                    tool.get(
                        "authorized"
                    )
                    is True
                ):

                    authorized_tools += 1

                if tool_name not in {
                    "none",
                    "human.review",
                }:

                    executable_tools.append(
                        tool
                    )

            if (
                executable_tools
                and all(
                    tool.get(
                        "authorized"
                    )
                    is True
                    and tool.get(
                        "requires_approval"
                    )
                    is not True
                    for tool
                    in executable_tools
                )
            ):

                auto_execution_eligible_runs += 1

        return {
            "tool_usage": dict(
                sorted(
                    tool_usage.items()
                )
            ),
            "risk_levels": dict(
                sorted(
                    risk_levels.items()
                )
            ),
            "approval_gated_tools": (
                approval_gated_tools
            ),
            "authorized_tools": (
                authorized_tools
            ),
            "auto_execution_eligible_runs": (
                auto_execution_eligible_runs
            ),
        }

    @classmethod
    async def integration_job_metrics(
        cls,
        db: AsyncSession,
    ) -> IntegrationJobObservabilitySummary:

        total_result = await db.execute(
            select(
                func.count(
                    IntegrationJob.id
                )
            )
        )

        total = int(
            total_result.scalar_one()
        )

        status_result = await db.execute(
            select(
                IntegrationJob.status,
                func.count(
                    IntegrationJob.id
                ),
            )
            .group_by(
                IntegrationJob.status
            )
            .order_by(
                IntegrationJob.status
            )
        )

        statuses = {
            str(status): int(count)
            for status, count
            in status_result.all()
        }

        type_result = await db.execute(
            select(
                IntegrationJob.job_type,
                func.count(
                    IntegrationJob.id
                ),
            )
            .group_by(
                IntegrationJob.job_type
            )
            .order_by(
                IntegrationJob.job_type
            )
        )

        job_types = {
            str(job_type): int(count)
            for job_type, count
            in type_result.all()
        }

        attempts_result = await db.execute(
            select(
                func.coalesce(
                    func.sum(
                        IntegrationJob.attempts
                    ),
                    0,
                )
            )
        )

        total_attempts = int(
            attempts_result.scalar_one()
        )

        retried_result = await db.execute(
            select(
                func.count(
                    IntegrationJob.id
                )
            )
            .where(
                IntegrationJob.attempts
                > 1
            )
        )

        retried_jobs = int(
            retried_result.scalar_one()
        )

        exhausted_result = await db.execute(
            select(
                func.count(
                    IntegrationJob.id
                )
            )
            .where(
                IntegrationJob.status
                == "failed"
            )
            .where(
                IntegrationJob.attempts
                >= IntegrationJob.max_attempts
            )
        )

        exhausted_jobs = int(
            exhausted_result.scalar_one()
        )

        completed_jobs = statuses.get(
            "completed",
            0,
        )

        failed_jobs = statuses.get(
            "failed",
            0,
        )

        return IntegrationJobObservabilitySummary(
            total=total,
            statuses=statuses,
            job_types=job_types,
            total_attempts=(
                total_attempts
            ),
            retried_jobs=(
                retried_jobs
            ),
            retry_rate=(
                cls.percentage(
                    retried_jobs,
                    total,
                )
            ),
            completed_jobs=(
                completed_jobs
            ),
            failed_jobs=(
                failed_jobs
            ),
            exhausted_jobs=(
                exhausted_jobs
            ),
        )

    @classmethod
    async def summary(
        cls,
        db: AsyncSession,
    ) -> AgentObservabilitySummary:

        total_runs = await cls.total_runs(
            db
        )

        actions = await (
            cls.action_distribution(
                db
            )
        )

        statuses = await (
            cls.status_distribution(
                db
            )
        )

        human_approval_required = await (
            cls.count_runs(
                db,
                AgentRun
                .requires_human_approval
                .is_(True),
            )
        )

        reviewed_runs = await (
            cls.count_runs(
                db,
                AgentRun.reviewed_at
                .is_not(None),
            )
        )

        executed_runs = await (
            cls.count_runs(
                db,
                AgentRun.status
                == "executed",
            )
        )

        execution_failed_runs = await (
            cls.count_runs(
                db,
                AgentRun.status
                == "execution_failed",
            )
        )

        attempted_executions = (
            executed_runs
            + execution_failed_runs
        )

        tool_metrics = await (
            cls.tool_metrics(
                db
            )
        )

        integration_jobs = await (
            cls.integration_job_metrics(
                db
            )
        )

        auto_execution_eligible_runs = (
            tool_metrics[
                "auto_execution_eligible_runs"
            ]
        )

        return AgentObservabilitySummary(
            generated_at=datetime.now(
                timezone.utc
            ),
            total_runs=(
                total_runs
            ),
            actions=(
                actions
            ),
            statuses=(
                statuses
            ),
            human_approval_required=(
                human_approval_required
            ),
            human_approval_rate=(
                cls.percentage(
                    human_approval_required,
                    total_runs,
                )
            ),
            reviewed_runs=(
                reviewed_runs
            ),
            review_rate=(
                cls.percentage(
                    reviewed_runs,
                    total_runs,
                )
            ),
            executed_runs=(
                executed_runs
            ),
            execution_failed_runs=(
                execution_failed_runs
            ),
            execution_success_rate=(
                cls.percentage(
                    executed_runs,
                    attempted_executions,
                )
            ),
            auto_execution_eligible_runs=(
                auto_execution_eligible_runs
            ),
            auto_execution_eligible_rate=(
                cls.percentage(
                    auto_execution_eligible_runs,
                    total_runs,
                )
            ),
            tool_usage=(
                tool_metrics[
                    "tool_usage"
                ]
            ),
            tool_risk_levels=(
                tool_metrics[
                    "risk_levels"
                ]
            ),
            approval_gated_tools=(
                tool_metrics[
                    "approval_gated_tools"
                ]
            ),
            authorized_tools=(
                tool_metrics[
                    "authorized_tools"
                ]
            ),
            integration_jobs=(
                integration_jobs
            ),
        )