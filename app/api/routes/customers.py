from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentPrincipal, CurrentTenant
from app.core.database import get_db
from app.schemas.customer import CustomerCreate, CustomerRead, CustomerUpdate
from app.services.customer_service import CustomerService

router = APIRouter(
    prefix="/customers",
    tags=["Customers"],
)

DatabaseSession = Annotated[
    AsyncSession,
    Depends(get_db),
]


@router.post(
    "",
    response_model=CustomerRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_customer(
    data: CustomerCreate,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
):
    # organization_id comes from CurrentTenant, never from client payload
    try:
        return await CustomerService.create_for_tenant(db, data, tenant.organization_id)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Customer resource conflict.",
        )


@router.get(
    "",
    response_model=list[CustomerRead],
)
async def list_customers(
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
):
    return await CustomerService.list_for_tenant(db, tenant.organization_id)


@router.get(
    "/{customer_id}",
    response_model=CustomerRead,
)
async def get_customer(
    customer_id: int,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
):
    customer = await CustomerService.get_for_tenant(
        db, customer_id, tenant.organization_id
    )

    if customer is None:
        raise HTTPException(
            status_code=404,
            detail="Customer not found",
        )

    return customer


@router.patch(
    "/{customer_id}",
    response_model=CustomerRead,
)
async def update_customer(
    customer_id: int,
    data: CustomerUpdate,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
):
    customer = await CustomerService.get_for_tenant(
        db, customer_id, tenant.organization_id
    )

    if customer is None:
        raise HTTPException(
            status_code=404,
            detail="Customer not found",
        )

    changes = data.model_dump(exclude_unset=True)

    if "email" in changes and changes["email"] is not None:
        changes["email"] = str(changes["email"])

    for field, value in changes.items():
        setattr(customer, field, value)

    await db.commit()
    await db.refresh(customer)

    return customer
