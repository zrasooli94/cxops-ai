from typing import Any, Literal

from pydantic import BaseModel, Field, validator


class AgentDecision(BaseModel):
    action: Literal[
        "respond",
        "route",
        "escalate",
        "internal_note",
        "human_review",
        "no_action",
    ]

    reason: str

    recommended_team: str | None = None

    recommended_priority: (
        Literal[
            "low",
            "normal",
            "high",
            "urgent",
        ]
        | None
    ) = None

    response_draft: str | None = None

    requires_human_approval: bool = True

    model_config = {"extra": "forbid"}


class KnowledgeNeedDecision(BaseModel):
    needs_knowledge: bool
    reason: str

    model_config = {"extra": "forbid"}


class AgentToolCall(BaseModel):
    tool: Literal[
        "zendesk.update_ticket",
        "zendesk.add_internal_note",
        "zendesk.send_reply",
        "human.review",
        "none",
    ]

    arguments: dict[str, Any] = Field(default_factory=dict)

    risk_level: Literal[
        "low",
        "medium",
        "high",
    ] = "low"

    requires_approval: bool = True

    required_capability: str | None = None

    authorized: bool = False

    @validator("arguments", always=True)
    def enforce_no_forbidden_arg_keys(cls, v):
        """Reject forbidden keys in tool arguments (Phase 1D.3)."""
        if v is None:
            return {}
        forbidden = {
            "organization_id",
            "tenant_id",
            "integration_id",
            "credential_id",
            "zendesk_ticket_id",
            "external_ticket_id",
        }
        for key in v:
            if key in forbidden:
                raise ValueError(
                    "Tool argument '" + str(key) + "' is forbidden and must not be supplied."
                )
        return v

    model_config = {"extra": "forbid"}


class AgentAnalysisResponse(BaseModel):
    run_id: str
    ticket_id: int

    decision: AgentDecision

    sources: list[Any] = Field(default_factory=list)

    workflow_path: list[str] = Field(default_factory=list)

    tool_plan: list[AgentToolCall] = Field(default_factory=list)
    auto_queued: bool = False

    job_id: int | None = None

    reused: bool = False

    fingerprint: str | None = None


class AgentReviewRequest(BaseModel):
    note: str | None = Field(
        default=None,
        max_length=2000,
    )

    model_config = {"extra": "forbid"}


class AgentRunResponse(BaseModel):
    run_id: str
    ticket_id: int
    organization_id: int | None = None
    action: str
    status: str
    reason: str

    recommended_team: str | None = None

    recommended_priority: str | None = None

    response_draft: str | None = None

    requires_human_approval: bool

    reviewer_note: str | None = None

    workflow_path: list[str] = Field(default_factory=list)

    tool_plan: list[AgentToolCall] = Field(default_factory=list)

    sources: list[Any] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class AgentExecutionResponse(BaseModel):
    run_id: str
    ticket_id: int
    status: str
    action: str
    external_ticket_id: str | None
    executed: bool
    duplicate: bool = False
    message: str

    model_config = {"extra": "forbid"}


class AgentExecutionQueuedResponse(BaseModel):
    run_id: str
    job_id: int
    status: str
    duplicate: bool

    model_config = {"extra": "forbid"}