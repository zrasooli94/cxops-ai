"""Generic business tool framework (Phase 1P.2).

A ``BusinessToolDefinition`` describes one deterministic, tenant-administered
business action exposed to the agent workflow. Each tool owns its provider
namespace, its argument validation (a Pydantic model shared by the executor and
the policy argument schema), and its executor. Tool execution always happens
through the existing authorization pipeline:

    customer text -> agent decision -> tool plan -> ToolAuthorizationService
    -> human approval -> durable IntegrationJob -> executor -> durable result

Business tools never receive tenant/org/conversation/ticket identifiers in
their arguments: every trusted target is derived server-side from the run's
persisted parents at execution time (see ``app.services.business_action_service``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# Risk classes (mirror ToolAuthorizationService risk levels).
RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"

# Bounded customer-visible result statuses for business actions.
ACTION_STATUS_RECEIVED = "received"
ACTION_STATUS_PROCESSING = "processing"
ACTION_STATUS_NEEDS_REVIEW = "needs_review"
ACTION_STATUS_APPROVED = "approved"
ACTION_STATUS_COMPLETED = "completed"
ACTION_STATUS_FAILED = "failed"

# The set a widget / staff surface may render. Everything else is internal.
CUSTOMER_VISIBLE_ACTION_STATUSES = frozenset(
    {
        ACTION_STATUS_RECEIVED,
        ACTION_STATUS_PROCESSING,
        ACTION_STATUS_NEEDS_REVIEW,
        ACTION_STATUS_APPROVED,
        ACTION_STATUS_COMPLETED,
        ACTION_STATUS_FAILED,
    }
)


class BusinessToolValidationError(Exception):
    """Tool argument validation failed against the tool's schema."""


class BusinessToolExecutionError(Exception):
    """The executor rejected or failed the tool call.

    The message must be bounded and safe to surface to staff; it never contains
    raw payload data.
    """


@dataclass(slots=True)
class BusinessToolContext:
    """Trusted execution context derived from persisted parents.

    Never populated from tool arguments; the service resolves it from the
    agent run's tenant-owned ticket/conversation at execution time.
    """

    organization_id: int
    ticket_id: int
    conversation_id: int | None = None
    customer_id: int | None = None
    run_id: str | None = None


@dataclass(slots=True)
class ToolExecutionResult:
    """Normalized, durable result of a business tool execution.

    ``metadata`` is bounded and serializable; it is persisted as part of the
    business action result and must never contain PII or raw payload echo.
    """

    status: str
    summary: str
    customer_visible: bool = True
    customer_message: str | None = None
    reference_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class BusinessToolExecutor(ABC):
    """Executor contract implemented by each provider adapter."""

    @abstractmethod
    async def execute(
        self,
        db: AsyncSession,
        *,
        context: BusinessToolContext,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Run the tool under a trusted context. Must be idempotent-safe."""


def build_policy_argument_schema(
    model: type[BaseModel],
) -> dict[str, dict[str, Any]]:
    """Derive the ToolAuthorizationService argument-schema from a Pydantic model.

    Keeps the policy digest schema and the runtime validation model in lockstep
    so a field change cannot silently desynchronize them. Values are extracted
    from the model's JSON schema (required set, string type, maxLength), which
    stays stable across pydantic internals.
    """
    model_schema = model.model_json_schema()
    properties = model_schema.get("properties", {})
    required_fields = set(model_schema.get("required", []))

    schema: dict[str, dict[str, Any]] = {}
    for name, prop in properties.items():
        entry: dict[str, Any] = {
            "required": name in required_fields,
        }
        if entry["required"] and prop.get("type") == "string":
            entry["non_empty"] = True
        if isinstance(prop.get("maxLength"), int):
            entry["max_length"] = prop["maxLength"]
        schema[name] = entry
    return schema


@dataclass(slots=True, frozen=True)
class BusinessToolDefinition:
    """One registered, policy-backed business tool.

    ``name`` must be globally unique and namespaced by provider
    (``<provider>.<capability>``, e.g. ``a1.create_vehicle_lead``); it is the
    key into ``ToolAuthorizationService.POLICIES``.
    """

    name: str
    provider: str | None
    description: str
    risk_level: str
    requires_approval: bool
    required_capability: str | None
    argument_model: type[BaseModel]
    executor: BusinessToolExecutor
    argument_schema: dict[str, dict[str, Any]] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "argument_schema",
            build_policy_argument_schema(self.argument_model),
        )

    def validate_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            validated = self.argument_model.model_validate(arguments)
        except Exception as exc:
            raise BusinessToolValidationError(
                f"Invalid arguments for {self.name}: {exc}"
            ) from exc
        return dict(validated)