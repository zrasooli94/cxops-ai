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

class AgentOperationalKPIs(BaseModel):
    total_runs: int

    escalation_rate: float
    human_review_rate: float
    no_action_rate: float

    approval_required_rate: float
    pending_approval_rate: float

    auto_approved_runs: int
    autonomous_execution_rate: float

    autonomous_executed_runs: int
    autonomous_success_rate: float

    execution_success_rate: float

    queue_retry_rate: float
    queue_failure_rate: float

    average_job_attempts: float

class AgentROISummary(BaseModel):
    total_runs: int

    instrumented_runs: int
    instrumented_autonomous_executed_runs: int

    support_hourly_cost_usd: float
    minutes_saved_per_autonomous_execution: float

    estimated_minutes_saved: float
    estimated_hours_saved: float

    estimated_labor_savings_usd: float

    agent_ai_cost_usd: float
    estimated_net_savings_usd: float

    pricing_configured: bool
    roi_percent: float | None

    measurement_status: str
    minimum_autonomous_samples: int
    sample_size_sufficient: bool
    provisional_roi_percent: float | None
    roi_percent: float | None