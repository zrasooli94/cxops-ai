"""Phase 1Q commercial onboarding API.

Five surfaces, each with its own trust boundary:

* ``/public/leads``      - unauthenticated; feature-flagged (default off),
                           bounded and rate-limited.
* ``/platform-admin/*``  - platform operators only (``platform_operators``
                           table by verified ``sub``); never tenant RBAC.
* ``/onboarding/*``      - the caller's resolved tenant; ``organization.*``
                           capabilities. No organization id in any body.
* ``/team/*``            - the caller's resolved tenant; ``member.*``
                           capabilities plus role-rank rules in the service.
* ``/invitations/*``     - an authenticated principal with no tenant yet (an
                           invitee is not a member until acceptance).
* ``/workspace/overview``- the caller's resolved tenant; sections gated by the
                           caller's effective capabilities.
"""

from __future__ import annotations

from typing import Annotated, Any, NoReturn

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentAuthorization,
    CurrentPrincipal,
    PlatformOperator,
    RequireCapability,
)
from app.core.database import get_db
from app.core.rbac import AuthorizationContext, Capability
from app.schemas.commercial import (
    LEAD_INDUSTRIES,
    BusinessUpdate,
    EntitlementUpdate,
    InvitationCreate,
    InvitationTokenRequest,
    LeadProvisionRequest,
    LeadSubmission,
    LeadUpdate,
    LifecycleActionRequest,
    MemberRoleUpdate,
    WidgetCreateRequest,
    WidgetSetupUpdate,
)
from app.services.identity_verification import (
    IdentityVerificationError,
    IdentityVerifier,
    build_identity_verifier,
)
from app.services.lead_service import LeadError, LeadService
from app.services.onboarding_service import OnboardingError, OnboardingService
from app.services.platform_admin_service import PlatformAdminError, PlatformAdminService
from app.services.team_service import TeamError, TeamService, assignable_roles
from app.services.workspace_service import WorkspaceService

DatabaseSession = Annotated[AsyncSession, Depends(get_db)]

OrganizationReadAuthz = Annotated[
    AuthorizationContext, Depends(RequireCapability(Capability.ORGANIZATION_READ))
]
OrganizationManageAuthz = Annotated[
    AuthorizationContext, Depends(RequireCapability(Capability.ORGANIZATION_MANAGE))
]
MemberReadAuthz = Annotated[
    AuthorizationContext, Depends(RequireCapability(Capability.MEMBER_READ))
]
MemberManageAuthz = Annotated[
    AuthorizationContext, Depends(RequireCapability(Capability.MEMBER_MANAGE))
]

def _fail(exc: Exception) -> NoReturn:
    """Map a service error to HTTP. Messages are authored for end users and
    carry no identifiers beyond what the caller supplied."""
    code = getattr(exc, "status_code", status.HTTP_400_BAD_REQUEST)
    raise HTTPException(status_code=code, detail=str(exc)) from exc


# ----------------------------------------------------------------------
# Public lead capture
# ----------------------------------------------------------------------

public_router = APIRouter(prefix="/public/leads", tags=["Public Leads"])


@public_router.get("/availability")
async def lead_availability() -> dict[str, Any]:
    """Whether early-access requests are being accepted, and the form options."""
    return {**LeadService.availability(), "industries": list(LEAD_INDUSTRIES)}


@public_router.post("", status_code=status.HTTP_202_ACCEPTED)
async def submit_lead(payload: LeadSubmission, db: DatabaseSession) -> dict[str, Any]:
    try:
        return await LeadService.submit(
            db,
            company_name=payload.company_name,
            contact_name=payload.contact_name,
            email=str(payload.email),
            website=payload.website,
            industry=payload.industry,
            inquiry=payload.inquiry,
            honeypot=payload.company_fax,
        )
    except LeadError as exc:
        _fail(exc)


# ----------------------------------------------------------------------
# Platform operators
# ----------------------------------------------------------------------

platform_router = APIRouter(prefix="/platform-admin", tags=["Platform Administration"])


@platform_router.get("/me")
async def platform_me(operator: PlatformOperator) -> dict[str, Any]:
    return {"platform_operator": True}


@platform_router.get("/leads")
async def list_leads(
    db: DatabaseSession,
    operator: PlatformOperator,
    status_filter: Annotated[str | None, Query(alias="status", max_length=20)] = None,
) -> dict[str, Any]:
    try:
        return await LeadService.list_leads(db, status=status_filter)
    except LeadError as exc:
        _fail(exc)


@platform_router.get("/leads/{lead_id}")
async def get_lead(lead_id: int, db: DatabaseSession, operator: PlatformOperator) -> dict[str, Any]:
    try:
        return await LeadService.get_lead(db, lead_id)
    except LeadError as exc:
        _fail(exc)


