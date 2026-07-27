from app.api.integration.v1.auth import router as auth_router
from app.api.integration.v1.admin import router as admin_router
from app.api.integration.v1.checks import router as checks_router
from app.api.integration.v1.erp import router as erp_router
from app.api.integration.v1.meta import router as meta_router
from app.api.integration.v1.sed import router as sed_router
from app.api.integration.v1.webhooks import router as webhooks_router

__all__ = [
    "auth_router",
    "admin_router",
    "checks_router",
    "erp_router",
    "meta_router",
    "sed_router",
    "webhooks_router",
]
