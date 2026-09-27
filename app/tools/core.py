"""Core business tools (Phase 1P.2).

Provider-less tools available to every organization. ``customer.send_reply``
publishes a human-approved reply to the customer in the local conversation;
it is the Phase 1P.2 carve-out that turns a freed agent draft into a durable,
customer-visible outbound message. The executor only validates and returns a
normalized result — the durable mirroring into the conversation is generic
(see ``BusinessActionService``), so nothing in this module knows about
widgets, channels, or delivery.
"""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.base import (
    ACTION_STATUS_COMPLETED,
    RISK_HIGH,
    BusinessToolContext,
    BusinessToolExecutor,
    ToolExecutionResult,
)

REPLY_BODY_MAX_LENGTH = 10000


class CustomerReplyPayload(BaseModel):
    """An approved reply text destined for the customer."""

    body: str = Field(min_length=1, max_length=REPLY_BODY_MAX_LENGTH)


class PublishCustomerReplyExecutor(BusinessToolExecutor):
    async def execute(
        self,
        db: AsyncSession,
        *,
        context: BusinessToolContext,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        payload = CustomerReplyPayload.model_validate(arguments)
        body = payload.body.strip()

        scope = context.run_id or f"ticket:{context.ticket_id}"
        digest = hashlib.sha256(
            f"{context.organization_id}:{scope}:customer.send_reply".encode()
        ).hexdigest()[:8]

        return ToolExecutionResult(
            status=ACTION_STATUS_COMPLETED,
            summary="Approved customer reply accepted for publication.",
            # The approved reply body is the durable outbound message: this
            # field is mirrored into the local conversation as an outbound,
            # customer-visible message by the business action service.
            customer_visible=True,
            customer_message=body,
            reference_id=f"CX-{digest.upper()}",
            metadata={},
        )


publish_customer_reply_executor = PublishCustomerReplyExecutor()


def _register_core_tools() -> None:
    from app.services.tool_authorization_service import ToolAuthorizationService
    from app.tools.base import BusinessToolDefinition
    from app.tools.registry import business_tool_registry

    definition = BusinessToolDefinition(
        name="customer.send_reply",
        provider=None,
        description=(
            "Publish a human-approved reply to the customer in the current "
            "chat conversation."
        ),
        risk_level=RISK_HIGH,
        requires_approval=True,
        required_capability="ticket.write",
        argument_model=CustomerReplyPayload,
        executor=publish_customer_reply_executor,
    )
    business_tool_registry.register(definition)

    ToolAuthorizationService.register_policy(
        "customer.send_reply",
        {
            "risk_level": RISK_HIGH,
            "requires_approval": True,
            "auto_authorize": False,
            "required_capability": "ticket.write",
            "argument_schema": definition.argument_schema,
            "business_tool": True,
            "provider": None,
            "description": definition.description,
        },
    )


_register_core_tools()