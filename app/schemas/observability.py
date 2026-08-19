from datetime import datetime

from pydantic import BaseModel


class AIObservabilitySummary(BaseModel):
    total_requests: int
    success_rate: float
    grounded_rate: float
    avg_latency_ms: float
    total_tokens: int
    estimated_cost_usd: float


class IntegrationJobObservabilitySummary(BaseModel):
    total: int

    statuses: dict[str, int]
    job_types: dict[str, int]

    total_attempts: int

    retried_jobs: int
    retry_rate: float

    completed_jobs: int
    failed_jobs: int
    exhausted_jobs: int


class AgentObservabilitySummary(BaseModel):
    generated_at: datetime

    total_runs: int

    actions: dict[str, int]
    statuses: dict[str, int]

    human_approval_required: int
    human_approval_rate: float

    reviewed_runs: int
    review_rate: float

    executed_runs: int
    execution_failed_runs: int
    execution_success_rate: float

    auto_execution_eligible_runs: int
    auto_execution_eligible_rate: float

    tool_usage: dict[str, int]
    tool_risk_levels: dict[str, int]

    approval_gated_tools: int
    authorized_tools: int

    integration_jobs: (
        IntegrationJobObservabilitySummary
    )

class AIFeatureObservabilitySummary(BaseModel):
    feature: str

    total_requests: int

    success_rate: float
    grounded_rate: float
    llm_call_rate: float

    avg_latency_ms: float

    input_tokens: int
    output_tokens: int
    total_tokens: int

    estimated_cost_usd: float

    models: dict[str, int]


class AIObservabilityBreakdown(BaseModel):
    overall: AIObservabilitySummary

    features: list[
        AIFeatureObservabilitySummary
    ]