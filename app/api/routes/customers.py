from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentPrincipal
from app.core.database import get_db
from app.repositories.customer_repository import CustomerRepository
from app.schemas.customer import CustomerCreate, CustomerRead
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
):
    return await CustomerService.create(db, data)


@router.get(
    "",
    response_model=list[CustomerRead],
)
async def list_customers(
    db: DatabaseSession,
    principal: CurrentPrincipal,
):
    return await CustomerRepository.list(db)


@router.get(
    "/{customer_id}",
    response_model=CustomerRead,
)
async def get_customer(
    customer_id: int,
    db: DatabaseSession,
    principal: CurrentPrincipal,
):
    customer = await CustomerRepository.get_by_id(
        db,
        customer_id,
    )

    if customer is None:
        raise HTTPException(
            status_code=404,
            detail="Customer not found",
        )

    return customer
