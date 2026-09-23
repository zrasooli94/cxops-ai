from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentPrincipal, CurrentTenant, RequireCapability
from app.core.database import get_db
from app.core.rbac import AuthorizationContext, Capability
from app.repositories.automation_rule_repository import (
    AutomationRuleRepository,
)
from app.schemas.automation_rule import (
    AutomationRuleCreate,
    AutomationRuleRead,
    AutomationRuleUpdate,
)
from app.services.automation_rule_service import (
    AutomationRuleService,
    InvalidAutomationRuleError,
)

router = APIRouter(
    prefix="/automation-rules",
    tags=["Automation Rules"],
)


DatabaseSession = Annotated[
    AsyncSession,
    Depends(get_db),
]

AutomationManageAuthz = Annotated[
    AuthorizationContext,
    Depends(RequireCapability(Capability.AUTOMATION_MANAGE)),
]
AutomationReadAuthz = Annotated[
    AuthorizationContext,
    Depends(RequireCapability(Capability.AUTOMATION_READ)),
]


@router.post(
    "",
    response_model=AutomationRuleRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_rule(
    data: AutomationRuleCreate,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: AutomationManageAuthz,
):
    # organization_id comes from CurrentTenant, never from client payload
    try:
        return await AutomationRuleService.create_for_tenant(
            db=db,
            data=data,
            organization_id=tenant.organization_id,
        )
    except InvalidAutomationRuleError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@router.get(
    "",
    response_model=list[AutomationRuleRead],
)
async def list_rules(
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: AutomationReadAuthz,
):
    return await AutomationRuleRepository.list_for_tenant(
        db=db,
        organization_id=tenant.organization_id,
    )


@router.get(
    "/{rule_id}",
    response_model=AutomationRuleRead,
)
async def get_rule(
    rule_id: int,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: AutomationReadAuthz,
):
    rule = await AutomationRuleRepository.get_by_id_for_tenant(
        db=db,
        rule_id=rule_id,
        organization_id=tenant.organization_id,
    )

    if rule is None:
        raise HTTPException(
            status_code=404,
            detail="Automation rule not found",
        )

    return rule


@router.patch(
    "/{rule_id}",
    response_model=AutomationRuleRead,
)
async def update_rule(
    rule_id: int,
    data: AutomationRuleUpdate,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: AutomationManageAuthz,
):
    try:
        rule = await AutomationRuleService.update_for_tenant(
            db=db,
            rule_id=rule_id,
            data=data,
            organization_id=tenant.organization_id,
        )
    except InvalidAutomationRuleError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    if rule is None:
        raise HTTPException(
            status_code=404,
            detail="Automation rule not found",
        )

    return rule
