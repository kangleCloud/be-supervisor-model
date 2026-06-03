"""FastAPI 应用入口。"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError

from app.api.auth import router as auth_router
from app.api.supervisor import router as supervisor_router
from app.core.config import get_settings
from app.core.database import initialize_database
from app.core.exceptions import AppError
from app.core.logging import configure_logging
from app.core.response import fail


LOGGER = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """创建并装配应用。"""
    settings = get_settings()
    configure_logging(settings.app.log_level)
    initialize_database(settings)

    app = FastAPI(
        title="be-supervisor-model",
        description="面向运维场景的 Supervisor 配置、登录鉴权与进程管理服务。",
        version="0.1.0",
    )
    app.include_router(auth_router)
    app.include_router(supervisor_router)

    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError):
        return fail(exc.http_status, exc.code, exc.msg, exc.data)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError):
        LOGGER.debug("request validation failed: %s", exc)
        return fail(400, 40000, "请求参数非法")

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_: Request, exc: Exception):
        LOGGER.exception("unexpected error", exc_info=exc)
        return fail(500, 50000, "服务内部错误")

    return app


app = create_app()
