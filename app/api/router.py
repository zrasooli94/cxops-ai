from fastapi import APIRouter

from app.api.routes.automation_rules import (
    router as automation_rules_router,
)
from app.api.routes.customers import router as customers_router
from app.api.routes.health import router as health_router
from app.api.routes.organizations import router as organizations_router
from app.api.routes.tickets import router as tickets_router
from app.api.routes.webhooks import router as webhooks_router


api_router = APIRouter()

api_router.include_router(health_router)
api_router.include_router(organizations_router)
api_router.include_router(customers_router)
api_router.include_router(tickets_router)
api_router.include_router(webhooks_router)
api_router.include_router(automation_rules_router)