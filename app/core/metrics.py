from prometheus_client import (
    Counter,
    Histogram,
)

# =====================================================
# Agent decision metrics
# =====================================================

AGENT_DECISIONS_TOTAL = Counter(
    "cxops_agent_decisions_total",
    "Total production agent decisions.",
    [
        "action",
        "decision_path",
    ],
)


AGENT_DECISION_LATENCY_SECONDS = Histogram(
    "cxops_agent_decision_latency_seconds",
    "Agent decision latency in seconds.",
    [
        "decision_path",
    ],
)


AGENT_DECISION_TOKENS_TOTAL = Counter(
    "cxops_agent_decision_tokens_total",
    "Tokens consumed by agent decisions.",
    [
        "token_type",
        "decision_path",
    ],
)


AGENT_RAG_SOURCES_TOTAL = Counter(
    "cxops_agent_rag_sources_total",
    "Total RAG sources used by production agent decisions.",
)


# =====================================================
# Approval metrics
# =====================================================

AGENT_APPROVALS_TOTAL = Counter(
    "cxops_agent_approvals_total",
    "Human approval decisions for agent runs.",
    [
        "result",
    ],
)


AGENT_AUTO_APPROVALS_TOTAL = Counter(
    "cxops_agent_auto_approvals_total",
    "Agent runs automatically approved by low-risk policy.",
    [
        "action",
    ],
)


# =====================================================
# Tool / execution metrics
# =====================================================

AGENT_TOOL_EXECUTIONS_TOTAL = Counter(
    "cxops_agent_tool_executions_total",
    "Successfully executed agent tools.",
    [
        "tool",
    ],
)


AGENT_EXECUTION_FAILURES_TOTAL = Counter(
    "cxops_agent_execution_failures_total",
    "Failed agent execution attempts.",
    [
        "action",
    ],
)


AUTONOMOUS_EXECUTIONS_TOTAL = Counter(
    "cxops_autonomous_executions_total",
    "Successfully completed autonomous agent executions.",
    [
        "action",
        "outcome",
    ],
)


# =====================================================
# Durable integration queue metrics
# =====================================================

INTEGRATION_JOBS_COMPLETED_TOTAL = Counter(
    "cxops_integration_jobs_completed_total",
    "Completed integration jobs.",
    [
        "job_type",
    ],
)


INTEGRATION_JOB_RETRIES_TOTAL = Counter(
    "cxops_integration_job_retries_total",
    "Integration jobs scheduled for retry.",
    [
        "job_type",
    ],
)


INTEGRATION_JOB_FAILURES_TOTAL = Counter(
    "cxops_integration_job_failures_total",
    "Integration jobs that exhausted retries.",
    [
        "job_type",
    ],
)


# =====================================================
# Recording helpers
# =====================================================


def record_agent_decision(
    *,
    action: str,
    llm_called: bool,
    latency_ms: float,
    retrieval_count: int,
    input_tokens: int,
    output_tokens: int,
) -> None:

    decision_path = "llm" if llm_called else "deterministic"

    AGENT_DECISIONS_TOTAL.labels(
        action=action,
        decision_path=decision_path,
    ).inc()

    AGENT_DECISION_LATENCY_SECONDS.labels(
        decision_path=decision_path,
    ).observe(latency_ms / 1000)

    AGENT_DECISION_TOKENS_TOTAL.labels(
        token_type="input",
        decision_path=decision_path,
    ).inc(input_tokens)

    AGENT_DECISION_TOKENS_TOTAL.labels(
        token_type="output",
        decision_path=decision_path,
    ).inc(output_tokens)

    if retrieval_count > 0:
        AGENT_RAG_SOURCES_TOTAL.inc(retrieval_count)


def record_agent_approval(
    *,
    result: str,
) -> None:

    AGENT_APPROVALS_TOTAL.labels(
        result=result,
    ).inc()


def record_agent_auto_approval(
    *,
    action: str,
) -> None:

    AGENT_AUTO_APPROVALS_TOTAL.labels(
        action=action,
    ).inc()


def record_agent_tool_execution(
    *,
    tool: str,
) -> None:

    AGENT_TOOL_EXECUTIONS_TOTAL.labels(
        tool=tool,
    ).inc()


def record_agent_execution_failure(
    *,
    action: str,
) -> None:

    AGENT_EXECUTION_FAILURES_TOTAL.labels(
        action=action,
    ).inc()


def record_autonomous_execution(
    *,
    action: str,
    outcome: str,
) -> None:

    AUTONOMOUS_EXECUTIONS_TOTAL.labels(
        action=action,
        outcome=outcome,
    ).inc()


def record_integration_job_completed(
    *,
    job_type: str,
) -> None:

    INTEGRATION_JOBS_COMPLETED_TOTAL.labels(
        job_type=job_type,
    ).inc()


def record_integration_job_retry(
    *,
    job_type: str,
) -> None:

    INTEGRATION_JOB_RETRIES_TOTAL.labels(
        job_type=job_type,
    ).inc()


def record_integration_job_failure(
    *,
    job_type: str,
) -> None:

    INTEGRATION_JOB_FAILURES_TOTAL.labels(
        job_type=job_type,
    ).inc()
