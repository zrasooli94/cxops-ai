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