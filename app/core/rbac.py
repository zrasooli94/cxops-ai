"""Centralized Role-Based Access Control (RBAC) policy for CXOps.

This module is the single source of truth for:

- Organization membership roles
- Capability definitions
- The role → capability mapping
- Capability-checking helpers

No business service should compare raw role strings. Services should depend on
``AuthorizationContext`` and use ``has_capability`` / ``require_capability``.
"""

from dataclasses import dataclass
from enum import Enum


class OrganizationRole(str, Enum):
    """Stable, organization-scoped membership roles.

    These are persisted in the database as lowercase strings. They are
    deliberately separate from any Nhost/Hasura roles or JWT claims.
    """

    OWNER = "owner"
    ADMIN = "admin"
    SUPERVISOR = "supervisor"
    AGENT = "agent"
    VIEWER = "viewer"

    @classmethod
    def values(cls) -> set[str]:
        return {member.value for member in cls}


class Capability(str, Enum):
    """Fine-grained, deterministic capabilities in CXOps.

    Capabilities are intentionally coarse enough to stay maintainable but
    fine-grained enough to distinguish read vs. write vs. administrative
    operations. Add new capabilities here; do not scatter string literals.
    """

    # Organization and membership lifecycle
    ORGANIZATION_READ = "organization.read"
    ORGANIZATION_MANAGE = "organization.manage"
    MEMBER_READ = "member.read"
    MEMBER_MANAGE = "member.manage"

    # Customer data
    CUSTOMER_READ = "customer.read"
    CUSTOMER_WRITE = "customer.write"

    # Tickets
    TICKET_READ = "ticket.read"
    TICKET_WRITE = "ticket.write"

    # Agent execution
    AGENT_RUN = "agent.run"
    AGENT_APPROVE = "agent.approve"
    AGENT_EXECUTE = "agent.execute"

    # Knowledge base
    KNOWLEDGE_READ = "knowledge.read"
    KNOWLEDGE_MANAGE = "knowledge.manage"

    # Automation rules
    AUTOMATION_READ = "automation.read"
    AUTOMATION_MANAGE = "automation.manage"

    # Integrations
    INTEGRATION_READ = "integration.read"
    INTEGRATION_MANAGE = "integration.manage"

    # Observability
    OBSERVABILITY_READ = "observability.read"

    # AI evaluations
    EVALUATION_READ = "evaluation.read"
    EVALUATION_MANAGE = "evaluation.manage"

    # Service transformation decision support
    TRANSFORMATION_SIMULATION_READ = "transformation.simulation.read"
    TRANSFORMATION_SIMULATION_MANAGE = "transformation.simulation.manage"

    # Service transformation experiments (Phase 1O)
    TRANSFORMATION_EXPERIMENT_READ = "transformation.experiment.read"
    TRANSFORMATION_EXPERIMENT_MANAGE = "transformation.experiment.manage"


ALL_CAPABILITIES: frozenset[Capability] = frozenset(Capability)

READ_ONLY_CAPABILITIES: frozenset[Capability] = frozenset({
    Capability.CUSTOMER_READ,
    Capability.TICKET_READ,
    Capability.KNOWLEDGE_READ,
    Capability.AUTOMATION_READ,
    Capability.OBSERVABILITY_READ,
})

OPERATIONAL_CAPABILITIES: frozenset[Capability] = frozenset({
    Capability.CUSTOMER_READ,
    Capability.CUSTOMER_WRITE,
    Capability.TICKET_READ,
    Capability.TICKET_WRITE,
    Capability.AGENT_RUN,
    Capability.KNOWLEDGE_READ,
    Capability.INTEGRATION_READ,
    Capability.OBSERVABILITY_READ,
})

# Role → capability matrix. This is the only place where role strings are
# translated into capabilities; keep it explicit and fail-closed.
ROLE_CAPABILITIES: dict[OrganizationRole, frozenset[Capability]] = {
    OrganizationRole.OWNER: ALL_CAPABILITIES,
    OrganizationRole.ADMIN: ALL_CAPABILITIES,
    OrganizationRole.SUPERVISOR: frozenset({
        Capability.CUSTOMER_READ,
        Capability.CUSTOMER_WRITE,
        Capability.TICKET_READ,
        Capability.TICKET_WRITE,
        Capability.AGENT_RUN,
        Capability.AGENT_APPROVE,
        Capability.AGENT_EXECUTE,
        Capability.KNOWLEDGE_READ,
        Capability.KNOWLEDGE_MANAGE,
        Capability.AUTOMATION_READ,
        Capability.OBSERVABILITY_READ,
        Capability.INTEGRATION_READ,
        Capability.MEMBER_READ,
        Capability.EVALUATION_READ,
        Capability.EVALUATION_MANAGE,
        Capability.TRANSFORMATION_SIMULATION_READ,
        Capability.TRANSFORMATION_SIMULATION_MANAGE,
        Capability.TRANSFORMATION_EXPERIMENT_READ,
        Capability.TRANSFORMATION_EXPERIMENT_MANAGE,
    }),
    OrganizationRole.AGENT: frozenset({
        Capability.CUSTOMER_READ,
        Capability.TICKET_READ,
        Capability.TICKET_WRITE,
        Capability.AGENT_RUN,
        Capability.KNOWLEDGE_READ,
        Capability.INTEGRATION_READ,
    }),
    OrganizationRole.VIEWER: READ_ONLY_CAPABILITIES,
}


