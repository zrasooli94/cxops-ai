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


AGENT_SPECIALIST_SELECTED_TOTAL = Counter(
    "cxops_agent_specialist_selected_total",
    "Total times each agent specialist executed.",
    [
        "specialist",
    ],
)


AGENT_HANDOFF_TOTAL = Counter(
    "cxops_agent_handoff_total",
    "Specialist handoffs during agent analysis.",
    [
        "from_specialist",
        "to_specialist",
    ],
)


AGENT_SPECIALIST_FAILURE_TOTAL = Counter(
    "cxops_agent_specialist_failure_total",
    "Agent specialist failures. The label is a bounded specialist name.",
    [
        "specialist",
    ],
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
# Human reply delivery metrics
# =====================================================

HUMAN_REPLY_DELIVERIES_TOTAL = Counter(
    "cxops_human_reply_deliveries_total",
    "Human reply delivery outcomes.",
    [
        "provider",
        "outcome",
    ],
)


# =====================================================
# Service operations metrics
# =====================================================

TICKET_ROUTED_TOTAL = Counter(
    "cxops_ticket_routed_total",
    "Tickets routed to a service queue.",
    [
        "routing_source",
    ],
)


TICKET_CLAIMED_TOTAL = Counter(
    "cxops_ticket_claimed_total",
    "Tickets claimed by an agent.",
    [
        "routing_source",
    ],
)


SLA_ESCALATIONS_TRIGGERED_TOTAL = Counter(
    "cxops_sla_escalations_triggered_total",
    "SLA escalation transitions triggered.",
    [
        "milestone",
        "stage",
    ],
)


SLA_ESCALATIONS_ACKNOWLEDGED_TOTAL = Counter(
    "cxops_sla_escalations_acknowledged_total",
    "SLA escalations acknowledged by a supervisor.",
    [
        "milestone",
        "stage",
    ],
)


SERVICE_TRANSFORMATION_REQUESTS_TOTAL = Counter(
    "cxops_service_transformation_requests_total",
    "Service transformation analytics requests.",
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


def record_agent_specialist_selected(
    *,
    specialist: str,
) -> None:

    AGENT_SPECIALIST_SELECTED_TOTAL.labels(
        specialist=specialist,
    ).inc()


def record_agent_handoff(
    *,
    from_specialist: str,
    to_specialist: str,
) -> None:

    AGENT_HANDOFF_TOTAL.labels(
        from_specialist=from_specialist,
        to_specialist=to_specialist,
    ).inc()


def record_agent_specialist_failure(
    *,
    specialist: str,
) -> None:

    AGENT_SPECIALIST_FAILURE_TOTAL.labels(
        specialist=specialist,
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


def record_human_reply_delivery(
    *,
    provider: str,
    outcome: str,
) -> None:

    HUMAN_REPLY_DELIVERIES_TOTAL.labels(
        provider=provider,
        outcome=outcome,
    ).inc()


def record_ticket_routed(
    *,
    routing_source: str,
) -> None:
    TICKET_ROUTED_TOTAL.labels(
        routing_source=routing_source,
    ).inc()


def record_ticket_claimed(
    *,
    routing_source: str,
) -> None:
    TICKET_CLAIMED_TOTAL.labels(
        routing_source=routing_source,
    ).inc()


def record_escalation_transition(
    *,
    milestone: str,
    stage: str,
) -> None:
    SLA_ESCALATIONS_TRIGGERED_TOTAL.labels(
        milestone=milestone,
        stage=stage,
    ).inc()


def record_escalation_acknowledged(
    *,
    milestone: str,
    stage: str,
) -> None:
    SLA_ESCALATIONS_ACKNOWLEDGED_TOTAL.labels(
        milestone=milestone,
        stage=stage,
    ).inc()


def record_service_transformation_request() -> None:
    SERVICE_TRANSFORMATION_REQUESTS_TOTAL.inc()


# =====================================================
# Public chat metrics (Phase 1P.3)
# =====================================================
#
# These carry lifecycle labels only. No customer identifier, session token,
# widget key, message body, IP address, or origin is ever used as a metric label:
# labels are a cardinality and privacy hazard, and an unbounded value there
# would both leak PII and grow the scrape response without limit.

PUBLIC_CHAT_SESSIONS_TOTAL = Counter(
    "cxops_public_chat_sessions_total",
    "Public chat sessions created.",
    [
        "outcome",
    ],
)


PUBLIC_CHAT_MESSAGES_TOTAL = Counter(
    "cxops_public_chat_messages_total",
    "Customer messages processed by the public widget.",
    [
        "outcome",
    ],
)


PUBLIC_CHAT_HANDOFF_REQUESTS_TOTAL = Counter(
    "cxops_public_chat_handoff_requests_total",
    "Customer-initiated human handoff requests.",
    [
        "outcome",
    ],
)


PUBLIC_CHAT_SESSIONS_CLOSED_TOTAL = Counter(
    "cxops_public_chat_sessions_closed_total",
    "Public chat sessions closed, by the closing party.",
    [
        "closed_by",
    ],
)


PUBLIC_CHAT_REJECTIONS_TOTAL = Counter(
    "cxops_public_chat_rejections_total",
    "Public chat requests refused before any work was done.",
    [
        "reason",
    ],
)


def record_public_chat_session(*, outcome: str) -> None:
    PUBLIC_CHAT_SESSIONS_TOTAL.labels(outcome=outcome).inc()


def record_public_chat_message(*, outcome: str) -> None:
    PUBLIC_CHAT_MESSAGES_TOTAL.labels(outcome=outcome).inc()


def record_public_chat_handoff(*, outcome: str) -> None:
    PUBLIC_CHAT_HANDOFF_REQUESTS_TOTAL.labels(outcome=outcome).inc()


def record_public_chat_session_closed(*, closed_by: str) -> None:
    PUBLIC_CHAT_SESSIONS_CLOSED_TOTAL.labels(closed_by=closed_by).inc()


def record_public_chat_rejection(*, reason: str) -> None:
    PUBLIC_CHAT_REJECTIONS_TOTAL.labels(reason=reason).inc()


# Phase 1R: why a public-chat turn was (or was not) answered automatically.
# ``outcome`` is a closed set (see ``record_public_chat_answer_decision``);
# customer text, session ids and tenant ids are deliberately never labels.
PUBLIC_CHAT_ANSWER_DECISIONS_TOTAL = Counter(
    "cxops_public_chat_answer_decisions_total",
    "Public chat AI turns by the reason they were answered or handed off.",
    [
        "outcome",
    ],
)

PUBLIC_CHAT_TURN_STAGE_SECONDS = Histogram(
    "cxops_public_chat_turn_stage_seconds",
    "Wall-clock seconds spent in each stage of a public chat turn.",
    [
        "stage",
    ],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 16, 32),
)

EMBEDDING_QUERY_CACHE_TOTAL = Counter(
    "cxops_embedding_query_cache_total",
    "Query-embedding cache lookups.",
    [
        "result",
    ],
)

PUBLIC_CHAT_ANSWER_OUTCOMES = frozenset(
    {
        "grounded_reply",
        "stale_handoff_suppressed",
        "skip_flag_off",
        "skip_no_run",
        "skip_action_not_respond",
        "skip_intent_not_information",
        "skip_business_tool",
        "ineligible_not_grounded",
        "ineligible_no_sources",
        "ineligible_empty_answer",
        "ineligible_no_similarity",
        "ineligible_fallback_answer",
        "ineligible_invalid_citations",
        "ineligible_invalid_sources",
        "ineligible_invalid_similarity",
        "ineligible_low_similarity",
        "ineligible_overlong_answer",
        "ineligible_oversized_evidence",
        "ineligible_explicit_source_conflict",
        "ineligible_empty_after_strip",
        "rag_error",
        "allowance_exhausted",
    }
)

PUBLIC_CHAT_TURN_STAGES = frozenset(
    {
        "validate",
        "persist_inbound",
        "enqueue",
        "allowance",
        "agent",
        "rag",
        "persist_reply",
    }
)


def record_public_chat_answer_decision(*, outcome: str) -> None:
    if outcome not in PUBLIC_CHAT_ANSWER_OUTCOMES:
        outcome = "other"
    PUBLIC_CHAT_ANSWER_DECISIONS_TOTAL.labels(outcome=outcome).inc()


def record_public_chat_turn_stage(*, stage: str, seconds: float) -> None:
    if stage not in PUBLIC_CHAT_TURN_STAGES:
        return
    PUBLIC_CHAT_TURN_STAGE_SECONDS.labels(stage=stage).observe(max(0.0, seconds))


def record_embedding_cache(*, result: str) -> None:
    EMBEDDING_QUERY_CACHE_TOTAL.labels(result="hit" if result == "hit" else "miss").inc()


# Phase 1U-A: staff handoff notification outcomes. Closed label set; no tenant,
# destination, session or customer value is ever a label.
HANDOFF_NOTIFICATIONS_TOTAL = Counter(
    "cxops_handoff_notifications_total",
    "Staff handoff notification job outcomes.",
    ["outcome"],
)
HANDOFF_NOTIFICATION_OUTCOMES = frozenset(
    {
        "staged",
        "provider_accepted",
        "channel_accepted_email",
        "channel_accepted_telegram",
        "suppressed_disabled",
        "suppressed_inactive",
        "suppressed_stale",
        "suppressed_unconfigured",
        "suppressed_rate_limited",
        "suppressed_nothing_waiting",
        "deferred_config_missing",
        "deferred_busy",
        "transport_error",
    }
)


def record_handoff_notification(*, outcome: str) -> None:
    if outcome not in HANDOFF_NOTIFICATION_OUTCOMES:
        outcome = "transport_error"
    HANDOFF_NOTIFICATIONS_TOTAL.labels(outcome=outcome).inc()
