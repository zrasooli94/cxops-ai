"""Business tool registry (Phase 1P.2).

Central registry of all business tools. Definitions register themselves at
import time (``app.tools.registry`` imports the provider adapters), so any
module that imports the registry sees the full catalog. Tool names are unique
and provider-namespaced; the registry is the only authority for resolving a
tool by name into an executor.

Tenant/provider scoping is NOT decided here — the registry describes what
*can* execute; ``BusinessIntegrationService`` decides whether a tool is enabled
for a given organization.
"""

from __future__ import annotations

import threading

from app.tools.base import BusinessToolDefinition, BusinessToolExecutor


class ToolDefinitionConflictError(Exception):
    """Two definitions registered under the same tool name."""


class ToolNotFoundError(Exception):
    """No definition is registered for the requested tool name."""


class BusinessToolRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._definitions: dict[str, BusinessToolDefinition] = {}

    def register(self, definition: BusinessToolDefinition) -> None:
        with self._lock:
            if definition.name in self._definitions:
                raise ToolDefinitionConflictError(
                    f"Tool '{definition.name}' is already registered."
                )
            self._definitions[definition.name] = definition

    def get(self, tool_name: str) -> BusinessToolDefinition | None:
        return self._definitions.get(tool_name)

    def require(self, tool_name: str) -> BusinessToolDefinition:
        definition = self._definitions.get(tool_name)
        if definition is None:
            raise ToolNotFoundError(f"Business tool '{tool_name}' is not registered.")
        return definition

    def names(self) -> set[str]:
        return set(self._definitions)

    def definitions(self) -> list[BusinessToolDefinition]:
        return list(self._definitions.values())

    def list_for_provider(self, provider: str) -> list[BusinessToolDefinition]:
        return sorted(
            (d for d in self._definitions.values() if d.provider == provider),
            key=lambda d: d.name,
        )

    def executor(self, tool_name: str) -> BusinessToolExecutor | None:
        definition = self._definitions.get(tool_name)
        return definition.executor if definition is not None else None


business_tool_registry = BusinessToolRegistry()

# Provider catalog (the only provider codes eligibility accepts). Adding a
# provider here is what makes its namespace real; RISPU is deliberately NOT
# registered in Phase 1P.2, so a RISPU tenant can never be offered A1 tools.
PROVIDER_A1_CASH_FOR_CARS = "a1_cash_for_cars"
SUPPORTED_BUSINESS_PROVIDERS = frozenset({PROVIDER_A1_CASH_FOR_CARS})

# Provider codes reserved but not yet implemented (kept for README/eligibility
# diagnostics so admins see why a requested provider is rejected).
RESERVED_BUSINESS_PROVIDERS = frozenset({"rispu"})


def register_provider_tools() -> None:
    """Import provider adapters so their tools self-register.

    Importing is idempotent (Python module cache); calling this at app load
    guarantees the full catalog is present before any workflow runs.
    """
    # Imported for side effects; noqa because the names are not used here.
    from app.integrations import a1_cash_for_cars  # noqa: F401
    from app.integrations.a1_cash_for_cars import tools  # noqa: F401


# Register on import so ``from app.tools.registry import business_tool_registry``
# is always sufficient even if the caller bypassed ``register_provider_tools``.
register_provider_tools()