from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
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
)

router = APIRouter(
    prefix="/automation-rules",
    tags=["Automation Rules"],
)


DatabaseSession = Annotated[
    AsyncSession,
    Depends(get_db),
]


@router.post(
    "",
    response_model=AutomationRuleRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_rule(
    data: AutomationRuleCreate,
    db: DatabaseSession,
):
    return await AutomationRuleService.create(
        db=db,
        data=data,
    )


@router.get(
    "",
    response_model=list[AutomationRuleRead],
)
async def list_rules(
    db: DatabaseSession,
):
    return await AutomationRuleRepository.list_all(
        db=db,
    )


@router.get(
    "/{rule_id}",
    response_model=AutomationRuleRead,
)
async def get_rule(
    rule_id: int,
    db: DatabaseSession,
):
    rule = await AutomationRuleRepository.get_by_id(
        db=db,
        rule_id=rule_id,
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
):
    rule = await AutomationRuleService.update(
        db=db,
        rule_id=rule_id,
        data=data,
    )

    if rule is None:
        raise HTTPException(
            status_code=404,
            detail="Automation rule not found",
        )

    return rule
