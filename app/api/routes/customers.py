from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentPrincipal,
    CurrentTenant,
    RequireCapability,
)
from app.core.database import get_db
from app.core.logging import get_logger
from app.core.rbac import AuthorizationContext, Capability
from app.repositories.customer_identity_repository import (
    CustomerIdentityRepository,
)
from app.schemas.customer import (
    CustomerCreate,
    CustomerListResponse,
    CustomerRead,
    CustomerSummary,
    CustomerTicketListResponse,
    CustomerUpdate,
)
from app.schemas.customer_context import CustomerContextResponse
from app.schemas.customer_identity import (
    CustomerIdentityListResponse,
    CustomerIdentityRead,
)
from app.schemas.customer_timeline import CustomerTimelineResponse
from app.services.customer_360_service import Customer360Service
from app.services.customer_context_service import CustomerContextService
from app.services.customer_service import CustomerService
from app.services.customer_timeline_service import CustomerTimelineService

router = APIRouter(
    prefix="/customers",
    tags=["Customers"],
)

log = get_logger(__name__)

DatabaseSession = Annotated[
    AsyncSession,
    Depends(get_db),
]

CustomerWriteAuthz = Annotated[
    AuthorizationContext,
    Depends(RequireCapability(Capability.CUSTOMER_WRITE)),
]
CustomerReadAuthz = Annotated[
    AuthorizationContext,
    Depends(RequireCapability(Capability.CUSTOMER_READ)),
]
# Composite read: customer.read AND ticket.read. Both gates are enforced.
CustomerTicketReadAuthz = Annotated[
    AuthorizationContext,
    Depends(RequireCapability(Capability.TICKET_READ)),
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
    authz: CustomerWriteAuthz,
):
    # organization_id comes from CurrentTenant, never from client payload
    try:
        return await CustomerService.create_for_tenant(db, data, tenant.organization_id)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Customer resource conflict.",
        ) from None


@router.get(
    "",
    response_model=CustomerListResponse,
)
async def list_customers(
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: CustomerReadAuthz,
    search: str | None = Query(default=None, max_length=200),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
):
    items, total = await CustomerService.search_for_tenant(
        db,
        tenant.organization_id,
        search=search,
        offset=offset,
        limit=limit,
    )

    # Structured, PII-free search metric: the search term itself is never logged.
    log.info(
        "customer_search_performed",
        organization_id=tenant.organization_id,
        result_count=len(items),
        total=total,
        offset=offset,
        limit=limit,
        search_present=search is not None and search.strip() != "",
    )

    return CustomerListResponse(
        items=[CustomerRead.model_validate(item) for item in items],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get(
    "/{customer_id}",
    response_model=CustomerRead,
)
async def get_customer(
    customer_id: int,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: CustomerReadAuthz,
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


@router.get(
    "/{customer_id}/summary",
    response_model=CustomerSummary,
)
async def get_customer_summary(
    customer_id: int,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: CustomerReadAuthz,
):
    customer = await Customer360Service.get_for_tenant(
        db, customer_id, tenant.organization_id
    )

    if customer is None:
        raise HTTPException(
            status_code=404,
            detail="Customer not found",
        )

    return await Customer360Service.get_summary(
        db,
        customer_id=customer_id,
        organization_id=tenant.organization_id,
    )


@router.get(
    "/{customer_id}/tickets",
    response_model=CustomerTicketListResponse,
)
async def list_customer_tickets(
    customer_id: int,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: CustomerReadAuthz,
    ticket_authz: CustomerTicketReadAuthz,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
):
    customer = await Customer360Service.get_for_tenant(
        db, customer_id, tenant.organization_id
    )

    if customer is None:
        raise HTTPException(
            status_code=404,
            detail="Customer not found",
        )

    items, total = await Customer360Service.list_tickets_for_customer(
        db,
        customer_id=customer_id,
        organization_id=tenant.organization_id,
        offset=offset,
        limit=limit,
    )

    return CustomerTicketListResponse(
        items=items,
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get(
    "/{customer_id}/timeline",
    response_model=CustomerTimelineResponse,
)
async def get_customer_timeline(
    customer_id: int,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: CustomerReadAuthz,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
):
    customer = await Customer360Service.get_for_tenant(
        db, customer_id, tenant.organization_id
    )

    if customer is None:
        raise HTTPException(
            status_code=404,
            detail="Customer not found",
        )

    return await CustomerTimelineService.build_timeline(
        db,
        customer_id=customer_id,
        organization_id=tenant.organization_id,
        authz=authz,
        offset=offset,
        limit=limit,
    )


@router.get(
    "/{customer_id}/identities",
    response_model=CustomerIdentityListResponse,
)
async def list_customer_identities(
    customer_id: int,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: CustomerReadAuthz,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
):
    customer = await Customer360Service.get_for_tenant(
        db, customer_id, tenant.organization_id
    )

    if customer is None:
        raise HTTPException(
            status_code=404,
            detail="Customer not found",
        )

    items = await CustomerIdentityRepository.list_for_customer_for_tenant(
        db,
        organization_id=tenant.organization_id,
        customer_id=customer_id,
        limit=limit,
    )

    total = await CustomerIdentityRepository.count_for_customer_for_tenant(
        db,
        organization_id=tenant.organization_id,
        customer_id=customer_id,
    )

    return CustomerIdentityListResponse(
        items=[CustomerIdentityRead.model_validate(item) for item in items],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get(
    "/{customer_id}/context",
    response_model=CustomerContextResponse,
)
async def get_customer_context(
    customer_id: int,
    db: DatabaseSession,
    principal: CurrentPrincipal,
    tenant: CurrentTenant,
    authz: CustomerReadAuthz,
):
    customer = await Customer360Service.get_for_tenant(
        db, customer_id, tenant.organization_id
    )

    if customer is None:
        raise HTTPException(
            status_code=404,
            detail="Customer not found",
        )

    context = await CustomerContextService.build_for_customer(
        db,
        organization_id=tenant.organization_id,
        customer=customer,
    )

    return CustomerContextResponse.model_validate(context.model_dump())


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
    authz: CustomerWriteAuthz,
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

    try:
        await db.commit()
    except IntegrityError:
        # Duplicate email or external_id for another customer in this
        # organization is a generic resource conflict — constraint names and
        # internal details are never exposed back to the client.
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Customer resource conflict.",
        ) from None
    await db.refresh(customer)

    return customer