@platform_router.patch("/leads/{lead_id}")
async def update_lead(
    lead_id: int, payload: LeadUpdate, db: DatabaseSession, operator: PlatformOperator
) -> dict[str, Any]:
    try:
        return await LeadService.update_lead(
            db,
            lead_id=lead_id,
            operator_subject=operator.subject,
            status=payload.status,
            operator_notes=payload.operator_notes,
            notes_provided="operator_notes" in payload.model_fields_set,
        )
    except LeadError as exc:
        _fail(exc)


@platform_router.post("/leads/{lead_id}/provision", status_code=status.HTTP_201_CREATED)
async def provision_lead(
    lead_id: int,
    payload: LeadProvisionRequest,
    db: DatabaseSession,
    operator: PlatformOperator,
) -> dict[str, Any]:
    try:
        return await LeadService.provision(
            db,
            lead_id=lead_id,
            operator_subject=operator.subject,
            organization_name=payload.organization_name,
        )
    except LeadError as exc:
        _fail(exc)


@platform_router.get("/organizations")
async def list_organizations(db: DatabaseSession, operator: PlatformOperator) -> dict[str, Any]:
    return await PlatformAdminService.list_organizations(db)


@platform_router.post("/organizations/{organization_id}/lifecycle")
async def change_lifecycle(
    organization_id: int,
    payload: LifecycleActionRequest,
    db: DatabaseSession,
    operator: PlatformOperator,
) -> dict[str, Any]:
    try:
        return await PlatformAdminService.change_lifecycle(
            db,
            organization_id=organization_id,
            action=payload.action,
            operator_subject=operator.subject,
        )
    except PlatformAdminError as exc:
        _fail(exc)


@platform_router.patch("/organizations/{organization_id}/entitlements")
async def set_entitlements(
    organization_id: int,
    payload: EntitlementUpdate,
    db: DatabaseSession,
    operator: PlatformOperator,
) -> dict[str, Any]:
    changes = {field: getattr(payload, field) for field in payload.model_fields_set}
    try:
        return await PlatformAdminService.set_entitlements(
            db,
            organization_id=organization_id,
            operator_subject=operator.subject,
            changes=changes,
        )
    except PlatformAdminError as exc:
        _fail(exc)


@platform_router.get("/usage")
async def platform_usage(db: DatabaseSession, operator: PlatformOperator) -> dict[str, Any]:
    return await PlatformAdminService.platform_usage(db)


# ----------------------------------------------------------------------
# Tenant setup wizard
# ----------------------------------------------------------------------

onboarding_router = APIRouter(prefix="/onboarding", tags=["Onboarding"])


@onboarding_router.get("/state")
async def onboarding_state(db: DatabaseSession, authz: OrganizationReadAuthz) -> dict[str, Any]:
    state = await OnboardingService.state(db, organization_id=authz.organization_id)
    state["can_edit"] = authz.has(Capability.ORGANIZATION_MANAGE)
    return state


@onboarding_router.patch("/business")
async def update_business(
    payload: BusinessUpdate, db: DatabaseSession, authz: OrganizationManageAuthz
) -> dict[str, Any]:
    changes = {field: getattr(payload, field) for field in payload.model_fields_set}
    try:
        await OnboardingService.update_business(
            db,
            organization_id=authz.organization_id,
            actor_subject=authz.subject,
            changes=changes,
        )
    except OnboardingError as exc:
        _fail(exc)
    return await OnboardingService.state(db, organization_id=authz.organization_id)


@onboarding_router.post("/widget", status_code=status.HTTP_201_CREATED)
async def create_widget(
    payload: WidgetCreateRequest, db: DatabaseSession, authz: OrganizationManageAuthz
) -> dict[str, Any]:
    try:
        return await OnboardingService.create_widget(
            db,
            organization_id=authz.organization_id,
            actor_subject=authz.subject,
            display_name=payload.display_name,
            welcome_message=payload.welcome_message,
            theme_token=payload.theme_token,
            allowed_origins=payload.allowed_origins,
        )
    except OnboardingError as exc:
        _fail(exc)


@onboarding_router.patch("/widget")
async def update_widget(
    payload: WidgetSetupUpdate, db: DatabaseSession, authz: OrganizationManageAuthz
) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for field in payload.model_fields_set:
        value = getattr(payload, field)
        if value is None and field != "theme_token":
            continue
        changes[field] = value
    try:
        await OnboardingService.update_widget(
            db,
            organization_id=authz.organization_id,
            actor_subject=authz.subject,
            changes=changes,
        )
    except OnboardingError as exc:
        _fail(exc)
    return await OnboardingService.state(db, organization_id=authz.organization_id)


