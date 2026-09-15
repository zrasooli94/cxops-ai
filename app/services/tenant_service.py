from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.principal import AuthenticatedPrincipal
from app.core.tenant import TenantContext
from app.repositories.organization_membership_repository import (
    OrganizationMembershipRepository,
)

log = get_logger(__name__)


class TenantResolutionError(Exception):
    """Base tenant-resolution error."""


class TenantMembershipMissingError(TenantResolutionError):
    """Principal has no organization memberships."""


class TenantMembershipAmbiguousError(TenantResolutionError):
    """Principal has multiple memberships but no explicit selector was provided."""


class TenantAccessDeniedError(TenantResolutionError):
    """Requested organization is not a membership of the principal."""


async def resolve_tenant_context(
    db: AsyncSession,
    principal: AuthenticatedPrincipal,
    requested_organization_id: int | None = None,
) -> TenantContext:
    """Resolve a trusted tenant for an authenticated principal.

    Strategy (Phase 1C.1):
    - zero memberships -> deny (403 semantics)
    - one membership   -> resolve automatically; a forged selector is still
                         denied because the selector must be validated.
    - multiple         -> require an explicit ``X-CXOps-Organization-ID``
                         selector; absent selector -> 409 (ambiguous).
                         This avoids any "first row" ambiguity and is
                         deterministic (ORDER BY organization_id).

    ``requested_organization_id`` is never trusted on its own — it is always
    correlated against ``principal.subject`` in the membership table.
    """
    subject = principal.subject

    memberships = await OrganizationMembershipRepository.list_for_subject(db, subject)

    if not memberships:
        log.warning(
            "tenant_membership_missing",
            subject=subject,
        )
        raise TenantMembershipMissingError("No organization membership")

    if requested_organization_id is None:
        if len(memberships) > 1:
            log.warning(
                "tenant_membership_ambiguous",
                subject=subject,
                organization_count=len(memberships),
            )
            raise TenantMembershipAmbiguousError(
                "Multiple organization memberships: select one"
            )
        resolved_id = memberships[0].organization_id
        log.info(
            "tenant_resolution_success",
            subject=subject,
            organization_id=resolved_id,
        )
        return TenantContext(
            organization_id=resolved_id,
            subject=subject,
        )

    membership = (
        await OrganizationMembershipRepository.get_for_subject_and_organization(
            db, subject, requested_organization_id
        )
    )
    if membership is None:
        log.warning(
            "tenant_access_denied",
            subject=subject,
            organization_id=requested_organization_id,
        )
        raise TenantAccessDeniedError("Organization membership not found")

    log.info(
        "tenant_resolution_success",
        subject=subject,
        organization_id=requested_organization_id,
    )
    return TenantContext(
        organization_id=requested_organization_id,
        subject=subject,
    )
