from prometheus_client import (
    Counter,
    Histogram,
)


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


def record_agent_decision(
    *,
    action: str,
    llm_called: bool,
    latency_ms: float,
    retrieval_count: int,
    input_tokens: int,
    output_tokens: int,
) -> None:

    decision_path = (
        "llm"
        if llm_called
        else "deterministic"
    )

    AGENT_DECISIONS_TOTAL.labels(
        action=action,
        decision_path=decision_path,
    ).inc()

    AGENT_DECISION_LATENCY_SECONDS.labels(
        decision_path=decision_path,
    ).observe(
        latency_ms / 1000
    )

    AGENT_DECISION_TOKENS_TOTAL.labels(
        token_type="input",
        decision_path=decision_path,
    ).inc(
        input_tokens
    )

    AGENT_DECISION_TOKENS_TOTAL.labels(
        token_type="output",
        decision_path=decision_path,
    ).inc(
        output_tokens
    )

    if retrieval_count > 0:
        AGENT_RAG_SOURCES_TOTAL.inc(
            retrieval_count
        )