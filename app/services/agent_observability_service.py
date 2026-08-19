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
from app.core.config import settings
from app.models.ai_request_log import AIRequestLog

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

    #
    @classmethod
    async def operational_kpis(
        cls,
        db: AsyncSession,
    ) -> dict:

        total_runs = await cls.total_runs(
            db
        )

        actions = await cls.action_distribution(
            db
        )

        statuses = await cls.status_distribution(
            db
        )

        approval_required = await cls.count_runs(
            db,
            AgentRun
            .requires_human_approval
            .is_(True),
        )

        auto_approved_result = await db.execute(
            select(
                func.count(
                    AgentRun.id
                )
            )
            .where(
                AgentRun.reviewer_note
                == (
                    "Automatically approved "
                    "by low-risk tool policy"
                )
            )
        )

        auto_approved_runs = int(
            auto_approved_result.scalar_one()
        )

        autonomous_executed_result = (
            await db.execute(
                select(
                    func.count(
                        AgentRun.id
                    )
                )
                .where(
                    AgentRun.reviewer_note
                    == (
                        "Automatically approved "
                        "by low-risk tool policy"
                    )
                )
                .where(
                    AgentRun.status
                    == "executed"
                )
            )
        )

        autonomous_executed_runs = int(
            autonomous_executed_result
            .scalar_one()
        )

        executed_runs = statuses.get(
            "executed",
            0,
        )

        failed_execution_runs = (
            statuses.get(
                "execution_failed",
                0,
            )
        )

        attempted_executions = (
            executed_runs
            + failed_execution_runs
        )

        jobs = await cls.integration_job_metrics(
            db
        )

        average_job_attempts = (
            jobs.total_attempts
            / jobs.total
            if jobs.total
            else 0.0
        )

        return {
            "total_runs": total_runs,

            "escalation_rate": (
                cls.percentage(
                    actions.get(
                        "escalate",
                        0,
                    ),
                    total_runs,
                )
            ),

            "human_review_rate": (
                cls.percentage(
                    actions.get(
                        "human_review",
                        0,
                    ),
                    total_runs,
                )
            ),

            "no_action_rate": (
                cls.percentage(
                    actions.get(
                        "no_action",
                        0,
                    ),
                    total_runs,
                )
            ),

            "approval_required_rate": (
                cls.percentage(
                    approval_required,
                    total_runs,
                )
            ),

            "pending_approval_rate": (
                cls.percentage(
                    statuses.get(
                        "pending_approval",
                        0,
                    ),
                    total_runs,
                )
            ),

            "auto_approved_runs": (
                auto_approved_runs
            ),

            "autonomous_execution_rate": (
                cls.percentage(
                    auto_approved_runs,
                    total_runs,
                )
            ),

            "autonomous_executed_runs": (
                autonomous_executed_runs
            ),

            "autonomous_success_rate": (
                cls.percentage(
                    autonomous_executed_runs,
                    auto_approved_runs,
                )
            ),

            "execution_success_rate": (
                cls.percentage(
                    executed_runs,
                    attempted_executions,
                )
            ),

            "queue_retry_rate": (
                jobs.retry_rate
            ),

            "queue_failure_rate": (
                cls.percentage(
                    jobs.failed_jobs,
                    jobs.total,
                )
            ),

            "average_job_attempts": round(
                average_job_attempts,
                2,
            ),
        }
    
    @classmethod
    async def roi_summary(
        cls,
        db: AsyncSession,
    ) -> dict:

        total_runs = await cls.total_runs(
            db
        )

        # Match a production AgentRun to its
        # corresponding AI telemetry row.
        match_condition = (
            AIRequestLog.request_id
            == func.concat(
                "agent-",
                AgentRun.run_id,
            )
        )

        instrumented_result = await db.execute(
            select(
                func.count(
                    AgentRun.id
                )
            )
            .join(
                AIRequestLog,
                match_condition,
            )
            .where(
                AIRequestLog.feature
                == "agent_decision"
            )
        )

        instrumented_runs = int(
            instrumented_result.scalar_one()
        )

        autonomous_result = await db.execute(
            select(
                func.count(
                    AgentRun.id
                )
            )
            .join(
                AIRequestLog,
                match_condition,
            )
            .where(
                AIRequestLog.feature
                == "agent_decision"
            )
            .where(
                AgentRun.reviewer_note
                == (
                    "Automatically approved "
                    "by low-risk tool policy"
                )
            )
            .where(
                AgentRun.status
                == "executed"
            )
        )

        instrumented_autonomous_executed_runs = int(
            autonomous_result.scalar_one()
        )

        ai_cost_result = await db.execute(
            select(
                func.coalesce(
                    func.sum(
                        AIRequestLog
                        .estimated_cost_usd
                    ),
                    0.0,
                )
            )
            .join(
                AgentRun,
                match_condition,
            )
            .where(
                AIRequestLog.feature
                == "agent_decision"
            )
        )

        agent_ai_cost_usd = float(
            ai_cost_result.scalar_one()
            or 0.0
        )

        minutes_saved = (
            instrumented_autonomous_executed_runs
            * settings
            .minutes_saved_per_autonomous_execution
        )

        hours_saved = (
            minutes_saved / 60
        )

        labor_savings = (
            hours_saved
            * settings.support_hourly_cost_usd
        )

        net_savings = (
            labor_savings
            - agent_ai_cost_usd
        )

        pricing_configured = (
            settings.llm_input_cost_per_million
            > 0
            or
            settings.llm_output_cost_per_million
            > 0
        )

        roi_percent = None

        if (
            pricing_configured
            and agent_ai_cost_usd > 0
        ):
            roi_percent = round(
                (
                    net_savings
                    / agent_ai_cost_usd
                )
                * 100,
                2,
            )

        return {
            "total_runs": total_runs,

            "instrumented_runs": (
                instrumented_runs
            ),

            "instrumented_autonomous_executed_runs": (
                instrumented_autonomous_executed_runs
            ),

            "support_hourly_cost_usd": (
                settings.support_hourly_cost_usd
            ),

            "minutes_saved_per_autonomous_execution": (
                settings
                .minutes_saved_per_autonomous_execution
            ),

            "estimated_minutes_saved": round(
                minutes_saved,
                2,
            ),

            "estimated_hours_saved": round(
                hours_saved,
                2,
            ),

            "estimated_labor_savings_usd": round(
                labor_savings,
                2,
            ),

            "agent_ai_cost_usd": round(
                agent_ai_cost_usd,
                6,
            ),

            "estimated_net_savings_usd": round(
                net_savings,
                6,
            ),

            "pricing_configured": (
                pricing_configured
            ),

            "roi_percent": roi_percent,
        }