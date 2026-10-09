"""Staff-facing public-chat handoff schemas (Phase 1P.2)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

PublicChatWindow = Literal["24h", "7d"]


class StaffHandoffSessionSummary(BaseModel):
    session_id: int
    conversation_id: int
    ticket_id: int
    customer_id: int | None
    status: str
    assigned_to_subject: str | None
    assigned_at: datetime | None
    released_at: datetime | None
    expires_at: datetime
    created_at: datetime
    updated_at: datetime


class StaffHandoffSessionsResponse(BaseModel):
    sessions: list[StaffHandoffSessionSummary]


class StaffBusinessActionSummary(BaseModel):
    id: int
    request_type: str
    status: str
    reference_id: str | None
    summary: str | None
    created_at: datetime
    updated_at: datetime


class StaffBusinessActionsResponse(BaseModel):
    actions: list[StaffBusinessActionSummary]


# ---------------------------------------------------------------------------
# Live-pilot operations summary (Phase 1P.7)
#
# The response is deliberately free of customer message text, session tokens,
# widget keys, IPs, prompts, and any PII: every field is a bounded count,
# timestamp, rate, or cost.
# ---------------------------------------------------------------------------


class PublicChatConfigSummary(BaseModel):
    widget_enabled: bool
    grounded_auto_reply_enabled: bool
    theme_token: str | None
    allowed_origin_count: int


class PublicChatQueueSummary(BaseModel):
    human_requested: int
    human_assigned: int
    ai_active: int
    active_total: int


class PublicChatQueueHealth(BaseModel):
    # Operator attention signal only; deliberately not an SLA.
    health: Literal["normal", "attention"]
    oldest_waiting_since: datetime | None
    oldest_waiting_minutes: int | None


class PublicChatWindowSummary(BaseModel):
    sessions_created: int
    customer_messages: int
    sessions_closed: int
    grounded_public_auto_replies: int


class PublicChatRagSummary(BaseModel):
    rag_requests: int
    rag_grounded: int
    rag_errors: int
    avg_rag_latency_ms: float
    avg_best_similarity: float | None
    estimated_ai_cost_usd: float


class PublicChatSafetySummary(BaseModel):
    public_chat_integration_jobs: int
    autonomous_public_chat_executions: int


class PublicChatPilotSummaryResponse(BaseModel):
    tenant_id: int
    window: PublicChatWindow
    generated_at: datetime
    config: PublicChatConfigSummary
    queue: PublicChatQueueSummary
    queue_health: PublicChatQueueHealth
    window_summary: PublicChatWindowSummary
    rag: PublicChatRagSummary
    safety: PublicChatSafetySummary