from datetime import datetime

from pydantic import BaseModel


class ServiceTransformationWindow(BaseModel):
    days: int
    current_start: datetime
    current_end: datetime
    previous_start: datetime
    previous_end: datetime


class ServiceTransformationComparison(BaseModel):
    current: int | float | None = None
    previous: int | float | None = None
    absolute_change: int | float | None = None
    percent_change: float | None = None


class ServiceTransformationVolume(BaseModel):
    tickets_created: int
    tickets_resolved: int
    currently_open: int
    currently_needs_response: int


class ServiceTransformationPerformance(BaseModel):
    average_first_response_minutes: float | None = None
    median_first_response_minutes: float | None = None
    average_resolution_time_minutes: float | None = None
    median_resolution_time_minutes: float | None = None


class ServiceTransformationSLA(BaseModel):
    first_response_sla_breaches: int
    resolution_sla_breaches: int
    total_sla_breaches: int
    due_soon: int
    escalation_count: int
    escalation_rate: float | None = None
    reopened_tickets: int
    reopen_rate: float | None = None


class ServiceTransformationAIAdoption(BaseModel):
    tickets_analyzed_by_ai: int
    agent_runs: int
    autonomous_executions: int
    human_approval_required: int
    human_approved: int
    human_rejected: int
    successful_agent_executions: int
    failed_agent_executions: int
    no_action_runs: int
    ai_analysis_rate: float | None = None
    autonomous_execution_rate: float | None = None
    human_approval_rate: float | None = None
    execution_success_rate: float | None = None


class ServiceTransformationSpecialistUsage(BaseModel):
    coordinator_runs: int
    knowledge_specialist_runs: int
    action_specialist_runs: int
    knowledge_usage_rate: float | None = None
    pure_action_route_rate: float | None = None
    invalid_specialist_path_count: int


class ServiceTransformationHumanWorkload(BaseModel):
    human_messages_sent: int
    ai_executed_replies: int


class ServiceTransformationValueRealization(BaseModel):
    estimated_minutes_saved: float
    estimated_hours_saved: float
    estimated_labor_savings_usd: float
    agent_ai_cost_usd: float
    estimated_net_savings_usd: float
    pricing_configured: bool
    measurement_status: str
    minimum_autonomous_samples: int
    sample_size_sufficient: bool
    roi_percent: float | None = None


class ServiceTransformationChannelBreakdownItem(BaseModel):
    channel: str
    conversation_count: int
    message_count: int
    percentage: float


class ServiceTransformationQueueBreakdownItem(BaseModel):
    queue_key: str | None
    queue_name: str | None
    open_tickets: int
    needs_response: int
    due_soon: int
    breached: int
    priority_urgent_high: int
    assigned_tickets: int
    resolved_in_window: int
    agent_runs: int
    autonomous_executions: int


class ServiceTransformationOpportunitySignal(BaseModel):
    signal: str
    scope_type: str
    scope_key: str
    evidence: dict[str, int | float]
    suggested_focus: str


class ServiceTransformationSummary(BaseModel):
    generated_at: datetime
    window: ServiceTransformationWindow
    service_volume: ServiceTransformationVolume
    service_performance: ServiceTransformationPerformance
    sla: ServiceTransformationSLA
    ai_adoption: ServiceTransformationAIAdoption
    specialist_usage: ServiceTransformationSpecialistUsage
    human_workload: ServiceTransformationHumanWorkload
    value_realization: ServiceTransformationValueRealization
    queue_breakdown: list[ServiceTransformationQueueBreakdownItem]
    channel_breakdown: list[ServiceTransformationChannelBreakdownItem]
    comparisons: dict[str, ServiceTransformationComparison]
    opportunity_signals: list[ServiceTransformationOpportunitySignal]