"""Pydantic schemas for the AI evaluation database foundation.

Read schemas expose only derived evaluation data; no PII (customer email,
phone, raw ticket/conversation bodies) is ever represented.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TargetType = Literal["rag", "agent", "repeatability", "latency"]


class EvaluationRunCreate(BaseModel):
    run_id: str = Field(min_length=1, max_length=64)
    target_type: TargetType
    model: str = Field(min_length=1, max_length=100)
    embedding_model: str | None = Field(default=None, max_length=100)
    agent_decision_version: str | None = Field(default=None, max_length=30)
    tool_policy_version: int | None = None
    corpus_revision: dict | None = None
    trigger_source: Literal["manual", "ci", "scheduler"] = "manual"
    requested_by_subject: str | None = Field(default=None, max_length=255)

    model_config = ConfigDict(extra="forbid")


class EvaluationRunRead(BaseModel):
    id: int
    run_id: str
    organization_id: int
    target_type: str
    status: str = "queued"
    model: str
    embedding_model: str | None = None
    agent_decision_version: str | None = None
    tool_policy_version: int | None = None
    corpus_revision: dict | None = None
    trigger_source: str = "manual"
    requested_by_subject: str | None = None
    pass_rate: float | None = None
    metrics: dict = Field(default_factory=dict)
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class EvaluationRunListResponse(BaseModel):
    items: list[EvaluationRunRead]
    total: int
    limit: int
    offset: int


class EvaluationRunQueuedResponse(EvaluationRunRead):
    job_id: int
    job_status: str


class EvalRAGCaseInput(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    question: str = Field(min_length=1, max_length=2000)
    expected_sources: list[str] = Field(default_factory=list)
    expected_terms: list[str] = Field(default_factory=list)
    should_refuse: bool = False
    fingerprint: str | None = Field(default=None, max_length=64)

    model_config = ConfigDict(extra="forbid")


class EvalAgentCaseInput(BaseModel):
    ticket_id: int
    expected_action: str = Field(min_length=1, max_length=50)
    expected_retrieval: bool
    expected_tool: str = Field(min_length=1, max_length=100)
    expected_auto_execute: bool
    fingerprint: str | None = Field(default=None, max_length=64)

    model_config = ConfigDict(extra="forbid")


class EvalRAGCaseInputs(BaseModel):
    cases: list[EvalRAGCaseInput] = Field(min_length=1, max_length=100)

    model_config = ConfigDict(extra="forbid")


class EvalAgentCaseInputs(BaseModel):
    cases: list[EvalAgentCaseInput] = Field(min_length=1, max_length=100)

    model_config = ConfigDict(extra="forbid")


class EvaluationCaseCreate(BaseModel):
    case_id: str = Field(min_length=1, max_length=100)
    case_type: Literal["rag", "agent"]
    expected: dict = Field(default_factory=dict)
    actual: dict = Field(default_factory=dict)
    dimensions: dict = Field(default_factory=dict)
    input: dict = Field(default_factory=dict)
    latency_ms: float | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    fingerprint: str | None = Field(default=None, max_length=64)

    model_config = ConfigDict(extra="forbid")


class EvaluationCaseRead(BaseModel):
    id: int
    run_id: int
    organization_id: int
    case_id: str
    case_type: str
    input: dict = Field(default_factory=dict)
    expected: dict = Field(default_factory=dict)
    actual: dict = Field(default_factory=dict)
    dimensions: dict = Field(default_factory=dict)
    latency_ms: float | None = None
    total_tokens: int | None = None
    estimated_cost_usd: float | None = None
    fingerprint: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class EvaluationCaseListResponse(BaseModel):
    items: list[EvaluationCaseRead]
    total: int


class EvaluationBaselineCreate(BaseModel):
    target_type: TargetType
    version: str = Field(min_length=1, max_length=30)
    model: str = Field(min_length=1, max_length=100)
    embedding_model: str | None = Field(default=None, max_length=100)
    agent_decision_version: str | None = Field(default=None, max_length=30)
    tool_policy_version: int | None = None
    corpus_revision: dict | None = None
    pass_rate: float | None = Field(default=None, ge=0, le=1)
    metrics: dict = Field(default_factory=dict)
    cases_count: int = Field(default=0, ge=0)
    created_by_subject: str | None = Field(default=None, max_length=255)

    model_config = ConfigDict(extra="forbid")


class EvaluationBaselineRead(BaseModel):
    id: int
    organization_id: int
    target_type: str
    version: str
    model: str
    embedding_model: str | None = None
    agent_decision_version: str | None = None
    tool_policy_version: int | None = None
    corpus_revision: dict | None = None
    pass_rate: float | None = None
    metrics: dict = Field(default_factory=dict)
    cases_count: int = 0
    created_by_subject: str | None = None
    promoted: bool = False
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


MetricDirection = Literal["improved", "regressed", "same"]


class EvaluationIdentitySnapshot(BaseModel):
    """Version identity for one side of a comparison (never judged)."""

    model: str
    embedding_model: str | None = None
    agent_decision_version: str | None = None
    tool_policy_version: int | None = None
    corpus_revision: dict | None = None

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class EvaluationMetricComparison(BaseModel):
    metric: str
    baseline: float
    candidate: float
    delta: float
    direction: MetricDirection

    model_config = ConfigDict(extra="forbid")


class EvaluationRunComparison(BaseModel):
    """Simple candidate-run vs baseline comparison (Phase 1J.7A only).

    Only metrics present on BOTH the candidate run and the baseline are
    compared. No aggregate/magic score is computed.
    """

    target_type: str
    candidate_pass_rate: float | None = None
    baseline_pass_rate: float | None = None
    pass_rate_delta: float | None = None
    metrics: list[EvaluationMetricComparison] = Field(default_factory=list)
    baseline: EvaluationIdentitySnapshot
    candidate: EvaluationIdentitySnapshot

    model_config = ConfigDict(extra="forbid")


class EvaluationBaselineListResponse(BaseModel):
    items: list[EvaluationBaselineRead]
    total: int


class EvaluationReleaseDecisionCreate(BaseModel):
    """What a client may send when recording a release decision.

    The tenant, deciding subject, and comparison snapshot are never
    client-controlled: they come from the trusted caller and the existing
    comparison service. ``baseline_id`` is the client-chosen reference the
    candidate run is compared against.
    """

    baseline_id: int
    decision: Literal["approved", "rejected"]
    note: str | None = Field(default=None, max_length=1000)

    model_config = ConfigDict(extra="forbid")


class EvaluationReleaseDecisionRead(BaseModel):
    id: int
    organization_id: int
    candidate_run_id: int
    baseline_id: int
    decision: str
    decided_by_subject: str
    note: str | None = None
    comparison_snapshot: dict = Field(default_factory=dict)
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class EvaluationReleaseDecisionListResponse(BaseModel):
    items: list[EvaluationReleaseDecisionRead]
    total: int
    limit: int
    offset: int


class EvaluationReleaseGateIssue(BaseModel):
    """One release-gate finding (Phase 1J.9A).

    ``kind`` is ``critical`` (candidate < baseline on a critical metric),
    ``missing`` (a required critical metric could not be compared), or
    ``warning`` (an ordinary, non-blocking regression). ``baseline`` and
    ``candidate`` carry the compared numeric values when known and stay
    ``None`` for a missing metric.
    """

    metric: str
    kind: str
    baseline: float | None = None
    candidate: float | None = None
    message: str

    model_config = ConfigDict(extra="forbid")


class EvaluationReleaseGateResult(BaseModel):
    """Deterministic release-gate check result (Phase 1J.9A).

    ``blocked`` is true only when a critical metric regressed (candidate <
    baseline, never ``<=``) or a required critical metric is missing. Ordinary
    regressions are collected in ``warnings`` and never set ``blocked`` in this
    phase. Deliberately no aggregate or magic quality score.
    """

    blocked: bool
    target_type: str
    critical_regressions: list[EvaluationReleaseGateIssue] = Field(default_factory=list)
    warnings: list[EvaluationReleaseGateIssue] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")
