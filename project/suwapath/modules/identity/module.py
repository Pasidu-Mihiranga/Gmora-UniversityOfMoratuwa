"""`identity` module registration (ARC-3): the identity port, the token verifier and routes."""

from __future__ import annotations

import logging
import secrets

from suwapath.modules.identity.api.router import build_router
from suwapath.modules.identity.application.service import SqlIdentityService
from suwapath.platform.registry import ModuleContext

logger = logging.getLogger("suwapath.identity")


def register(container: ModuleContext) -> None:
    secret = container.settings.jwt_secret.get_secret_value()
    if not secret:
        if container.settings.suwapath_env == "production":
            raise RuntimeError("JWT_SECRET is required in production (SEC-2)")
        secret = secrets.token_urlsafe(48)  # development: sessions end when the process stops
        logger.warning("JWT_SECRET is empty; using a random secret for this process")
    service = SqlIdentityService(container.uow_factory, secret)
    container.ports["identity"] = service
    container.token_verifier = service.verify_access_token
    settings = container.settings
    demo = settings.demo_password.get_secret_value() if settings.dev_tools_enabled else ""
    container.routers.append(build_router(service, demo or None))
