from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.knowledge import RAGSource


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


class KnowledgeNeedDecision(BaseModel):
    needs_knowledge: bool
    reason: str


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

    authorized: bool = False


class AgentAnalysisResponse(BaseModel):
    run_id: str
    ticket_id: int

    decision: AgentDecision

    sources: list[RAGSource] = Field(default_factory=list)

    workflow_path: list[str] = Field(default_factory=list)

    tool_plan: list[AgentToolCall] = Field(default_factory=list)
    auto_queued: bool = False

    job_id: int | None = None


class AgentReviewRequest(BaseModel):
    note: str | None = Field(
        default=None,
        max_length=2000,
    )


class AgentRunResponse(BaseModel):
    run_id: str
    ticket_id: int
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


class AgentExecutionResponse(BaseModel):
    run_id: str
    ticket_id: int
    status: str
    action: str
    external_ticket_id: str | None
    executed: bool
    duplicate: bool = False
    message: str


class AgentExecutionQueuedResponse(BaseModel):
    run_id: str
    job_id: int
    status: str
    duplicate: bool
