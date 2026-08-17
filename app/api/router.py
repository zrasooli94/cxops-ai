from fastapi import APIRouter

from app.api.routes.automation_rules import router as automation_rules_router
from app.api.routes.customers import router as customers_router
from app.api.routes.health import router as health_router
from app.api.routes.organizations import router as organizations_router
from app.api.routes.tickets import router as tickets_router
from app.api.routes.webhooks import router as webhooks_router
from app.api.routes.zendesk_webhooks import router as zendesk_webhooks_router
from app.api.routes.zendesk_auth import router as zendesk_auth_router
from app.api.routes.zendesk import router as zendesk_router
from app.api.routes.knowledge import router as knowledge_router
from app.api.routes.observability import router as observability_router

api_router = APIRouter()

api_router.include_router(health_router)
api_router.include_router(organizations_router)
api_router.include_router(customers_router)
api_router.include_router(tickets_router)
api_router.include_router(webhooks_router)
api_router.include_router(automation_rules_router)
api_router.include_router(zendesk_webhooks_router)
api_router.include_router(zendesk_router)
api_router.include_router(zendesk_auth_router)
api_router.include_router(knowledge_router)
api_router.include_router(observability_router)


