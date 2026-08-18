from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.knowledge import RAGSource


class AgentDecision(BaseModel):
    action: Literal[
        "respond",
        "route",
        "escalate",
        "human_review",
        "no_action",
    ]

    reason: str

    recommended_team: str | None = None

    recommended_priority: Literal[
        "low",
        "normal",
        "high",
        "urgent",
    ] | None = None

    response_draft: str | None = None

    requires_human_approval: bool = True


class AgentAnalysisResponse(BaseModel):
    run_id: str

    ticket_id: int

    decision: AgentDecision

    sources: list[RAGSource] = Field(
        default_factory=list
    )

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
    recommended_team: str | None
    recommended_priority: str | None
    response_draft: str | None
    requires_human_approval: bool
    reviewer_note: str | None

class AgentExecutionResponse(BaseModel):
    run_id: str
    ticket_id: int
    status: str
    action: str
    external_ticket_id: str | None
    executed: bool
    duplicate: bool = False
    message: str