# Phase 1Q: an organization that has not been approved by a platform operator
# keeps only the capabilities needed to finish setup. Everything that can spend
# AI allowance (agent runs, evaluations, knowledge search/ingestion), reach an
# external system (integration management, ticket/conversation writes that
# deliver to Zendesk), or change automation is withheld until approval. The
# role is unchanged - the effective capability set is role ∩ this mask.
SETUP_CAPABILITIES: frozenset[Capability] = frozenset({
    Capability.ORGANIZATION_READ,
    Capability.ORGANIZATION_MANAGE,
    Capability.MEMBER_READ,
    Capability.MEMBER_MANAGE,
    Capability.CUSTOMER_READ,
    Capability.TICKET_READ,
    Capability.INTEGRATION_READ,
    Capability.OBSERVABILITY_READ,
    Capability.AUTOMATION_READ,
})

# Added to the setup mask only when the platform enables knowledge setup during
# onboarding (settings.onboarding_knowledge_setup_enabled); ingestion and
# search call the paid embedding API.
KNOWLEDGE_SETUP_CAPABILITIES: frozenset[Capability] = frozenset({
    Capability.KNOWLEDGE_READ,
    Capability.KNOWLEDGE_MANAGE,
})

ORGANIZATION_ACTIVE = "active"


def lifecycle_capability_mask(
    organization_status: str,
    *,
    knowledge_setup_enabled: bool = False,
) -> frozenset[Capability]:
    """Capabilities an organization in ``organization_status`` may exercise.

    ``active`` is unrestricted. Every other value - including an unknown one -
    gets the setup mask (fail closed); a ``suspended`` organization gets only
    read access to its own organization and team.
    """
    if organization_status == ORGANIZATION_ACTIVE:
        return ALL_CAPABILITIES
    if organization_status == "suspended":
        return frozenset({Capability.ORGANIZATION_READ, Capability.MEMBER_READ})
    if knowledge_setup_enabled:
        return SETUP_CAPABILITIES | KNOWLEDGE_SETUP_CAPABILITIES
    return SETUP_CAPABILITIES


class AuthorizationError(Exception):
    """Base authorization decision error."""


class MissingCapabilityError(AuthorizationError):
    """A required capability was not granted to the authorization context."""


class InvalidRoleError(AuthorizationError):
    """A role value is unknown or not a valid OrganizationRole."""


@dataclass(frozen=True)
class AuthorizationContext:
    """Immutable authorization state for an authenticated subject inside one
    resolved organization.

    Authentication (``AuthenticatedPrincipal``) answers "who are you?".
    Tenancy (``TenantContext``) answers "which organization?".
    AuthorizationContext answers "what role do you hold there?".

    It deliberately does not duplicate JWT claims, session state, or frontend
    selections. The database membership row is the authoritative source of the
    role.
    """

    organization_id: int
    subject: str
    role: OrganizationRole
    # Phase 1Q: the organization's commercial lifecycle status. Defaults to
    # ``active`` so contexts built before Phase 1Q keep their capabilities.
    organization_status: str = ORGANIZATION_ACTIVE
    knowledge_setup_enabled: bool = False

    @property
    def capabilities(self) -> frozenset[Capability]:
        return capabilities_for_role(self.role) & lifecycle_capability_mask(
            self.organization_status,
            knowledge_setup_enabled=self.knowledge_setup_enabled,
        )

    @property
    def role_capabilities(self) -> frozenset[Capability]:
        """What the role grants once the organization is active."""
        return capabilities_for_role(self.role)

    def has(self, capability: Capability) -> bool:
        """Fail-closed capability check."""
        return has_capability(self, capability)

    def require(self, capability: Capability) -> None:
        """Raise ``MissingCapabilityError`` if the capability is absent."""
        require_capability(self, capability)


def capabilities_for_role(role: OrganizationRole) -> frozenset[Capability]:
    """Return the capability set for a known role.

    Unknown roles are denied every capability (fail closed).
    """
    return ROLE_CAPABILITIES.get(role, frozenset())


def has_capability(
    authz: AuthorizationContext,
    capability: Capability,
) -> bool:
    """Fail-closed check whether ``authz`` holds ``capability``.

    Unknown roles, unknown capabilities, and missing authorization are all
    denied.
    """
    if authz is None or capability is None:
        return False
    return capability in authz.capabilities


def require_capability(
    authz: AuthorizationContext,
    capability: Capability,
) -> None:
    """Require a capability or raise ``MissingCapabilityError``.

    Prefer this for imperative guards inside services.
    """
    if not has_capability(authz, capability):
        raise MissingCapabilityError(
            f"Capability {capability.value} required for this operation"
        )
