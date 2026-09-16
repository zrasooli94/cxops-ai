from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_principal
from app.core.config import get_settings
from app.core.database import get_db
from app.core.logging import get_logger
from app.core.principal import AuthenticatedPrincipal
from app.core.rbac import AuthorizationContext, Capability
from app.core.tenant import TenantContext
from app.integrations.webhooks.security import verify_signed_webhook_signature
from app.services.authorization_service import (
    AuthorizationMembershipMissingError,
    AuthorizationRoleInvalidError,
    resolve_authorization_context,
)
from app.services.tenant_service import (
    TenantAccessDeniedError,
    TenantMembershipAmbiguousError,
    TenantMembershipMissingError,
    resolve_tenant_context,
)

log = get_logger(__name__)

WEBHOOK_SIGNATURE_HEADER = "x-cxops-signature"
WEBHOOK_TIMESTAMP_HEADER = "x-cxops-timestamp"
TENANT_ORGANIZATION_ID_HEADER = "x-cxops-organization-id"


async def get_current_principal_dep(
    authorization: Annotated[str | None, Header()] = None,
) -> AuthenticatedPrincipal:
    """FastAPI dependency for the current authenticated principal.

    Delegates to app.core.auth.get_current_principal, which is the single
    authentication boundary. All token validation and HTTP error mapping
    (401 for missing/invalid credentials, 500 for auth misconfiguration)
    lives there.

    Example:
        @router.get("/protected")
        async def protected_endpoint(
            principal: CurrentPrincipal,
        ):
            ...

    Returns:
        AuthenticatedPrincipal: The verified principal from the JWT token.
    """
    return await get_current_principal(authorization=authorization)


CurrentPrincipal = Annotated[
    AuthenticatedPrincipal,
    Depends(get_current_principal_dep),
]


async def get_current_tenant(
    principal: CurrentPrincipal,
    db: Annotated[AsyncSession, Depends(get_db)],
    x_cxops_organization_id: Annotated[str | None, Header()] = None,
) -> TenantContext:
    """FastAPI dependency for the current trusted tenant.

    Resolves the tenant from the authenticated principal's organization
    memberships. The ``X-CXOps-Organization-ID`` header is treated strictly as a
    *selector* (a requested tenant), never as proof of authorization: it is
    always correlated against the principal's ``subject`` in the membership
    table.

    HTTP mapping:
    - no membership at all -> 403
    - multiple memberships without a selector -> 409
    - selector with no matching membership -> 403
    """
    requested_organization_id: int | None = None

    if x_cxops_organization_id:
        try:
            requested_organization_id = int(x_cxops_organization_id)
        except ValueError:
            log.warning(
                "tenant_invalid_selector",
                subject=principal.subject,
                header=TENANT_ORGANIZATION_ID_HEADER,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid organization selection",
            )
        if requested_organization_id <= 0:
            log.warning(
                "tenant_invalid_selector",
                subject=principal.subject,
                header=TENANT_ORGANIZATION_ID_HEADER,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid organization selection",
            )

    try:
        return await resolve_tenant_context(
            db,
            principal,
            requested_organization_id,
        )
    except TenantMembershipMissingError:
        log.warning(
            "tenant_membership_missing",
            subject=principal.subject,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No active organization membership",
        )
    except TenantMembershipAmbiguousError:
        log.warning(
            "tenant_access_denied",
            subject=principal.subject,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Multiple organization memberships configured; "
                "select one via X-CXOps-Organization-ID"
            ),
        )
    except TenantAccessDeniedError:
        log.warning(
            "tenant_access_denied",
            subject=principal.subject,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization membership not found",
        )


CurrentTenant = Annotated[
    TenantContext,
    Depends(get_current_tenant),
]


async def get_current_authorization(
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AuthorizationContext:
    """FastAPI dependency for the current authorization context.

    Resolves the subject's role inside the already-validated tenant from the
    database membership row. Membership revocation or invalid persisted roles
    after tenant resolution are handled fail-closed (403).
    """
    try:
        return await resolve_authorization_context(db, principal, tenant)
    except AuthorizationMembershipMissingError:
        log.warning(
            "authorization_membership_missing",
            subject=principal.subject,
            organization_id=tenant.organization_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization membership not found",
        )
    except AuthorizationRoleInvalidError:
        log.warning(
            "authorization_invalid_role",
            subject=principal.subject,
            organization_id=tenant.organization_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid organization membership role",
        )


CurrentAuthorization = Annotated[
    AuthorizationContext,
    Depends(get_current_authorization),
]


def RequireCapability(capability: Capability | str):
    """Dependency factory that requires a specific capability.

    Usage:
        @router.post("/tickets")
        async def create_ticket(
            authz: CurrentAuthorization = Depends(RequireCapability("ticket.write")),
        ):
            ...
    """
    from app.core.rbac import has_capability

    resolved = capability if isinstance(capability, Capability) else Capability(capability)

    async def _require(
        authz: CurrentAuthorization,
    ) -> AuthorizationContext:
        if not has_capability(authz, resolved):
            log.warning(
                "capability_denied",
                subject=authz.subject,
                organization_id=authz.organization_id,
                role=authz.role.value,
                capability=resolved.value,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return authz

    return _require


async def verify_ticket_event_signature(request: Request) -> bytes:
    """Machine-authentication dependency for the generic ticket-event webhook.

    Rejects the request unless it carries a valid HMAC-SHA256 signature over
    ``timestamp + body`` in the ``X-CXOps-Signature`` header and a parseable
    ``X-CXOps-Timestamp`` within the configured replay window. This is machine
    authentication only — human JWT is neither required nor accepted here.

    Returns the raw request body on success.
    """
    settings = get_settings()
    secret = settings.ticket_event_webhook_secret

    if not secret:
        log.warning("webhook_secret_not_configured", route="ticket-events")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook not configured",
        )

    signature = request.headers.get(WEBHOOK_SIGNATURE_HEADER)
    timestamp = request.headers.get(WEBHOOK_TIMESTAMP_HEADER)

    if not signature or not timestamp:
        log.warning(
            "webhook_signature_missing",
            route="ticket-events",
            has_signature=bool(signature),
            has_timestamp=bool(timestamp),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing webhook signature or timestamp",
        )

    body = await request.body()

    if not verify_signed_webhook_signature(
        secret=secret,
        timestamp=timestamp,
        body=body,
        signature=signature,
        replay_window_seconds=settings.generic_webhook_replay_window_seconds,
    ):
        log.warning("webhook_signature_invalid", route="ticket-events")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )

    return body