@onboarding_router.post("/submit")
async def submit_for_review(
    db: DatabaseSession, authz: OrganizationManageAuthz
) -> dict[str, Any]:
    try:
        return await OnboardingService.submit_for_review(
            db, organization_id=authz.organization_id, actor_subject=authz.subject
        )
    except OnboardingError as exc:
        _fail(exc)


# ----------------------------------------------------------------------
# Team management
# ----------------------------------------------------------------------

team_router = APIRouter(prefix="/team", tags=["Team"])


@team_router.get("/members")
async def list_members(db: DatabaseSession, authz: MemberReadAuthz) -> dict[str, Any]:
    members = await TeamService.list_members(db, authz=authz)
    can_manage = authz.has(Capability.MEMBER_MANAGE)
    return {
        "members": members,
        "can_manage": can_manage,
        "assignable_roles": assignable_roles(authz.role) if can_manage else [],
        "your_role": authz.role.value,
    }


@team_router.patch("/members/{membership_id}")
async def change_member_role(
    membership_id: int,
    payload: MemberRoleUpdate,
    db: DatabaseSession,
    authz: MemberManageAuthz,
) -> dict[str, Any]:
    try:
        return await TeamService.change_role(
            db, authz=authz, membership_id=membership_id, new_role=payload.role
        )
    except TeamError as exc:
        _fail(exc)


@team_router.delete("/members/{membership_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    membership_id: int, db: DatabaseSession, authz: CurrentAuthorization
) -> None:
    # Any member may leave; removing someone else needs member.manage (checked
    # in the service together with the role-rank rules).
    try:
        await TeamService.remove_member(db, authz=authz, membership_id=membership_id)
    except TeamError as exc:
        _fail(exc)


@team_router.get("/invitations")
async def list_invitations(db: DatabaseSession, authz: MemberManageAuthz) -> dict[str, Any]:
    return {
        "invitations": await TeamService.list_invitations(
            db, organization_id=authz.organization_id
        )
    }


@team_router.post("/invitations", status_code=status.HTTP_201_CREATED)
async def create_invitation(
    payload: InvitationCreate, db: DatabaseSession, authz: MemberManageAuthz
) -> dict[str, Any]:
    try:
        invitation = await TeamService.invite(
            db, authz=authz, email=str(payload.email), role=payload.role
        )
    except TeamError as exc:
        _fail(exc)
    invitation["delivery"] = (
        "Email delivery is not configured. Share the invitation link with the "
        "person directly; it is shown only once."
    )
    return invitation


@team_router.post("/invitations/{invitation_id}/revoke")
async def revoke_invitation(
    invitation_id: int, db: DatabaseSession, authz: MemberManageAuthz
) -> dict[str, Any]:
    try:
        return await TeamService.revoke_invitation(db, authz=authz, invitation_id=invitation_id)
    except TeamError as exc:
        _fail(exc)


invitation_router = APIRouter(prefix="/invitations", tags=["Invitations"])


@invitation_router.post("/preview")
async def preview_invitation(
    payload: InvitationTokenRequest, db: DatabaseSession, principal: CurrentPrincipal
) -> dict[str, Any]:
    try:
        return await TeamService.preview(db, token=payload.token)
    except TeamError as exc:
        _fail(exc)


def get_identity_verifier() -> IdentityVerifier:
    """Overridable in tests; production behaviour comes from settings."""
    return build_identity_verifier()


IdentityVerifierDep = Annotated[IdentityVerifier, Depends(get_identity_verifier)]


@invitation_router.post("/accept")
async def accept_invitation(
    payload: InvitationTokenRequest,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    verifier: IdentityVerifierDep,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    access_token = (authorization or "").partition(" ")[2].strip()
    try:
        identity = await verifier.verify(principal, access_token)
    except IdentityVerificationError as exc:
        _fail(exc)
    try:
        return await TeamService.accept(db, token=payload.token, identity=identity)
    except TeamError as exc:
        _fail(exc)


# ----------------------------------------------------------------------
# Business-owner workspace
# ----------------------------------------------------------------------

workspace_router = APIRouter(prefix="/workspace", tags=["Workspace"])


@workspace_router.get("/overview")
async def workspace_overview(db: DatabaseSession, authz: CurrentAuthorization) -> dict[str, Any]:
    return await WorkspaceService.overview(db, authz=authz)


routers = (
    public_router,
    platform_router,
    onboarding_router,
    team_router,
    invitation_router,
    workspace_router,
)

__all__ = ["routers"]
