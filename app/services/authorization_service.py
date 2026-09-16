"""Authorization resolution service.

Resolves an ``AuthorizationContext`` from a trusted ``AuthenticatedPrincipal``
and a trusted ``TenantContext``. The database membership row is the only
source of truth for the role; no request payload, header, cookie, or JWT claim
can influence the result.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.principal import AuthenticatedPrincipal
from app.core.rbac import (
    AuthorizationContext,
    OrganizationRole,
)
from app.core.tenant import TenantContext
from app.repositories.organization_membership_repository import (
    OrganizationMembershipRepository,
)

log = get_logger(__name__)


class AuthorizationResolutionError(Exception):
    """Base error for authorization resolution failures."""


class AuthorizationMembershipMissingError(AuthorizationResolutionError):
    """Subject is no longer a member of the resolved organization."""


class AuthorizationRoleInvalidError(AuthorizationResolutionError):
    """Membership row contains an unrecognized role value."""


async def resolve_authorization_context(
    db: AsyncSession,
    principal: AuthenticatedPrincipal,
    tenant: TenantContext,
) -> AuthorizationContext:
    """Resolve the authoritative role for a principal inside a tenant.

    Flow:
        TenantContext.organization_id + AuthenticatedPrincipal.subject
        → organization_memberships
        → role
        → AuthorizationContext

    Fail-closed conditions:
    - No matching membership row → ``AuthorizationMembershipMissingError``
    - Persisted role is not a valid ``OrganizationRole`` →
      ``AuthorizationRoleInvalidError``

    The membership lookup re-correlates subject and organization_id even though
    ``TenantContext`` was already validated. This protects against race
    conditions where a membership is revoked between tenant resolution and
    authorization resolution.
    """
    membership = (
        await OrganizationMembershipRepository.get_for_subject_and_organization(
            db,
            principal.subject,
            tenant.organization_id,
        )
    )

    if membership is None:
        log.warning(
            "authorization_membership_missing",
            subject=principal.subject,
            organization_id=tenant.organization_id,
        )
        raise AuthorizationMembershipMissingError(
            "Organization membership not found"
        )

    try:
        role = OrganizationRole(membership.role)
    except (ValueError, AttributeError) as exc:
        log.error(
            "authorization_invalid_role",
            subject=principal.subject,
            organization_id=tenant.organization_id,
            role=getattr(membership, "role", None),
        )
        raise AuthorizationRoleInvalidError(
            f"Invalid organization role: {membership.role}"
        ) from exc

    log.info(
        "authorization_resolved",
        subject=principal.subject,
        organization_id=tenant.organization_id,
        role=role.value,
    )

    return AuthorizationContext(
        organization_id=tenant.organization_id,
        subject=principal.subject,
        role=role,
    )
